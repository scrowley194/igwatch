from urllib.parse import urlparse
from typing import List, Dict

def render_email(company: str, src_url: str, result: Dict) -> str:
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

    # --- New EBITDA/Revenue formatting ---
    e = result.get("ebitda", {})
    r = result.get("revenue", {})

    def _fmt_metric(name, d):
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
# Main loop
# --------------------------------------------------------------------
def main_loop():
    companies = load_companies()
    watchers = []
    for c in companies:
        for w in c.get("watchers", []):
            try:
                watchers.append((c, build_watcher(w)))  # store whole company dict
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
                    # --- domain filter ---
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
