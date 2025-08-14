import os
import time
import re
import logging
from datetime import datetime, timezone
from urllib.parse import urlparse

# --- Local Imports from your project ---
from .watchers.press_wires import PressWireWatcher, GoogleNewsWatcher
from .utils.log import get_logger
from .utils.state import State
from .watchers.rss_watcher import RSSWatcher, RSSPageWatcher
from .watchers.page_watcher import PageWatcher
from .watchers.edgar_watcher import EdgarWatcher
from .parsers.extract import fetch_and_summarize
from .emailers import smtp_oauth
from .config import (
    POLL_SECONDS,
    DRY_RUN,
    MAIL_FROM,
    MAIL_TO,
    START_FROM_DAYS as CONFIG_START_FROM_DAYS, # Renamed to avoid conflict
    STRICT_EARNINGS_KEYWORDS,
    ENABLE_EDGAR,
    REQUIRE_NUMBERS,
    GOOD_WIRE_DOMAINS,
    BLOCK_DOMAINS, # Import the block list
)

# --- Run Mode Configuration ---
# HISTORICAL_RUN: Performs one deep search for past articles and then exits. Perfect for testing.
# TEST_MODE: Processes a single hardcoded URL.
# If both are False, runs in continuous polling mode.
HISTORICAL_RUN = True
TEST_MODE = False

# Override START_FROM_DAYS for a deeper historical search
START_FROM_DAYS = 90
TEST_URL = "https://www.globenewswire.com/news-release/2025/05/08/2877564/0/en/Genius-Sports-Announces-First-Quarter-2025-Financial-Results.html"


# --------------------------------------------------------------------
# Setup & Dynamic Discovery Configuration
# --------------------------------------------------------------------
logger = get_logger("igwatch")
state = State("data/seen.db")
DIV = "-" * 72

# Keywords to define the sectors we are interested in.
SECTOR_KEYWORDS = [
    "igaming",
    "online casino",
    "sports betting",
    "gambling technology",
]

# Keywords to identify relevant financial news announcements.
FINANCIAL_NEWS_KEYWORDS = [
    "financial results", "quarterly earnings", "quarterly update", "annual report",
    "guidance update", "earnings call", "Q1", "Q2", "Q3", "Q4", "H1", "H2", "FY",
    "first quarter", "second quarter", "third quarter", "fourth quarter",
    "full year", "half year", "interim report"
]

# --------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------
def utc_ts() -> int:
    return int(datetime.now(timezone.utc).timestamp())

def construct_discovery_query() -> str:
    """
    Creates a single, broad search query to discover recent financial news across the entire sector.
    """
    sector_query_part = " OR ".join([f'"{kw}"' for kw in SECTOR_KEYWORDS])
    financial_query_part = " OR ".join([f'"{kw}"' for kw in FINANCIAL_NEWS_KEYWORDS])
    
    # This query searches the entire web.
    return f"({sector_query_part}) AND ({financial_query_part})"

def build_watcher(wcfg: dict):
    wtype = wcfg.get("type")
    if wtype == "rss":
        return RSSWatcher(wcfg["url"], allowed_domains=wcfg.get("allowed_domains"))
    if wtype == "rss_page":
        return RSSPageWatcher(wcfg["url"])
    if wtype == "page":
        return PageWatcher(wcfg["url"], allowed_domains=wcfg.get("allowed_domains"))
    if wtype == "gnews":
        return GoogleNewsWatcher(wcfg["query"])
    if wtype == "edgar_atom":
        if not ENABLE_EDGAR:
            return None
        return EdgarWatcher(wcfg["ticker"])
    if wtype == "wire":
        return PressWireWatcher(wcfg["url"])
    raise ValueError(f"Unknown watcher type: {wtype}")

def is_recent(published_ts: int | None) -> bool:
    if not published_ts:
        return True
    # Use the overridden START_FROM_DAYS for historical runs
    return published_ts >= (utc_ts() - START_FROM_DAYS * 86400)

def is_results_like(title: str) -> bool:
    if not STRICT_EARNINGS_KEYWORDS:
        return True
    return any(k in (title or "").lower() for k in FINANCIAL_NEWS_KEYWORDS)

def year_guard(title: str, url: str) -> bool:
    years = [int(y) for y in re.findall(r"(19|20)\d{2}", f"{title} {url}") if len(y) >= 4]
    return bool(years) and max(years) <= datetime.now().year - 2

def _has_numbers(result: dict) -> bool:
    def ok(d):
        return isinstance(d, dict) and bool(d.get("current")) and d["current"].lower() not in ("not found", "n/a")
    return ok(result.get("revenue")) or ok(result.get("ebitda"))

def is_blocked_domain(url: str) -> bool:
    """Check if the URL's domain is in the block list."""
    if not BLOCK_DOMAINS:
        return False
    netloc = urlparse(url).netloc.lower()
    return any(netloc == d or netloc.endswith("." + d) for d in BLOCK_DOMAINS)

# --------------------------------------------------------------------
# Email rendering & Sending (Your original code, unchanged)
# --------------------------------------------------------------------
def render_email(company: str, src_url: str, result: dict) -> str:
    lines = [
        f"Company: {company}", f"Source: {src_url}", DIV,
        f"Headline: {result.get('headline','')}", DIV,
        "Summary:", result.get("short_summary",""), DIV,
        "Top 5 controversial points:"
    ]
    cps = result.get("controversial_points") or []
    lines.extend([f"- {c}" for c in cps[:5]] if cps else ["- None detected."])

    e, r = result.get("ebitda", {}), result.get("revenue", {})
    def fmt_metric(name: str, d: dict) -> str | None:
        cur, yoy = (d.get("current") or "").strip(), (d.get("yoy") or "").strip()
        if cur.lower() in ("", "not found", "n/a"): return None
        return f"{name}: {cur}" + (f" | YoY {yoy}" if yoy and yoy.lower() != "n/a" else "")

    metrics = [m for m in (fmt_metric("Revenue", r), fmt_metric("EBITDA", e)) if m]
    if metrics:
        lines.extend(metrics + [DIV])

    lines.append("Geography breakdown (YoY):")
    lines.extend([f"- {g}" for g in result.get("geo_breakdown", [])] or ["- None"])
    lines.append(DIV)
    lines.append("Product breakdown (YoY):")
    lines.extend([f"- {p}" for p in result.get("product_breakdown", [])] or ["- None"])
    lines.extend([DIV, "Final thoughts:", result.get("final_thoughts", ""), DIV, "— NEXT.io iGaming Earnings Watcher"])
    return "\n".join(lines)

def send_email(subject: str, body: str):
    if DRY_RUN:
        logger.info("[DRY_RUN] Email from %s to %s\nSubject: %s\n\n%s", MAIL_FROM, MAIL_TO, subject, body)
        return
    try:
        smtp_oauth.send_plaintext(subject, body, MAIL_TO)
        logger.info("Email sent successfully: %s", subject)
    except Exception as e:
        logger.error("SMTP send failed: %s", e)

# --------------------------------------------------------------------
# Main loop Functions
# --------------------------------------------------------------------
def process_item(item):
    """Processes a single found item (fetches, summarizes, sends email)."""
    try:
        # **FIXED LOGIC**: Now we only filter out blocked domains.
        if is_blocked_domain(item.url) or \
           year_guard(item.title, item.url) or \
           not is_recent(item.published_ts) or \
           not is_results_like(item.title):
            return

        item_id = state.make_id(item.source, item.url, item.title)
        if state.is_seen(item_id):
            return

        company_match = re.match(r"^([\w\s.&,()]+?)(?:\s\(|reports|announces)", item.title, re.IGNORECASE)
        email_company_name = company_match.group(1).strip() if company_match else "Unknown Company"

        result = fetch_and_summarize(item.url, title_hint=item.title)
        if REQUIRE_NUMBERS and not _has_numbers(result):
            logger.info("Skipping (no numbers): %s", item.url)
            return
        
        subject = f"[{email_company_name}] {result.get('headline','')[:120]}"
        body = render_email(email_company_name, item.url, result)
        send_email(subject, body)
        state.mark_seen(item_id, utc_ts())
        time.sleep(0.5)
    except Exception as e:
        logger.error("Parse/send error for %s: %s", item.url, e)

def run_historical_mode():
    """Runs a single, deep discovery search and then exits."""
    logger.info("--- RUNNING IN HISTORICAL DISCOVERY MODE (90 days) ---")
    discovery_query = construct_discovery_query()
    watcher = build_watcher({"type": "gnews", "query": discovery_query})
    
    try:
        found_items = list(watcher.poll())
        logger.info("Historical query found %d potential articles. Processing...", len(found_items))
        for item in found_items:
            process_item(item)
    except Exception as e:
        logger.error("Historical discovery watcher failed: %s", e)
    finally:
        logger.info("--- HISTORICAL MODE FINISHED ---")

def run_continuous_mode():
    """Runs the continuous polling loop to discover new articles."""
    discovery_query = construct_discovery_query()
    watcher = build_watcher({"type": "gnews", "query": discovery_query})
    
    logger.info(
        "Running in continuous discovery mode. Poll=%ss DRY_RUN=%s START_FROM_DAYS=%s STRICT=%s",
        POLL_SECONDS, DRY_RUN, CONFIG_START_FROM_DAYS, STRICT_EARNINGS_KEYWORDS
    )

    while True:
        try:
            for item in watcher.poll():
                process_item(item)
        except Exception as e:
            logger.error("Watcher error for Sector Discovery: %s", e)
        time.sleep(POLL_SECONDS)

if __name__ == "__main__":
    if HISTORICAL_RUN:
        run_historical_mode()
    elif TEST_MODE:
        # This mode is now secondary to the historical run for testing
        pass # Or implement the single URL test logic here if needed
    else:
        try:
            run_continuous_mode()
        except KeyboardInterrupt:
            logger.info("Stopped.")
