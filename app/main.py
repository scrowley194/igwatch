import os
import time
import re
import logging
from datetime import datetime, timezone
from urllib.parse import urlparse

# --- Local Imports from your project ---
from .watchers.press_wires import GoogleNewsWatcher
from .utils.log import get_logger
from .utils.state import State
from .parsers.extract import fetch_and_summarize
from .emailers import smtp_oauth
from .config import (
    DRY_RUN,
    MAIL_FROM,
    MAIL_TO,
    STRICT_EARNINGS_KEYWORDS,
    REQUIRE_NUMBERS,
    GOOD_WIRE_DOMAINS,
    BLOCK_DOMAINS,
)

# --- Run Configuration ---
# This script is now configured for a single, daily scan.
# How many days back to look for articles. 2 is ideal for a daily run.
START_FROM_DAYS = 2

# --------------------------------------------------------------------
# Setup
# --------------------------------------------------------------------
logger = get_logger("igwatch")
state = State("data/seen.db")
DIV = "-" * 72

# Keywords for search queries
SECTOR_KEYWORDS = [
    "igaming", "online casino", "sports betting", "gambling technology"
]
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

def construct_queries() -> list[str]:
    """
    Creates multiple, targeted search queries to ensure comprehensive coverage.
    """
    sector_query = " OR ".join([f'"{kw}"' for kw in SECTOR_KEYWORDS])
    financial_query = " OR ".join([f'"{kw}"' for kw in FINANCIAL_NEWS_KEYWORDS])
    
    # Query 1: Broad search across the entire web for primary sources.
    broad_search = f"({sector_query}) AND ({financial_query})"
    
    # Query 2: Targeted search focused only on high-value press wire domains.
    primary_source_sites = " OR ".join([f'site:{site}' for site in GOOD_WIRE_DOMAINS])
    targeted_search = f"({sector_query}) AND ({financial_query}) AND ({primary_source_sites})"
    
    return [broad_search, targeted_search]

def is_recent(published_ts: int | None) -> bool:
    if not published_ts:
        return True
    return published_ts >= (utc_ts() - START_FROM_DAYS * 86400)

def is_results_like(title: str) -> bool:
    if not STRICT_EARNINGS_KEYWORDS:
        return True
    return any(k in (title or "").lower() for k in FINANCIAL_NEWS_KEYWORDS)

def year_guard(title: str, url: str) -> bool:
    years = [int(y) for y in re.findall(r"(20\d{2})", f"{title} {url}")]
    return bool(years) and max(years) < datetime.now().year - 1

def _has_numbers(result: dict) -> bool:
    def ok(d):
        return isinstance(d, dict) and bool(d.get("current")) and d["current"].lower() not in ("not found", "n/a")
    return ok(result.get("revenue")) or ok(result.get("ebitda"))

# --------------------------------------------------------------------
# Email rendering & Sending
# --------------------------------------------------------------------
def render_email(company: str, src_url: str, result: dict) -> str:
    lines = [
        f"Company: {company}", f"Source: {src_url}", DIV,
        f"Headline: {result.get('headline','')}", DIV,
        "Summary:", result.get("short_summary",""), DIV,
    ]
    highlights = result.get("key_highlights") or []
    if highlights:
        lines.append("Key Highlights:")
        lines.extend([f"- {h}" for h in highlights])
        lines.append(DIV)
    lines.append("Top 5 controversial points:")
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
# Main Application Logic
# --------------------------------------------------------------------
def process_item(item) -> bool:
    """
    Processes a single found item. Returns True if an email was sent, False otherwise.
    """
    try:
        # **FIX**: The is_blocked_domain check is now handled inside fetch_and_summarize.
        if year_guard(item.title, item.url) or \
           not is_recent(item.published_ts) or \
           not is_results_like(item.title):
            return False

        item_id = state.make_id(item.source, item.url, item.title)
        if state.is_seen(item_id):
            return False

        company_match = re.match(r"^([\w\s.&,()]+?)(?:\s\(|reports|announces)", item.title, re.IGNORECASE)
        email_company_name = company_match.group(1).strip() if company_match else "Unknown Company"

        result = fetch_and_summarize(item.url, title_hint=item.title)
        # If fetch_and_summarize returns None, it means the domain was blocked.
        if result is None:
            return False
            
        if REQUIRE_NUMBERS and not _has_numbers(result):
            logger.info("Skipping (no numbers): %s", item.url)
            return False
        
        subject = f"[{email_company_name}] {result.get('headline','')[:120]}"
        body = render_email(email_company_name, result.get("final_url", item.url), result)
        send_email(subject, body)
        state.mark_seen(item_id, utc_ts())
        time.sleep(1)
        return True
    except Exception as e:
        logger.error("Parse/send error for %s: %s", item.url, e)
        return False

def main_loop():
    """
    Runs a single, comprehensive discovery scan for a daily scheduled job.
    """
    logger.info("--- RUNNING DAILY DISCOVERY SCAN (%s days) ---", START_FROM_DAYS)
    
    queries = construct_queries()
    watchers = [GoogleNewsWatcher(query) for query in queries]
    
    all_items = {}
    for watcher in watchers:
        try:
            for item in watcher.poll():
                if item.url not in all_items:
                    all_items[item.url] = item
        except Exception as e:
            logger.error("Watcher failed for query '%s': %s", watcher.query, e)
    
    found_items = list(all_items.values())
    logger.info("Combined search found %d unique potential articles. Processing...", len(found_items))
    
    processed_count = 0
    for item in found_items:
        if process_item(item):
            processed_count += 1
            
    if processed_count == 0:
        logger.info("No new reports found to process.")
        send_email(
            "iGaming Watcher: No New Reports Found",
            f"The daily scan completed at {datetime.now(timezone.utc).isoformat()} but did not find any new financial reports to process."
        )
    else:
        logger.info("Successfully processed %d new reports.", processed_count)

    logger.info("--- DAILY SCAN FINISHED ---")

if __name__ == "__main__":
    main_loop()
