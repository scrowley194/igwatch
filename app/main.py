import os, time, yaml, traceback
from typing import List, Dict
from datetime import datetime, timezone
from .utils.log import get_logger
from .utils.state import State
from .watchers.rss_watcher import RSSWatcher, RSSPageWatcher
from .watchers.page_watcher import PageWatcher
from .watchers.edgar_watcher import EdgarWatcher
from .parsers.extract import summarize, _fetch_text
from .config import POLL_SECONDS, DRY_RUN, MAIL_FROM, MAIL_TO

logger = get_logger("igwatch")
state = State("data/seen.db")

def utc_ts() -> int:
    return int(datetime.now(timezone.utc).timestamp())

def load_companies() -> List[Dict]:
    with open("config/companies.yml", "r") as f:
        data = yaml.safe_load(f)
    return data.get("companies", [])

def build_watchers(wcfg: Dict):
    t = wcfg.get("type")
    if t == "rss":
        return RSSWatcher(wcfg["url"])
    if t == "rss_page":
        return RSSPageWatcher(wcfg["url"])
    if t == "page":
        return PageWatcher(wcfg["url"])
    if t == "edgar_atom":
        return EdgarWatcher(wcfg["ticker"])
    raise ValueError(f"Unknown watcher type: {t}")

def render_email(company: str, src_url: str, result: Dict) -> str:
    lines = []
    lines.append(f"Company: {company}")
    lines.append(f"Source: {src_url}")
    lines.append("")
    lines.append(f"Headline: {result['headline']}")
    lines.append("")
    lines.append("Short summary:")
    lines.append(result["short_summary"] or "N/A")
    lines.append("")
    lines.append("Top 5 controversial points:")
    cps = result.get("controversial_points") or []
    if not cps:
        lines.append("- None detected in release.")
    else:
        for c in cps[:5]:
            lines.append(f"- {c}")
    lines.append("")
    e = result.get("ebitda", {})
    lines.append(f"EBITDA: {e.get('current','N/A')} vs prior {e.get('prior','N/A')} ({e.get('yoy','YoY N/A')})")
    r = result.get("revenue", {})
    lines.append(f"Revenue: {r.get('current','N/A')} vs prior {r.get('prior','N/A')} ({r.get('yoy','YoY N/A')})")
    lines.append("")
    lines.append("Geography breakdown (YoY):")
    for g in result.get("geo_breakdown", []):
        lines.append(f"- {g}")
    lines.append("")
    lines.append("Product breakdown (YoY):")
    for p in result.get("product_breakdown", []):
        lines.append(f"- {p}")
    lines.append("")
    lines.append("Final thoughts:")
    lines.append(result.get("final_thoughts", ""))
    lines.append("")
    lines.append("— NEXT.io iGaming Earnings Watcher")
    return "\n".join(lines)

def send_email(subject: str, body: str):
    if DRY_RUN:
        logger.info("[DRY_RUN] Would send: %s", subject)
        logger.info("\n%s", body)
        return
    # Prefer Graph
    try:
        from .emailers.graph_mailer import send_plaintext as graph_send
        graph_send(subject, body, MAIL_TO)
        logger.info("Email sent via Graph to %s", MAIL_TO)
    except Exception as e:
        logger.error("Graph send failed (%s). Falling back to SMTP OAuth...", e)
        try:
            from .emailers.smtp_oauth import send_plaintext as smtp_send
            smtp_send(subject, body, MAIL_TO)
            logger.info("Email sent via SMTP OAuth to %s", MAIL_TO)
        except Exception as e2:
            logger.error("SMTP OAuth send failed: %s", e2)

def main_loop():
    companies = load_companies()
    watchers = []
    for c in companies:
        for w in c.get("watchers", []):
            try:
                watchers.append((c["name"], build_watchers(w)))
            except Exception as e:
                logger.error("Watcher build failed for %s: %s", c["name"], e)

    logger.info("Loaded %d watchers across %d companies. Poll=%ss DRY_RUN=%s",
                len(watchers), len(companies), POLL_SECONDS, DRY_RUN)

    while True:
        for cname, watcher in watchers:
            try:
                for item in watcher.poll():
                    item_id = state.make_id(item.source, item.url, item.title)
                    if state.is_seen(item_id):
                        continue
                    # fetch text & summarize
                    try:
                        text = _fetch_text(item.url)
                        result = summarize(text, title_hint=item.title)
                        subject = f"[{cname}] {result['headline'][:120]}"
                        body = render_email(cname, item.url, result)
                        send_email(subject, body)
                        state.mark_seen(item_id, utc_ts())
                        time.sleep(0.5)  # polite
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
