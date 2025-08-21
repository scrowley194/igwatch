"""
Email rendering helpers for igwatch.


Public API:
- render_subject(payload: dict) -> str
- render_body(payload: dict) -> str


These functions expect the payload shape produced by
app.parsers.extract.fetch_and_summarize.
"""
from __future__ import annotations
from typing import Dict, List




def render_subject(p: Dict[str, object]) -> str:
"""Compose a concise email subject from the parsed payload."""
h = str(p.get("headline", "Update")).strip() or "Update"
return f"[Earnings Watch] {h}"




def _format_metrics(p: Dict[str, object]) -> List[str]:
lines: List[str] = []
for key, label in [
("revenue", "Revenue"),
("ebitda", "Adj. EBITDA"),
("net_income", "Net income"),
("eps", "EPS"),
]:
m = p.get(key)
if isinstance(m, dict) and (m.get("current") or m.get("yoy")):
cur = (m.get("current") or "").strip()
yoy = (m.get("yoy") or "").strip()
val = cur
if yoy:
val = f"{val} ({yoy})" if val else f"({yoy})"
if val:
lines.append(f"{label}: {val}")
return lines




def render_body(p: Dict[str, object]) -> str:
"""Render a plaintext email body with headline, link, bullets and KPIs."""
head = str(p.get("headline", "")).strip()
url = str(p.get("final_url", "")).strip()
short = str(p.get("short_summary", "")).strip()
bullets: List[str] = [str(b).strip() for b in p.get("key_highlights", []) if str(b).strip()]
metrics = _format_metrics(p)
notes = str(p.get("final_thoughts", "")).strip()


lines: List[str] = []
if head:
lines.append(f"Headline: {head}")
if url:
lines.append(f"URL: {url}")


if short:
lines.append("")
lines.append(f"Summary: {short}")


if bullets:
lines.append("")
lines.append("Key Highlights:")
lines.extend([f" - {b}" for b in bullets])


if metrics:
lines.append("")
lines.append("Metrics:")
lines.extend([f" - {m}" for m in metrics])


return "\n".join(lines) + "\n"
