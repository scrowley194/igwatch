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
    START_FROM_DAYS,
    STRICT_EARNINGS_KEYWORDS,
    ENABLE_EDGAR,
    REQUIRE_NUMBERS,
    GOOD_WIRE_DOMAINS,
)

# --- Test Mode Configuration ---
# Set to True to run a one-off test with a real URL.
# Set to False for normal, continuous operation.
TEST_MODE = False
# This is a real URL to a Q2 2025 earnings report for testing the full pipeline.
TEST_URL = "https://www.businesswire.com/news/home/20250806801950/en/Light-Wonder-Inc.-Reports-Second-Quarter-2025-Results"


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
    
    # This query now searches the entire web, not just specific press wire sites.
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

def domain_allowed(url: str, allowed: set[str]) -> bool:
    if not allowed:
        return True
    netloc = urlparse(url).netloc.lower()
    return any(netloc == d or netloc.endswith("." + d) for d in allowed)

def in_good_sources(url: str) -> bool:
    if not GOOD_WIRE_DOMAINS:
        return True
    netloc = urlparse(url).netloc.lower()
    return any(netloc == d or netloc.endswith("." + d) for d in GOOD_WIRE_DOMAINS)

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
# Main loop (Rewritten to support Test Mode and Discovery Mode)
# --------------------------------------------------------------------
def run_test_mode():
    """Executes a single run with a predefined URL to test the full pipeline."""
    logger.info("--- RUNNING IN TARGETED TEST MODE ---")
    logger.info("Processing URL: %s", TEST_URL)
    
    try:
        # A simple regex to extract the company name from the headline for the email subject
        title_hint = "Light & Wonder Inc. Reports Second Quarter 2025 Results"
        company_match = re.match(r"^([\w\s.&,()]+?)(?:\s\(|reports|announces)", title_hint, re.IGNORECASE)
        email_company_name = company_match.group(1).strip() if company_match else "Unknown Company"

        result = fetch_and_summarize(TEST_URL, title_hint=title_hint)
        
        if REQUIRE_NUMBERS and not _has_numbers(result):
            logger.info("Test item skipped (no numbers found).")
            return

        subject = f"[TEST] [{email_company_name}] {result.get('headline','')[:120]}"
        body = render_email(email_company_name, TEST_URL, result)
        send_email(subject, body)
        
    except Exception as e:
        logger.error("Targeted test failed: %s", e, exc_info=True)
    finally:
        logger.info("--- TEST MODE FINISHED ---")

def run_discovery_mode():
    """Runs the continuous polling loop to discover new articles."""
    discovery_query = construct_discovery_query()
    logger.info("Constructed dynamic discovery query for wide search.")

    virtual_watcher_config = {
        "company_name": "Sector Discovery",
        "allowed_domains": set(GOOD_WIRE_DOMAINS),
        "watcher_obj": build_watcher({"type": "gnews", "query": discovery_query})
    }
    
    logger.info(
        "Running in discovery mode. Poll=%ss DRY_RUN=%s START_FROM_DAYS=%s STRICT=%s",
        POLL_SECONDS, DRY_RUN, START_FROM_DAYS, STRICT_EARNINGS_KEYWORDS
    )

    while True:
        cname = virtual_watcher_config["company_name"]
        watcher = virtual_watcher_config["watcher_obj"]
        allowed_domains = virtual_watcher_config["allowed_domains"]
        
        try:
            for item in watcher.poll():
                # The in_good_sources check is now less critical but can act as a quality filter
                if not domain_allowed(item.url, allowed_domains) or not in_good_sources(item.url) or \
                   year_guard(item.title, item.url) or not is_recent(item.published_ts) or \
                   not is_results_like(item.title):
                    continue

                item_id = state.make_id(item.source, item.url, item.title)
                if state.is_seen(item_id):
                    continue

                try:
                    company_match = re.match(r"^([\w\s.&,()]+?)(?:\s\(|reports|announces)", item.title, re.IGNORECASE)
                    email_company_name = company_match.group(1).strip() if company_match else "Unknown Company"

                    result = fetch_and_summarize(item.url, title_hint=item.title)
                    if REQUIRE_NUMBERS and not _has_numbers(result):
                        logger.info("Skipping (no numbers): %s", item.url)
                        continue
                    
                    subject = f"[{email_company_name}] {result.get('headline','')[:120]}"
                    body = render_email(email_company_name, item.url, result)
                    send_email(subject, body)
                    state.mark_seen(item_id, utc_ts())
                    time.sleep(0.5)
                except Exception as e:
                    logger.error("Parse/send error for %s: %s", item.url, e)
        except Exception as e:
            logger.error("Watcher error for %s: %s", cname, e)

        time.sleep(POLL_SECONDS)

if __name__ == "__main__":
    if TEST_MODE:
        run_test_mode()
    else:
        try:
            run_discovery_mode()
        except KeyboardInterrupt:
            logger.info("Stopped.")
