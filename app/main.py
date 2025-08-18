import os
import time
import logging
from datetime import datetime, timezone

# --- Local Imports from your project ---
from .watchers.press_wires import GoogleNewsWatcher, PressWireWatcher
from .utils.log import get_logger
from .utils.state import State
from .emailers import smtp_oauth
from .config import (
    DRY_RUN,
    MAIL_FROM,
    MAIL_TO,
    START_FROM_DAYS,
    USE_JINA_READER_FALLBACK,
    JINA_API_KEY,
)
from .parsers.extract import fetch_and_summarize
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
def render_email(item: dict) -> str:
    """Render a dict result into a plaintext email body."""
    lines = [
        f"Headline: {item.get('headline','')}",
        f"URL: {item.get('final_url','')}",
        "",
        f"Summary: {item.get('short_summary','')}",
        "",
        "Key Highlights:",
    ]
    for h in item.get("key_highlights", []):
        lines.append(f" - {h}")
    lines.append("")

    metrics = []
    for k in ["revenue", "ebitda", "net_income", "eps"]:
        v = item.get(k, {})
        if isinstance(v, dict):
            metrics.append(f"{k.upper()}: {v.get('current','')} (YoY: {v.get('yoy','')})")
    if metrics:
        lines.append("Metrics:")
        lines.extend([" - " + m for m in metrics])

    if item.get("final_thoughts"):
        lines.append("")
        lines.append("Notes: " + item["final_thoughts"])

    return "\n".join(lines)


def send_email(subject: str, body: str):
    """Send email using smtp_oauth, unless DRY_RUN is set."""
    if DRY_RUN:
        logger.info("[DRY RUN] Would send email: %s\n%s", subject, body)
        return
    to = MAIL_TO.split(",") if isinstance(MAIL_TO, str) else MAIL_TO
    smtp_oauth.send_plaintext(subject, body, to, mail_from=MAIL_FROM)

# --------------------------------------------------------------------
# Main Application Logic
# --------------------------------------------------------------------
def process_item(url: str, title: str):
    if state.has(url):
        return
    result = fetch_and_summarize(url, title_hint=title)
    if not result:
        return
    state.add(url)
    subject = f"[Earnings Watch] {result.get('headline','Update')}"
    body = render_email(result)
    send_email(subject, body)
    logger.info("Sent email for %s", url)


def main_loop():
    watchers = [
        GoogleNewsWatcher(start_days=START_FROM_DAYS),
        PressWireWatcher(start_days=START_FROM_DAYS),
    ]

    for w in watchers:
        logger.info(DIV)
        logger.info("Checking %s", w.__class__.__name__)
        try:
            for url, title in w.poll():
                process_item(url, title)
        except Exception as e:
            logger.exception("Watcher %s failed: %s", w.__class__.__name__, e)

    state.save()


if __name__ == "__main__":
    main_loop()
