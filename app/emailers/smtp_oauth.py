# smtp_oauth.py  (Gmail SMTP with App Password)
import smtplib
import ssl
from email.message import EmailMessage
from typing import List
import os
import logging

logger = logging.getLogger(__name__)

SMTP_SERVER = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", 587))
SMTP_USER = os.getenv("SMTP_USER")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD")


def send_plaintext(subject: str, body: str, to_addrs: List[str]):
    """
    Send a plain text email using Gmail SMTP + App Password.

    Environment variables required:
        SMTP_HOST=smtp.gmail.com
        SMTP_PORT=587
        SMTP_USER=your_gmail_address
        SMTP_PASSWORD=your_gmail_app_password
    """
    if not SMTP_USER or not SMTP_PASSWORD:
        raise ValueError("SMTP_USER and SMTP_PASSWORD must be set in environment variables.")

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = SMTP_USER
    msg["To"] = ", ".join(to_addrs)
    msg.set_content(body)

    try:
        context = ssl.create_default_context()
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
            server.starttls(context=context)
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.send_message(msg)
        logger.info("Email sent successfully via Gmail SMTP to %s", to_addrs)
    except Exception as e:
        logger.error("Failed to send email via Gmail SMTP: %s", e)
        raise
