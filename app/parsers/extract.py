import re
from typing import Dict, List, Tuple
from bs4 import BeautifulSoup
from pdfminer.high_level import extract_text as pdf_extract_text
import requests

CURRENCY = r"\$|€|£"

# Define controversial keywords
CONTRO_KEYS = [
    "guidance", "impairment", "restructur", "investigation", "material weakness",
    "restatement", "fine", "penalt", "tax", "lawsuit", "litigation", "license",
    "licence", "regulator", "covenant", "going concern", "margin pressure",
    "breach", "cyber", "data breach", "aml", "responsible gambling", "rgc"
]

def _fetch_text(url: str) -> str:
    if url.lower().endswith(".pdf"):
        return pdf_extract_text(url)
    # HTML fallback
    res = requests.get(url, timeout=30, headers={"User-Agent": "NEXT.io Earnings Watcher"})
    res.raise_for_status()
    soup = BeautifulSoup(res.text, "lxml")
    for t in soup.find_all("a", href=True):
        # Follow obvious "PDF" press release links if present
        if t.get_text(" ", strip=True).lower().endswith(".pdf") or "pdf" in (t.get("href") or "").lower():
            href = requests.compat.urljoin(url, t["href"])
            if href.lower().endswith(".pdf"):
                try:
                    return pdf_extract_text(href)
                except Exception:
                    pass
    # Otherwise return readable text
    for el in soup(["script","style","noscript"]):
        el.decompose()
    return soup.get_text("\n", strip=True)

def _find_number_pair(text: str, label: str) -> Tuple[str,str,str]:
    """Try to find 'label of $X vs $Y' patterns; returns (current, prior, yoy%)."""
    patt = rf"{label}[^\n]*?({CURRENCY}\s?\d[\d,\.]*\s?(?:million|bn|b)?)"
    curr = prior = yoy = ""
    m = re.search(patt, text, flags=re.I)
    if m:
        curr = m.group(1)
        # look around for prior and YoY
        span_text = text[m.start(): m.start()+500]
        m2 = re.search(r"(?:(?:prior|last|previous)[- ]year[^\d%$]*|yoy[^\d%$]*)(" + CURRENCY + r"\s?\d[\d,\.]*\s?(?:million|bn|b)?)", span_text, flags=re.I)
        if m2:
            prior = m2.group(1)
        m3 = re.search(r"(\+|−|-)?\d{1,3}\.?\d?\s?%\s?yoy", span_text, flags=re.I)
        if m3:
            yoy = m3.group(0)
    return curr, prior, yoy

def _extract_breakdowns(text: str, kind: str) -> List[str]:
    lines = []
    # naive scan for region/product lines with figures and YoY
    for ln in text.splitlines():
        if any(k in ln.lower() for k in ["us", "u.s.", "uk", "italy","sweden","europe","row","rest of world","online casino","igaming","sportsbook","retail"]):
            if re.search(rf"{CURRENCY}\s?\d", ln) or re.search(r"\b\d{{1,3}}%\b", ln):
                lines.append(ln.strip())
        if len(lines) >= 8:
            break
    return lines

def summarize(text: str, title_hint: str = "") -> Dict:
    # Headline & short summary
    headline = title_hint or (text.split("\n",1)[0][:160] if text else "")
    # use first 2 sentences as summary
    parts = re.split(r"(?<=[.!?])\s+", text)
    short = " ".join(parts[:2]).strip()

    # EBITDA & Revenue
    e_curr, e_prior, e_yoy = _find_number_pair(text, "adjusted ebitda|ebitda")
    r_curr, r_prior, r_yoy = _find_number_pair(text, "revenue|net revenue|net revenues|total revenue")

    # Controversial points
    found = []
    for ln in text.splitlines():
        low = ln.lower()
        if any(k in low for k in CONTRO_KEYS):
            # keep reasonably sized lines
            if 40 <= len(ln) <= 240:
                found.append(ln.strip())
        if len(found) >= 5:
            break

    geo = _extract_breakdowns(text, "geo")
    prod = _extract_breakdowns(text, "product")

    return {
        "headline": headline,
        "short_summary": short,
        "controversial_points": found[:5],
        "ebitda": {"current": e_curr, "prior": e_prior, "yoy": e_yoy},
        "revenue": {"current": r_curr, "prior": r_prior, "yoy": r_yoy},
        "geo_breakdown": geo or ["Not disclosed in release."],
        "product_breakdown": prod or ["Not disclosed in release."],
        "final_thoughts": "Early read: watch OSB/iCasino seasonality, regulatory updates, and supplier order books; revise after transcript."
    }
