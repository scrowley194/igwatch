import os
from dotenv import load_dotenv

# Load environment variables from .env file (GitHub Actions creates this before running)
load_dotenv()

def _bool(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().lower() in ("1", "true", "yes", "y")

# Runtime config
POLL_SECONDS = int(os.getenv("POLL_SECONDS", "60"))
DRY_RUN = _bool("DRY_RUN", "false")
START_FROM_DAYS = int(os.getenv("START_FROM_DAYS", "45"))
STRICT_EARNINGS_KEYWORDS = _bool("STRICT_EARNINGS_KEYWORDS", "true")

# Email settings (Gmail SMTP)
MAIL_FROM = os.getenv("MAIL_FROM", "stuatnext@gmail.com")
MAIL_TO = [x.strip() for x in os.getenv("MAIL_TO", "stuatnext@gmail.com").split(",") if x.strip()]

SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "stuatnext@gmail.com")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD")  # Gmail App Password

# User agent for SEC/EDGAR API requests
SEC_USER_AGENT = os.getenv("SEC_USER_AGENT", "NEXT.io Earnings Watcher (contact: stuatnext@gmail.com)")

# config.py (additions)
FIRST_PARTY_ONLY = _bool("FIRST_PARTY_ONLY", "true")
REQUIRE_NUMBERS = _bool("REQUIRE_NUMBERS", "true")
ENABLE_EDGAR = _bool("ENABLE_EDGAR", "false")  # default OFF

# Browser-like UA for IR sites that block generic clients
BROWSER_UA = os.getenv(
    "BROWSER_UA",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

# Minimal allow/block lists (comma-separated envs can override)
GOOD_WIRE_DOMAINS = set([d.strip().lower() for d in os.getenv(
    "GOOD_WIRE_DOMAINS",
    "businesswire.com,globenewswire.com,prnewswire.com,newsfilecorp.com,newsdirect.com"
).split(",") if d.strip()])

BLOCK_DOMAINS = set([d.strip().lower() for d in os.getenv(
    "BLOCK_DOMAINS",
    "news.google.com,seekingalpha.com,marketwatch.com,msn.com,finance.yahoo.com,"
    "yahoo.com,bloomberg.com,thestreet.com,benzinga.com,investopedia.com,"
    "sportsgrid.com,ainvest.com"
).split(",") if d.strip()])

