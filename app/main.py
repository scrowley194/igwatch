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
    START_FROM_DAYS,
)

# --------------------------------------------------------------------
# Setup
# --------------------------------------------------------------------
logger = get_logger("igwatch")
state = State("data/seen.db")
DIV = "-" * 72

# Keywords for search queries
SECTOR_KEYWORDS = [
    "igaming", "online casino", "sports betting", "gambling technology",
    "igaming supplier", "sportsbook", "iCasino", "lottery"
]
FINANCIAL_NEWS_KEYWORDS = [
    # existing
    "financial results", "quarterly earnings", "quarterly update", "annual report",
    "guidance update", "earnings call", "Q1", "Q2", "Q3", "Q4", "H1", "H2", "FY",
    "first quarter", "second quarter", "third quarter", "fourth quarter",
    "full year", "half year", "interim report",
    # added (strict but broader industry wording)
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
    """Keep stories within the configured lookback window; allow unknown to pass."""
    if not published_ts:
        return True
    return published_ts >= (utc_ts() - START_FROM_DAYS * 86400)


def _results_like_patterns(title: str) -> bool:
    t = (title or "").lower()
    if any(k.lower() in t for k in FINANCIAL_NEWS_KEYWORDS):
        return True
    # common structural patterns in results headlines
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
    """Require at least one key metric to be present when REQUIRE_NUMBERS is True."""
    def ok(d):
        return isinstance(d, dict) and bool(d.get("current")) and str(d["current"]).strip().lower() not in ("not found", "n/a")
    return any(
        ok(result.get(k)) for k in ("revenue", "ebitda", "net_income", "eps")
    )

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

    # metrics
    r, e, n, s = (
        result.get("revenue", {}),
        result.get("ebitda", {}),
        result.get("net_income", {}),
        result.get("eps", {}),
    )

    def fmt_metric(name: str, d: dict) -> str | None:
        cur, yoy = (d.get("current") or "").strip(), (d.get("yoy") or "").strip()
        if cur.lower() in ("", "not found", "n/a"):
            return None
        return f"{name}: {cur}" + (f" | YoY {yoy}" if yoy and yoy.lower() != "n/a" else "")

    metrics = [
        m for m in (
            fmt_metric("Revenue", r),
            fmt_metric("EBITDA", e),
            fmt_metric("Net income", n),
            fmt_metric("EPS", s),
        ) if m
    ]
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
    Adds explicit logging for skip reasons so tuning remains easy but strict.
    """
    try:
        # Date/year/keyword gates (strict but transparent)
        if year_guard(item.title, item.url):
            logger.info("Skipping (too old year): %s", item.url)
            return False
        if not is_recent(item.published_ts):
            logger.info("Skipping (outside lookback window): %s", item.url)
            return False
        if not is_results_like(item.title):
            logger.info("Skipping (not results-like headline): %s — %s", item.title, item.url)
            return False

        item_id = state.make_id(item.source, item.url, item.title)
        if state.is_seen(item_id):
            logger.info("Skipping (already seen): %s", item.url)
            return False

        company_match = re.match(r"^([\w\s.&,()]+?)(?:\s\(|reports|announces)", item.title, re.IGNORECASE)
        email_company_name = company_match.group(1).strip() if company_match else "Unknown Company"

        result = fetch_and_summarize(item.url, title_hint=item.title)
        # If fetch_and_summarize returns None, it means the domain was blocked or failed downstream.
        if result is None:
            logger.info("Skipping (blocked/unsupported domain after redirect): %s", item.url)
            return False

        if REQUIRE_NUMBERS and not _has_numbers(result):
            logger.info("Skipping (no key financial numbers found): %s", result.get("final_url", item.url))
            return False

        subject = f"[{email_company_name}] {result.get('headline','')[:120]}"
        body = render_email(email_company_name, result.get("final_url", item.url), result)
        send_email(subject, body)
        state.mark_seen(item_id, utc_ts())
        time.sleep(1)  # gentle rate limit
        return True
    except Exception as e:
        logger.error("Parse/send error for %s: %s", item.url, e)
        return False


def main_loop():
    """
    Runs a single, comprehensive discovery scan.
    """
    logger.info("--- RUNNING DAILY DISCOVERY SCAN (%s days) ---", START_FROM_DAYS)

    queries = construct_queries()

    # Include both Google News queries and primary wire listing pages for higher recall
    watchers = [GoogleNewsWatcher(query) for query in queries] + [
        # Primary wire listings (strict-quality domains)
        PressWireWatcher("https://www.businesswire.com/portal/site/home/news/"),
        PressWireWatcher("https://www.globenewswire.com/"),
        PressWireWatcher("https://www.prnewswire.com/news-releases/news-releases-list/")
    ]

    all_items = {}
    for watcher in watchers:
        try:
            for item in watcher.poll():
                # de-dupe on final URL
                if item.url not in all_items:
                    all_items[item.url] = item
        except Exception as e:
            # Some watchers won't have .query attr — guard for that in log
            q = getattr(watcher, "query", getattr(watcher, "listing_url", watcher.__class__.__name__))
            logger.error("Watcher failed for '%s': %s", q, e)

    # Process newest first when we have timestamps
    found_items = list(all_items.values())
    found_items.sort(key=lambda it: it.published_ts or 0, reverse=True)

    logger.info("Combined search found %d unique potential articles. Processing...", len(found_items))

    processed_count = 0
    for item in found_items:
        if process_item(item):
            processed_count += 1

    if processed_count == 0:
        logger.info("No new reports found to process.")
        send_email(
            "iGaming Watcher: No New Reports Found",
            f"The scan completed at {datetime.now(timezone.utc).isoformat()} but did not find any new financial reports to process."
        )
    else:
        logger.info("Successfully processed %d new reports.", processed_count)

    logger.info("--- DAILY SCAN FINISHED ---")


if __name__ == "__main__":
    main_loop()
