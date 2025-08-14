import base64, ssl, smtplib, requests
from email.message import EmailMessage
from typing import List
from ..config import SMTP_SERVER, SMTP_PORT, SMTP_SENDER, SMTP_CLIENT_ID, SMTP_CLIENT_SECRET, SMTP_TENANT_ID

def _get_access_token():
    token_url = f"https://login.microsoftonline.com/{SMTP_TENANT_ID}/oauth2/v2.0/token"
    data = {
        "client_id": SMTP_CLIENT_ID,
        "client_secret": SMTP_CLIENT_SECRET,
        "grant_type": "client_credentials",
        "scope": "https://outlook.office365.com/.default",
    }
    r = requests.post(token_url, data=data, timeout=20)
    r.raise_for_status()
    return r.json()["access_token"]

def send_plaintext(subject: str, body: str, to_addrs: List[str]):
    token = _get_access_token()
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = SMTP_SENDER
    msg["To"] = ", ".join(to_addrs)
    msg.set_content(body)
    auth_string = f"user={SMTP_SENDER}auth=Bearer {token}".encode()
    auth_b64 = base64.b64encode(auth_string)
    context = ssl.create_default_context()
    with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
        server.starttls(context=context)
        server.docmd("AUTH", "XOAUTH2 " + auth_b64.decode())
        server.send_message(msg)
