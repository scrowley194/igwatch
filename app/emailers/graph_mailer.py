import requests
from typing import List
from ..config import GRAPH_TENANT_ID, GRAPH_CLIENT_ID, GRAPH_CLIENT_SECRET, MAIL_FROM, MAIL_TO

AUTH_URL = f"https://login.microsoftonline.com/{GRAPH_TENANT_ID}/oauth2/v2.0/token"
SEND_URL = "https://graph.microsoft.com/v1.0/users/{sender}/sendMail"

def _get_token(scope: str = "https://graph.microsoft.com/.default") -> str:
    data = {
        "client_id": GRAPH_CLIENT_ID,
        "client_secret": GRAPH_CLIENT_SECRET,
        "grant_type": "client_credentials",
        "scope": scope,
    }
    r = requests.post(AUTH_URL, data=data, timeout=20)
    r.raise_for_status()
    return r.json()["access_token"]

def send_plaintext(subject: str, body: str, to_addrs: List[str] | None = None):
    to_addrs = to_addrs or MAIL_TO
    token = _get_token()
    msg = {
        "message": {
            "subject": subject,
            "body": {"contentType": "Text", "content": body},
            "toRecipients": [{"emailAddress": {"address": a}} for a in to_addrs],
        },
        "saveToSentItems": "true",
    }
    url = SEND_URL.format(sender=MAIL_FROM)
    r = requests.post(url, headers={"Authorization": f"Bearer {token}"}, json=msg, timeout=20)
    r.raise_for_status()
    return True
