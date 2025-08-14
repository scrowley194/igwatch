import os
import time
import yaml
import re
import logging
from datetime import datetime, timezone
from urllib.parse import urlparse

from .utils.log import get_logger
from .utils.state import State
from .watchers.rss_watcher import RSSWatcher, RSSPageWatcher
from .watchers.page_watcher import PageWatcher
from .watchers.edgar_watcher import EdgarWatcher
from .parsers.extract import fetch_and_summarize
from .config import (
    POLL_SECONDS,
    DRY_RUN,
    MAIL_FROM,
    MAIL_TO,
    START_FROM_DAYS,
    STRICT_EARNINGS_KEYWORDS,
    ENABLE_EDGAR,
    REQUIRE_NUMBERS
)
from .emailers import smtp_oauth
from .watchers.press_wires import PressWireWatcher, GoogleNewsWatcher

# --------------------------------------------------------------------
# Setup
# --------------------------------------------------------------------
logger = get_logger("igwatch")
state = State("data/seen.db")

RESULT_KEYWORDS = [
    "q1", "q2", "q3", "q4",
    "quarter", "earnings", "results",
    "trading update", "interim", "half-year",
    "half year", "interim report"
]
DIV = "-" * 72

# --------------------------------------------------------------------
# Helper functions
# --------------------------------------------------------------------
def utc_ts() -> int:
    return int(datetime.now(timezone.utc).timestamp())

def load_companies() -> list[dict]:
    with open("config/companies.yml", "r") as f:
        data = yaml.safe_load(f)
    return data.get("companies", [])

def build_watcher(wcfg: dict):
    t = wcfg.get("type")
    if t == "rss":
        return RSSWatcher(wcfg["url"], allowed_domains=wcfg.get("allowed_domains"))
    if t == "rss_page":
        return RSSPageWatcher(wcfg["url"])
    if t == "page":
        # NEW: pass optional per-watcher allowlist and follow_detail flag
        return PageWatcher(
            wcfg["url"],
            allowed_domains=wcfg.get("allowed_domains"),
            follow_detail=wcfg.get("follow_detail", False),
        )
    if t == "edgar_atom":
        if not ENABLE_EDGAR:
            raise ValueError("EDGAR disabled by config")
        return EdgarWatcher(wcfg["ticker"])
    if t == "wire":
        # keep if you use PressWireWatcher
        return PressWireWatcher(wcfg["url"])
    raise ValueError(f"Unknown watcher type: {t}")


def is_recent(published_ts: int | None) -> bool:
    if not published_ts:
        return True
    cutoff = utc_ts() - START_FROM_DAYS * 86400
    return published_ts >= cutoff

def is_results_like(title: str, url: str) -> bool:
    t = (title or "").lower()
    if not STRICT_EARNINGS_KEYWORDS:
        return True
    return any(k in t for k in RESULT_KEYWORDS)

def year_guard(title: str, url: str) -> bool:
    years = [int(y) for y in re.findall(r"(19|20)\d{2}", f"{title} {url}") if len(y) >= 4]
    if not years:
        return False
    latest = max(years)
    cur = datetime.now().year
    return latest <= cur - 2

def _has_numbers(result: dict) -> bool:
    def _ok(d): 
        if not isinstance(d, dict): return False
        v = (d.get("current") or "").strip().lower()
        return bool(v) and v not in ("not found","n/a")
    return _ok(result.get("revenue")) or _ok(result.get("ebitda"))

# --------------------------------------------------------------------
# Email rendering
# --------------------------------------------------------------------
def render_email(company: str, src_url: str, result: dict) -> str:
    lines = [
        f"Company: {company}",
        f"Source: {src_url}",
        DIV,
        f"Headline: {result['headline']}",
        DIV,
        "Summary:",
        result["short_summary"],
        DIV,
        "Top 5 controversial points:"
    ]

    cps = result.get("controversial_points") or []
    if not cps:
        lines.append("- None detected.")
    else:
        for c in cps[:5]:
            lines.append(f"- {c}")

    lines.append(DIV)

    # EBITDA / Revenue formatting
    e = result.get("ebitda", {})
    r = result.get("revenue", {})

    def _fmt_metric(name: str, d: dict) -> str | None:
        cur = (d.get("current") or "").strip()
        yoy = (d.get("yoy") or "").strip()
        if cur.lower() in ("", "not found", "n/a"):
            return None
        return f"{name}: {cur}" + (f" | YoY {yoy}" if yoy and yoy.lower() != "n/a" else "")

    m = []
    x = _fmt_metric("Revenue", r)
    if x: m.append(x)
    x = _fmt_metric("EBITDA", e)
    if x: m.append(x)
    if m:
        lines.extend(m)
        lines.append(DIV)

    lines.append("Geography breakdown (YoY):")
    for g in result.get("geo_breakdown", []):
        lines.append(f"- {g}")

    lines.append(DIV)
    lines.append("Product breakdown (YoY):")
    for p in result.get("product_breakdown", []):
        lines.append(f"- {p}")

    lines.append(DIV)
    lines.append("Final thoughts:")
    lines.append(result.get("final_thoughts", ""))

    lines.append(DIV)
    lines.append("— NEXT.io iGaming Earnings Watcher")

    return "\n".join(lines)

# --------------------------------------------------------------------
# Email sending
# --------------------------------------------------------------------
def send_email(subject: str, body: str):
    if DRY_RUN:
        logger.info("[DRY_RUN] Email from %s to %s\nSubject: %s\n\n%s", MAIL_FROM, MAIL_TO, subject, body)
        return
    try:
        smtp_oauth.send_plaintext(subject, body, MAIL_TO)
        logger.info("Email sent successfully from %s to %s", MAIL_FROM, MAIL_TO)
    except Exception as e:
        logger.error("SMTP send failed: %s", e)

# --------------------------------------------------------------------
# Main loop
# --------------------------------------------------------------------
def main_loop():
    companies = load_companies()
    watchers = []

    for c in companies:
        for w in c.get("watchers", []):
            try:
                watchers.append((c, build_watcher(w)))  # store company dict for domain filtering
            except Exception as e:
                logger.error("Watcher build failed for %s: %s", c["name"], e)

    logger.info(
        "Loaded %d watchers across %d companies. Poll=%ss DRY_RUN=%s START_FROM_DAYS=%s STRICT=%s",
        len(watchers), len(companies), POLL_SECONDS, DRY_RUN, START_FROM_DAYS, STRICT_EARNINGS_KEYWORDS
    )

    while True:
        for c, watcher in watchers:
            cname = c["name"]
            allowed = set([d.lower() for d in c.get("allowed_domains", [])])

            try:
                for item in watcher.poll():
                    # domain filter
                    if allowed:
                        netloc = urlparse(item.url).netloc.lower()
                        if not any(netloc == d or netloc.endswith("." + d) for d in allowed):
                            continue

                    if year_guard(item.title, item.url):
                        continue
                    if not is_recent(item.published_ts):
                        continue
                    if not is_results_like(item.title, item.url):
                        continue

                    item_id = state.make_id(item.source, item.url, item.title)
                    if state.is_seen(item_id):
                        continue

                    try:
                        result = fetch_and_summarize(item.url, title_hint=item.title)
                        if REQUIRE_NUMBERS and not _has_numbers(result):
                            logger.info("Skip email (no numbers found) for %s", item.url)
                            continue

                        subject = f"[{cname}] {result['headline'][:120]}"
                        body = render_email(cname, item.url, result)
                        send_email(subject, body)
                        state.mark_seen(item_id, utc_ts())
                        time.sleep(0.5)

                    except Exception as e:
                        logger.error("Parse/send error for %s: %s", item.url, e)

            except Exception as e:
                logger.error("Watcher error for %s: %s", cname, e)

        time.sleep(POLL_SECONDS)

if __name__ == "__main__":
    try:
        main_loop()
    except KeyboardInterrupt:
        logger.info("Stopped.")
