import os
from dotenv import load_dotenv

load_dotenv()

def _bool(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().lower() in ("1", "true", "yes", "y")

POLL_SECONDS = int(os.getenv("POLL_SECONDS", "60"))
DRY_RUN = _bool("DRY_RUN", "false")
START_FROM_DAYS = int(os.getenv("START_FROM_DAYS", "45"))
STRICT_EARNINGS_KEYWORDS = _bool("STRICT_EARNINGS_KEYWORDS", "true")

MAIL_FROM = os.getenv("MAIL_FROM", "stuatnext@gmail.com")
MAIL_TO = [x.strip() for x in os.getenv("MAIL_TO", "stuatnext@gmail.com").split(",") if x.strip()]

SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "stuatnext@gmail.com")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD")  # 16-char Gmail App Password

SEC_USER_AGENT = os.getenv("SEC_USER_AGENT", "NEXT.io Earnings Watcher (contact: stuatnext@gmail.com)")
