# smtp_gmail.py
import smtplib, ssl
from email.message import EmailMessage
from typing import List

SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 587
SMTP_USER = "stuatnext@gmail.com"
SMTP_PASSWORD = "your_gmail_app_password"  # NOT your normal password

def send_plaintext(subject: str, body: str, to_addrs: List[str]):
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = SMTP_USER
    msg["To"] = ", ".join(to_addrs)
    msg.set_content(body)

    context = ssl.create_default_context()
    with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
        server.starttls(context=context)
        server.login(SMTP_USER, SMTP_PASSWORD)
        server.send_message(msg)
