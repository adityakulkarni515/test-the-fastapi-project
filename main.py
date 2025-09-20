import os
import base64
import pickle
from dotenv import load_dotenv
import google.generativeai as genai
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from googleapiclient.discovery import build
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request

# Load .env
load_dotenv()
GENAI_API_KEY = os.getenv("GENAI_API_KEY")
if not GENAI_API_KEY:
    raise ValueError("❌ GENAI_API_KEY not found in .env file")

# Gemini Config
genai.configure(api_key=GENAI_API_KEY)
model = genai.GenerativeModel("gemini-1.5-flash")

# Gmail API Config
SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]

app = FastAPI()

def authenticate_gmail():
    creds = None
    if os.path.exists("token.pickle"):
        with open("token.pickle", "rb") as token:
            creds = pickle.load(token)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(
                "client_secret.json", SCOPES
            )
            creds = flow.run_local_server(port=0)
        with open("token.pickle", "wb") as token:
            pickle.dump(creds, token)

    return build("gmail", "v1", credentials=creds)

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
