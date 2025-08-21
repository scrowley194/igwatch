#!/usr/bin/env python3
# Adjust path if running from repo root
if __name__ == "__main__":
sys.path.insert(0, os.path.abspath("."))


from app.utils.log import get_logger
from app.utils.state import State
from app.watchers.sec_edgar import SecEdgarWatcher
from app.watchers.rns_lse import RnsLseWatcher
from app.watchers.ir_sources import IrSourcesWatcher
from app.parsers.extract import fetch_and_summarize
from app.emailers.templates import render_subject, render_body


log = get_logger("smoke")


ENABLE_EDGAR = os.getenv("ENABLE_EDGAR", "true").lower() == "true"
ENABLE_LSE = os.getenv("ENABLE_LSE", "true").lower() == "true"
ENABLE_IR = os.getenv("ENABLE_IR", "true").lower() == "true"
START_FROM_DAYS = int(os.getenv("START_FROM_DAYS", "45"))
MAX_PARSE = int(os.getenv("SMOKE_MAX_PARSE", "3"))




def main() -> int:
log.info("Starting smoke test (no emails will be sent)")


watchers = []
if ENABLE_EDGAR:
watchers.append(SecEdgarWatcher(start_days=START_FROM_DAYS))
if ENABLE_LSE:
watchers.append(RnsLseWatcher(start_days=START_FROM_DAYS))
if ENABLE_IR:
watchers.append(IrSourcesWatcher(start_days=START_FROM_DAYS))


found = []
for w in watchers:
log.info("Checking %s", w.__class__.__name__)
try:
items = w.poll()
except Exception as e:
log.exception("Watcher failed: %s", w.__class__.__name__)
items = []
for url, title in (items or [])[:10]: # preview up to 10 links per watcher
print(f"- {w.__class__.__name__}: {title} -> {url}")
found.append((url, title))


# Parse a few items and print the email preview
print("\n--- Parser previews ---\n")
for url, title in found[:MAX_PARSE]:
try:
payload = fetch_and_summarize(url, title_hint=title)
except Exception:
log.exception("Parser failed for %s", url)
continue
subject = render_subject(payload)
body = render_body(payload)
print(subject)
print(textwrap.indent(body, prefix=" "))
print("-" * 72)


print("Done. If this looks good, run app/main.py in CI to send emails.")
return 0




if __name__ == "__main__":
raise SystemExit(main())
