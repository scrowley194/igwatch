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
# Main Application Logic
# --------------------------------------------------------------------
def process_item(item):
    url, title = item
    if state.seen(url):
        return None
    logger.info(f"Processing: {title} | {url}")
    result = fetch_and_summarize(url, title_hint=title)
    if result:
        state.mark_seen(url)
    return result

def main_loop():
    watchers = [
        GoogleNewsWatcher(),
        PressWireWatcher(),
    ]

    while True:
        for watcher in watchers:
            try:
                for item in watcher.poll():
                    res = process_item(item)
                    if res:
                        logger.info("Got result: %s", res.get("headline"))
                        # TODO: Add email or storage logic here
            except Exception as e:
                logger.error("Watcher error in %s: %s", watcher.__class__.__name__, e)

        time.sleep(60)

if __name__ == "__main__":
    main_loop()
