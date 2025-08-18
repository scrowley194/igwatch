import os
import time
import re
import logging
from datetime import datetime, timezone
from urllib.parse import urlparse

# --- Local Imports from your project ---
from .watchers.press_wires import GoogleNewsWatcher, PressWireWatcher
from .utils.log import get_logger
from .utils.state import State
from .emailers import smtp_oauth
from .config import (
    DRY_RUN,
    MAIL_FROM,
    MAIL_TO,
    STRICT_EARNINGS_KEYWORDS,
    REQUIRE_NUMBERS,
    GOOD_WIRE_DOMAINS,
    BLOCK_DOMAINS,
    START_FROM_DAYS,
    USE_JINA_READER_FALLBACK,
    JINA_API_KEY
)
from .net.fetchers import http_get, looks_like_botwall, fetch_text_via_jina
from .parsers.extract import parse_from_html, parse_from_clean_text

# --------------------------------------------------------------------
# Setup
# --------------------------------------------------------------------
logger = get_logger("igwatch")
state = State("data/seen.db")
DIV = "-" * 72
MAX_HIGHLIGHTS = int(os.getenv("MAX_HIGHLIGHTS", "6"))

# Keywords for search queries
SECTOR_KEYWORDS = [
    "igaming", "online casino", "sports betting", "gambling technology",
    "igaming supplier", "sportsbook", "iCasino", "lottery"
]
FINANCIAL_NEWS_KEYWORDS = [
    "financial results", "quarterly earnings", "quarterly update", "annual report",
    "guidance update", "earnings call", "Q1", "Q2", "Q3", "Q4", "H1", "H2", "FY",
    "first quarter", "second quarter", "third quarter", "fourth quarter",
    "full year", "half year", "interim report",
    "trading update", "performance update", "preliminary results",
    "financial highlights", "management discussion", "financial statements",
    "interim results", "trading statement"
]

# --------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------
def utc_ts() -> int:
    return int(datetime.now(timezone.utc).timestamp())

def construct_queries() -> list[str]:
    sector_query = " OR ".join([f'"{kw}"' for kw in SECTOR_KEYWORDS])
    financial_query = " OR ".join([f'"{kw}"' for kw in FINANCIAL_NEWS_KEYWORDS])
    broad_search = f"({sector_query}) AND ({financial_query})"
    primary_source_sites = " OR ".join([f'site:{site}' for site in GOOD_WIRE_DOMAINS])
    targeted_search = f"({sector_query}) AND ({financial_query}) AND ({primary_source_sites})"
    return [broad_search, targeted_search]

def is_recent(published_ts: int | None) -> bool:
    if not published_ts:
        return True
    return published_ts >= (utc_ts() - START_FROM_DAYS * 86400)

def _results_like_patterns(title: str) -> bool:
    t = (title or "").lower()
    if any(k.lower() in t for k in FINANCIAL_NEWS_KEYWORDS):
        return True
    if re.search(r"\bq[1-4]\b|\bfy\d{2,4}\b|\b(full year|half[- ]year|interim)\b", t):
        return True
    if re.search(r"\b(h1|h2)\b", t):
        return True
    return False

def is_results_like(title: str) -> bool:
    if not STRICT_EARNINGS_KEYWORDS:
        return True
    return _results_like_patterns(title)

def year_guard(title: str, url: str) -> bool:
    years = [int(y) for y in re.findall(r"(20\d{2})", f"{title} {url}")]
    return bool(years) and max(years) < datetime.now().year - 1

def _has_numbers(result: dict) -> bool:
    def ok(d):
        return isinstance(d, dict) and bool(d.get("current")) and str(d["current"]).strip().lower() not in ("not found", "n/a")
    return any(ok(result.get(k)) for k in ("revenue", "ebitda", "net_income", "eps"))

# --------------------------------------------------------------------
# Fetch + Summarize
# --------------------------------------------------------------------
def fetch_and_summarize(url: str, title_hint: str = None):
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"}
    try:
        final_url, html, ctype = http_get(url, headers=headers)
    except Exception as e:
        logger.error("Direct fetch failed: %s", e)
        return None

    if USE_JINA_READER_FALLBACK and looks_like_botwall(html):
        try:
            clean_text = fetch_text_via_jina(final_url, api_key=JINA_API_KEY)
            return parse_from_clean_text(clean_text, source_url=final_url)
        except Exception as e:
            logger.error("Jina Reader fallback failed: %s", e)
            return None

    try:
        return parse_from_html(html, source_url=final_url)
    except Exception as e:
        logger.error("HTML parse failed: %s", e)
        return None

# --------------------------------------------------------------------
# Email rendering & Sending
# --------------------------------------------------------------------

# (render_email and send_email unchanged from your original file)
# ... keep same implementation as before ...

# --------------------------------------------------------------------
# Main Application Logic
# --------------------------------------------------------------------

# (process_item and main_loop unchanged, but now call our new fetch_and_summarize)
# ... keep same implementation as before ...

if __name__ == "__main__":
    main_loop()
