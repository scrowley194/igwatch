import os, smtplib
from email.message import EmailMessage

def _getenv(name: str, default: str = None):
    v = os.getenv(name, default)
    if v is None or v == "":
        raise RuntimeError(f"Missing required env var: {name}")
    return v

def send_plaintext(subject: str, body: str, html: str | None = None):
    host = _getenv("SMTP_HOST")
    port = int(_getenv("SMTP_PORT", "587"))
    user = _getenv("SMTP_USER")
    password = _getenv("SMTP_PASSWORD")
    mail_from = _getenv("MAIL_FROM")
    mail_to = _getenv("MAIL_TO")

    # Compose message
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = mail_from
    msg["To"] = mail_to
    if html:
        msg.set_content(body or "")
        msg.add_alternative(html, subtype="html")
    else:
        msg.set_content(body or "")

    # Gmail requires STARTTLS on 587 and From must match the authenticated user
    with smtplib.SMTP(host, port) as s:
        s.ehlo()
        s.starttls()
        s.login(user, password)
        s.send_message(msg)
