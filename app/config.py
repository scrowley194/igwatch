import os
from dotenv import load_dotenv

load_dotenv()

def _bool(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().lower() in ("1","true","yes","y")

POLL_SECONDS = int(os.getenv("POLL_SECONDS", "60"))
DRY_RUN = _bool("DRY_RUN", "false")
START_FROM_DAYS = int(os.getenv("START_FROM_DAYS", "45"))
STRICT_EARNINGS_KEYWORDS = _bool("STRICT_EARNINGS_KEYWORDS", "true")

# Email (Graph)
GRAPH_TENANT_ID = os.getenv("GRAPH_TENANT_ID")
GRAPH_CLIENT_ID = os.getenv("GRAPH_CLIENT_ID")
GRAPH_CLIENT_SECRET = os.getenv("GRAPH_CLIENT_SECRET")
MAIL_FROM = os.getenv("MAIL_FROM", "newsletter@next.io")
MAIL_TO = [x.strip() for x in os.getenv("MAIL_TO", "stuart@next.io").split(",") if x.strip()]

# SMTP OAuth (fallback)
SMTP_SERVER = os.getenv("SMTP_SERVER", "smtp.office365.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_SENDER = os.getenv("SMTP_SENDER", MAIL_FROM)
SMTP_CLIENT_ID = os.getenv("SMTP_CLIENT_ID")
SMTP_CLIENT_SECRET = os.getenv("SMTP_CLIENT_SECRET")
SMTP_TENANT_ID = os.getenv("SMTP_TENANT_ID")

SEC_USER_AGENT = os.getenv("SEC_USER_AGENT", "NEXT.io Earnings Watcher (contact: you@next.io)")
