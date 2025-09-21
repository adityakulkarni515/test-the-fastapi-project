import os
import base64
import pickle
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse, RedirectResponse
from googleapiclient.discovery import build
from google_auth_oauthlib.flow import Flow
from google.auth.transport.requests import Request as GoogleRequest
import google.generativeai as genai

# === CONFIG ===
GENAI_API_KEY = os.getenv("GENAI_API_KEY")
if not GENAI_API_KEY:
    raise ValueError("❌ GENAI_API_KEY not set in env variables")

# Gemini
genai.configure(api_key=GENAI_API_KEY)
model = genai.GenerativeModel("gemini-1.5-flash")

# Gmail API
SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]

# OAuth hardcoded credentials for POC
CLIENT_ID = "YOUR_CLIENT_ID_HERE"
CLIENT_SECRET = "YOUR_CLIENT_SECRET_HERE"
REDIRECT_URI = "https://test-the-fastapi-project-360415887046.asia-south1.run.app/oauth/callback"

# Token storage (local temp, can be replaced with GCS later)
TOKEN_STORAGE = "/tmp"

app = FastAPI()

# -------------------------
# Gmail authentication
# -------------------------
def get_token_path(user_email: str):
    return os.path.join(TOKEN_STORAGE, f"token_{user_email}.pickle")

def create_oauth_flow():
    return Flow.from_client_config(
        {
            "web": {
                "client_id": CLIENT_ID,
                "client_secret": CLIENT_SECRET,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": [REDIRECT_URI],
            }
        },
        scopes=SCOPES,
        redirect_uri=REDIRECT_URI,
    )

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
        user_email = "me"

        # Save token locally
        with open(get_token_path(user_email), "wb") as token_file:
            pickle.dump(credentials, token_file)

        return JSONResponse({"message": "Authentication successful! You can now use /summarize-emails"})
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"OAuth error: {str(e)}")

def authenticate_gmail(user_email="me"):
    creds = None
    token_path = get_token_path(user_email)
    if os.path.exists(token_path):
        with open(token_path, "rb") as f:
            creds = pickle.load(f)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(GoogleRequest())
            with open(token_path, "wb") as f:
                pickle.dump(creds, f)
        else:
            raise HTTPException(
                status_code=401,
                detail="Not authenticated. Visit /oauth/start to authenticate."
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
        summaries = [{"email_number": i+1, "summary": summarize_email(email)} for i, email in enumerate(emails)]
        return JSONResponse({"summaries": summaries})
    except HTTPException:
        raise
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
