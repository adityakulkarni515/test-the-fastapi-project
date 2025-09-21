import os
import base64
import pickle
import tempfile
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse, RedirectResponse
from googleapiclient.discovery import build
from google_auth_oauthlib.flow import Flow
from google.auth.transport.requests import Request as GoogleRequest
from google.cloud import storage, secretmanager
import google.generativeai as genai

# Load environment variables (local testing)
from dotenv import load_dotenv
load_dotenv()

# === CONFIG ===
GENAI_API_KEY = os.getenv("GENAI_API_KEY")
if not GENAI_API_KEY:
    raise ValueError("❌ GENAI_API_KEY not set in env variables")

# Gemini configuration
genai.configure(api_key=GENAI_API_KEY)
model = genai.GenerativeModel("gemini-1.5-flash")

# Gmail API
SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]

# Cloud Storage for token storage
GCS_BUCKET = os.getenv("GCS_BUCKET")
if not GCS_BUCKET:
    raise ValueError("❌ GCS_BUCKET not set in env variables")

# OAuth config for Cloud Run
REDIRECT_URI = os.getenv(
    "REDIRECT_URI", "https://your-cloudrun-url.com/oauth/callback"
)
GMAIL_SECRET_NAME = os.getenv(
    "GMAIL_CLIENT_SECRET_JSON"
)  # projects/<project_id>/secrets/gmail-client-secret:latest

app = FastAPI()

# -------------------------
# Load Gmail client secret from Secret Manager
# -------------------------
def get_client_secrets_file():
    if not GMAIL_SECRET_NAME:
        raise ValueError("❌ GMAIL_CLIENT_SECRET_JSON not set in env variables")
    
    client = secretmanager.SecretManagerServiceClient()
    response = client.access_secret_version(request={"name": GMAIL_SECRET_NAME})
    secret_json = response.payload.data.decode("UTF-8")
    
    # Save temporarily to /tmp
    tmp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".json")
    tmp_file.write(secret_json.encode())
    tmp_file.close()
    
    return tmp_file.name

CLIENT_SECRETS_FILE = get_client_secrets_file()

# -------------------------
# Gmail authentication
# -------------------------
def get_token_path(user_email: str):
    return f"/tmp/token_{user_email}.pickle"

def download_token_from_gcs(user_email: str):
    client = storage.Client()
    bucket = client.bucket(GCS_BUCKET)
    blob = bucket.blob(f"{user_email}.pickle")
    local_path = get_token_path(user_email)
    if blob.exists():
        blob.download_to_filename(local_path)
        return local_path
    return None

def upload_token_to_gcs(user_email: str):
    client = storage.Client()
    bucket = client.bucket(GCS_BUCKET)
    blob = bucket.blob(f"{user_email}.pickle")
    local_path = get_token_path(user_email)
    if os.path.exists(local_path):
        blob.upload_from_filename(local_path)

def create_oauth_flow():
    return Flow.from_client_secrets_file(
        CLIENT_SECRETS_FILE,
        scopes=SCOPES,
        redirect_uri=REDIRECT_URI
    )

# OAuth routes
@app.get("/oauth/start")
def start_oauth():
    flow = create_oauth_flow()
    authorization_url, _ = flow.authorization_url(
        access_type='offline',
        include_granted_scopes='true'
    )
    return RedirectResponse(url=authorization_url)

@app.get("/oauth/callback")
def oauth_callback(request: Request):
    try:
        flow = create_oauth_flow()
        flow.fetch_token(authorization_response=str(request.url))
        
        credentials = flow.credentials
        user_email = "me"  # you can extract real email if needed
        
        # Save token
        with open(get_token_path(user_email), "wb") as token_file:
            pickle.dump(credentials, token_file)
        upload_token_to_gcs(user_email)
        
        return JSONResponse(content={"message": "Authentication successful! You can now use the API."})
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"OAuth error: {str(e)}")

def authenticate_gmail(user_email="me"):
    creds = None
    local_token = download_token_from_gcs(user_email)
    if local_token and os.path.exists(local_token):
        with open(local_token, "rb") as token_file:
            creds = pickle.load(token_file)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(GoogleRequest())
                # Save refreshed token
                with open(get_token_path(user_email), "wb") as token_file:
                    pickle.dump(creds, token_file)
                upload_token_to_gcs(user_email)
            except Exception:
                raise HTTPException(
                    status_code=401,
                    detail="Authentication expired. Please visit /oauth/start to re-authenticate."
                )
        else:
            raise HTTPException(
                status_code=401,
                detail="Not authenticated. Please visit /oauth/start to authenticate."
            )

    return build("gmail", "v1", credentials=creds)

# -------------------------
# Fetch & summarize emails
# -------------------------
def get_latest_emails(service, max_results=5):
    results = service.users().messages().list(
        userId="me",
        maxResults=max_results,
        labelIds=["INBOX"],
        q="is:unread OR newer_than:7d"
    ).execute()

    messages = results.get("messages", [])
    email_texts = []

    for msg in messages:
        txt = service.users().messages().get(userId="me", id=msg["id"]).execute()
        payload = txt["payload"]
        data = None

        if "parts" in payload:
            for part in payload["parts"]:
                if part["mimeType"] == "text/plain":
                    data = part["body"].get("data")
                    break
        else:
            data = payload["body"].get("data")

        if data:
            text = base64.urlsafe_b64decode(data).decode("utf-8", errors="ignore")
            email_texts.append(text[:4000])
    return email_texts

def summarize_email(email_text):
    prompt = f"""
    Summarize this email in structured format:
    - Summary
    - Key details
    - Action items (if any)

    Email:
    {email_text}
    """
    response = model.generate_content(prompt)
    return response.text

# -------------------------
# FastAPI routes
# -------------------------
@app.get("/")
def root():
    return {"message": "Email Summarizer API", "auth_url": "/oauth/start"}

@app.get("/summarize-emails")
def summarize_emails(count: int = 5):
    try:
        service = authenticate_gmail()
        emails = get_latest_emails(service, count)

        summaries = []
        for i, email_text in enumerate(emails, 1):
            summaries.append({
                "email_number": i,
                "summary": summarize_email(email_text)
            })

        return JSONResponse(content={"summaries": summaries})

    except HTTPException:
        raise
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)
