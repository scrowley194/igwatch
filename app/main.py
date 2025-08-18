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
    JINA_API_KEY,
    MAX_HIGHLIGHTS
)
from .parsers.extract import parse_from_html, parse_from_clean_text
# Import fetcher utilities directly from app.net_fetchers (single file module)
from .net_fetchers import http_get, looks_like_botwall, fetch_text_via_jina, BROWSER_UA

# --------------------------------------------------------------------
# Setup
# --------------------------------------------------------------------
logger = get_logger("igwatch")
state = State("data/seen.db")
DIV = "-" * 72

# --------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------
# (same helper functions as before...)
# ... keep unchanged ...

# --------------------------------------------------------------------
# Fetch + Summarize
# --------------------------------------------------------------------
def fetch_and_summarize(url: str, title_hint: str = None):
    headers = {"User-Agent": BROWSER_UA}
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
# (render_email and send_email unchanged)

# --------------------------------------------------------------------
# Main Application Logic
# --------------------------------------------------------------------
# (process_item and main_loop unchanged)

if __name__ == "__main__":
    main_loop()
