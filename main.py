import os
import base64
import pickle
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from googleapiclient.discovery import build
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from google.cloud import storage
import google.generativeai as genai

# Load environment variables (local testing)
from dotenv import load_dotenv
load_dotenv()

# === CONFIG ===
GENAI_API_KEY = os.getenv("GENAI_API_KEY")
if not GENAI_API_KEY:
    raise ValueError("❌ GENAI_API_KEY not set in env variables")

# Gemini
genai.configure(api_key=GENAI_API_KEY)
model = genai.GenerativeModel("gemini-1.5-flash")

# Gmail API
SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]

# Cloud Storage for token storage
GCS_BUCKET = os.getenv("GCS_BUCKET")  # set in Cloud Run env
if not GCS_BUCKET:
    raise ValueError("❌ GCS_BUCKET not set in env variables")

app = FastAPI()

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

def authenticate_gmail(user_email="me"):
    creds = None
    local_token = download_token_from_gcs(user_email)
    if local_token and os.path.exists(local_token):
        with open(local_token, "rb") as token_file:
            creds = pickle.load(token_file)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(
                "/secrets/gmail-client-secret.json", SCOPES
            )
            creds = flow.run_local_server(port=0)
        with open(get_token_path(user_email), "wb") as token_file:
            pickle.dump(creds, token_file)
        upload_token_to_gcs(user_email)

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
# FastAPI route
# -------------------------
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

    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)
