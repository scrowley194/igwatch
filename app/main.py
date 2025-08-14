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
from .watchers.press_wires import PressWireWatcher
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
# Helpers
# --------------------------------------------------------------------
def utc_ts() -> int:
    return int(datetime.now(timezone.utc).timestamp())

def load_companies() -> list[dict]:
    with open("config/companies.yml", "r") as f:
        return yaml.safe_load(f).get("companies", [])

def build_watcher(wcfg: dict):
    wtype = wcfg.get("type")
    if wtype == "rss":
        return RSSWatcher(wcfg["url"], allowed_domains=wcfg.get("allowed_domains"))
    if wtype == "rss_page":
        return RSSPageWatcher(wcfg["url"])
    if wtype == "page":
        # NOTE: no follow_detail arg here (your PageWatcher doesn't accept it)
        return PageWatcher(
            wcfg["url"],
            allowed_domains=wcfg.get("allowed_domains"),
        )
    if wtype == "edgar_atom":
        if not ENABLE_EDGAR:
            # Return None so we can skip cleanly during list build
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
    return any(k in (title or "").lower() for k in RESULT_KEYWORDS)

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
    """If GOOD_WIRE_DOMAINS is set, restrict to those hosts (e.g., BusinessWire/GlobeNewswire)."""
    if not GOOD_WIRE_DOMAINS:
        return True
    netloc = urlparse(url).netloc.lower()
    return any(netloc == d or netloc.endswith("." + d) for d in GOOD_WIRE_DOMAINS)

# --------------------------------------------------------------------
# Email rendering
# --------------------------------------------------------------------
def render_email(company: str, src_url: str, result: dict) -> str:
    lines = [
        f"Company: {company}",
        f"Source: {src_url}",
        DIV,
        f"Headline: {result.get('headline','')}",
        DIV,
        "Summary:",
        result.get("short_summary",""),
        DIV,
        "Top 5 controversial points:"
    ]
    cps = result.get("controversial_points") or []
    lines.extend([f"- {c}" for c in cps[:5]] if cps else ["- None detected."])

    e, r = result.get("ebitda", {}), result.get("revenue", {})
    def fmt_metric(name: str, d: dict) -> str | None:
        cur, yoy = (d.get("current") or "").strip(), (d.get("yoy") or "").strip()
        if cur.lower() in ("", "not found", "n/a"):
            return None
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

# --------------------------------------------------------------------
# Email sending
# --------------------------------------------------------------------
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
# Main loop
# --------------------------------------------------------------------
def main_loop():
    companies = load_companies()

    # Build watchers list safely (skip None/errored)
    watchers: list[tuple[dict, object]] = []
    for c in companies:
        for w in c.get("watchers", []):
            try:
                obj = build_watcher(w)
                if obj is not None:
                    watchers.append((c, obj))
            except Exception as e:
                logger.error("Watcher build failed for %s: %s", c.get("name","?"), e)

    logger.info(
        "Loaded %d watchers across %d companies. Poll=%ss DRY_RUN=%s START_FROM_DAYS=%s STRICT=%s",
        len(watchers), len(companies), POLL_SECONDS, DRY_RUN, START_FROM_DAYS, STRICT_EARNINGS_KEYWORDS
    )

    while True:
        for c, watcher in watchers:
            cname = c["name"]
            allowed_domains = set(map(str.lower, c.get("allowed_domains", [])))
            try:
                for item in watcher.poll():
                    if not domain_allowed(item.url, allowed_domains):
                        continue
                    if not in_good_sources(item.url):
                        continue
                    if year_guard(item.title, item.url):
                        continue
                    if not is_recent(item.published_ts):
                        continue
                    if not is_results_like(item.title):
                        continue

                    item_id = state.make_id(item.source, item.url, item.title)
                    if state.is_seen(item_id):
                        continue

                    try:
                        result = fetch_and_summarize(item.url, title_hint=item.title)
                        if REQUIRE_NUMBERS and not _has_numbers(result):
                            logger.info("Skipping (no numbers): %s", item.url)
                            continue
                        subject = f"[{cname}] {result.get('headline','')[:120]}"
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
