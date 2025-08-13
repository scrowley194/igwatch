# app/emailers/smtp_oauth.py
# Rewritten to use basic Gmail SMTP (STARTTLS + App Password). No OAuth.

import os
import smtplib
from email.message import EmailMessage
from typing import List, Iterable

def _getenv(name: str, default: str | None = None) -> str:
    v = os.getenv(name, default)
    if not v:
        raise RuntimeError(f"Missing required env var: {name}")
    return v

def _as_list(v: str | Iterable[str]) -> List[str]:
    if isinstance(v, str):
        return [x.strip() for x in v.split(",") if x.strip()]
    return list(v)

def send_plaintext(subject: str, body: str, to_addrs: List[str] | None = None) -> bool:
    smtp_host = _getenv("SMTP_HOST")          # e.g. smtp.gmail.com
    smtp_port = int(_getenv("SMTP_PORT", "587"))
    smtp_user = _getenv("SMTP_USER")          # your full Gmail address
    smtp_pass = _getenv("SMTP_PASSWORD")      # 16-char App Password
    mail_from = _getenv("MAIL_FROM", smtp_user)
    mail_to_env = _getenv("MAIL_TO", smtp_user)

    if mail_from.lower() != smtp_user.lower():
        mail_from = smtp_user

    recipients = _as_list(to_addrs or mail_to_env)

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = mail_from
    msg["To"] = ", ".join(recipients)
    msg.set_content(body or "")

    with smtplib.SMTP(smtp_host, smtp_port, timeout=30) as s:
        s.ehlo()
        s.starttls()
        s.ehlo()
        s.login(smtp_user, smtp_pass)
        s.send_message(msg)

    return True
