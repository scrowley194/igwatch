import os

# --------------------------------------------------------------------
# Utility
# --------------------------------------------------------------------
def _bool(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().lower() in ("1", "true", "yes", "on")

# --------------------------------------------------------------------
# Core Runtime Flags
# --------------------------------------------------------------------
DRY_RUN = _bool("DRY_RUN", "false")
START_FROM_DAYS = int(os.getenv("START_FROM_DAYS", "30"))
STRICT_EARNINGS_KEYWORDS = _bool("STRICT_EARNINGS_KEYWORDS", "true")
REQUIRE_NUMBERS = _bool("REQUIRE_NUMBERS", "true")
FIRST_PARTY_ONLY = _bool("FIRST_PARTY_ONLY", "true")

# --------------------------------------------------------------------
# Email
# --------------------------------------------------------------------
MAIL_FROM = os.getenv("MAIL_FROM", "")
MAIL_TO = os.getenv("MAIL_TO", "").split(",") if os.getenv("MAIL_TO") else []

# --------------------------------------------------------------------
# SMTP / Gmail
# --------------------------------------------------------------------
SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")

# --------------------------------------------------------------------
# Fetch & Parsing
# --------------------------------------------------------------------
USE_JINA_READER_FALLBACK = _bool("USE_JINA_READER_FALLBACK", "true")
JINA_API_KEY = os.getenv("JINA_API_KEY", "").strip() or None
MAX_HIGHLIGHTS = int(os.getenv("MAX_HIGHLIGHTS", "6"))

# --------------------------------------------------------------------
# Domains & Filtering
# --------------------------------------------------------------------
GOOD_WIRE_DOMAINS = [d.strip() for d in os.getenv("GOOD_WIRE_DOMAINS", "").split(",") if d.strip()]
BLOCK_DOMAINS = [d.strip() for d in os.getenv("JUNK_DOMAINS", "").split(",") if d.strip()]

# --------------------------------------------------------------------
# SEC / EDGAR (optional)
# --------------------------------------------------------------------
ENABLE_EDGAR = _bool("ENABLE_EDGAR", "false")
SEC_USER_AGENT = os.getenv("SEC_USER_AGENT", "igwatch-bot")

# --------------------------------------------------------------------
# Other
# --------------------------------------------------------------------
POLL_SECONDS = int(os.getenv("POLL_SECONDS", "60"))
