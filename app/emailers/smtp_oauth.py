import smtplib
import ssl
import os
from email.mime.text import MIMEText
from email.utils import formataddr
from ..utils.log import get_logger

logger = get_logger("smtp_oauth")

SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD")
MAIL_FROM = os.getenv("MAIL_FROM", SMTP_USER)

def send_plaintext(subject: str, body: str, recipients: list[str]):
    """
    Send a plaintext email using STARTTLS with basic authentication.
    If authentication fails, log error and return without raising fatal exceptions.
    """
    if not SMTP_USER or not SMTP_PASSWORD:
        logger.error("SMTP credentials are missing. Cannot send email.")
        return

    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = formataddr(("iGaming Earnings Watcher", MAIL_FROM))
    msg["To"] = ", ".join(recipients)

    try:
        context = ssl.create_default_context()
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as server:
            server.ehlo()
            server.starttls(context=context)
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.sendmail(MAIL_FROM, recipients, msg.as_string())
            logger.info("Email successfully sent to %s (subject: %s)", recipients, subject)
    except smtplib.SMTPAuthenticationError as e:
        logger.error("SMTP authentication failed: %s", e)
    except Exception as e:
        logger.error("SMTP send failed: %s", e)
