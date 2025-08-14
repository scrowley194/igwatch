import re
from urllib.parse import urlparse
from io import BytesIO

import requests
from bs4 import BeautifulSoup
from pdfminer.high_level import extract_text

from ..config import BROWSER_UA, GOOD_WIRE_DOMAINS, BLOCK_DOMAINS
from ..net import make_session

# --------------------------------------------------------------------
# Session Setup
# --------------------------------------------------------------------
SESSION = make_session()
SESSION.headers.update({
    "User-Agent": BROWSER_UA,
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9"
})

# --------------------------------------------------------------------
# Regex Patterns
# --------------------------------------------------------------------
_MONEY = r"(?:[\$£€]\s?\d[\d,]*(?:\.\d+)?\s*(?:million|billion|m|bn)?|\d[\d,]*(?:\.\d+)?\s*(?:million|billion|m|bn))"
_PCT = r"(?:\+|\-)?\d+(?:\.\d+)?\s?%"

# --------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------
def _host(u: str) -> str:
    """Extract hostname without port."""
    return urlparse(u).netloc.split(":")[0].lower()

def _canonical_url(soup: BeautifulSoup, fallback: str) -> str:
    """Get canonical URL if present, else fallback."""
    c = soup.find("link", rel="canonical")
    return (c.get("href") if c else None) or fallback

def _norm(s: str) -> str:
    """Normalize whitespace."""
    return re.sub(r"\s+", " ", (s or "").strip())

# --------------------------------------------------------------------
# Extraction Logic
# --------------------------------------------------------------------
def _extract_numbers(text: str) -> dict:
    """Very simple heuristics for revenue/EBITDA and YoY metrics."""
    out = {
        "revenue": {"current": "Not found", "prior": "Not found", "yoy": "n/a"},
        "ebitda": {"current": "Not found", "prior": "Not found", "yoy": "n/a"},
        "geo_breakdown": [],
        "product_breakdown": [],
        "controversial_points": []
    }

    # Revenue
    if m := re.search(r"(?:revenue|net sales|turnover)[^.\n]*?(" + _MONEY + ")", text, flags=re.I):
        out["revenue"]["current"] = _norm(m.group(1))
    if y := re.search(r"(?:revenue)[^.\n]*?(" + _PCT + r")\s*(?:yoy|year[- ]over[- ]year|vs\.?\s*prior)", text, flags=re.I):
        out["revenue"]["yoy"] = _norm(y.group(1))

    # EBITDA
    if m := re.search(r"(?:adjusted\s*)?ebitda[^.\n]*?(" + _MONEY + ")", text, flags=re.I):
        out["ebitda"]["current"] = _norm(m.group(1))
    if y := re.search(r"ebitda[^.\n]*?(" + _PCT + r")\s*(?:yoy|year[- ]over[- ]year|vs\.?\s*prior)", text, flags=re.I):
        out["ebitda"]["yoy"] = _norm(y.group(1))

    # Geography / Product breakdown
    for line in text.splitlines():
        if re.search(r"\b(US|United States|UK|United Kingdom|Europe|Italy|Spain|Germany|Nordics|Canada|Australia|LatAm|APAC)\b", line, re.I) and re.search(_PCT, line):
            out["geo_breakdown"].append(_norm(line))
        if re.search(r"\b(OSB|Sportsbook|iCasino|Casino|Retail|B2B|B2C|Gaming|Lottery|Bingo|Poker|Slots|Live Dealer)\b", line, re.I) and re.search(_PCT, line):
            out["product_breakdown"].append(_norm(line))

    # Controversies
    for kw in ["regulatory", "investigation", "fine", "penalty", "data breach", "governance", "restatement"]:
        if re.search(rf"\b{re.escape(kw)}\b", text, re.I):
            out["controversial_points"].append(f"Mentions: {kw}")

    return out

# --------------------------------------------------------------------
# Summarizers
# --------------------------------------------------------------------
def _summarize_html(url: str, html: str) -> dict:
    soup = BeautifulSoup(html, "lxml")
    title = soup.find("h1") or soup.find("title")
    headline = _norm(title.get_text(" ", strip=True) if title else "")

    summary = ""
    if p := soup.select_one("article p, main p, .content p, #content p"):
        summary = _norm(p.get_text(" ", strip=True))

    text = soup.get_text("\n")
    data = _extract_numbers(text)

    return {
        "headline": headline or "Earnings/Results",
        "short_summary": summary or "Press release / results page",
        **data,
        "final_thoughts": "Early read: watch OSB/iCasino trajectory, regulation, and supplier order books; refine after call transcript."
    }

def _summarize_pdf(text: str) -> dict:
    data = _extract_numbers(text)
    return {
        "headline": "Results presentation / PDF",
        "short_summary": "Key figures extracted from PDF.",
        **data,
        "final_thoughts": "PDF-only parse; verify against press release for context."
    }

# --------------------------------------------------------------------
# Main Entry
# --------------------------------------------------------------------
def fetch_and_summarize(url: str, title_hint: str = "") -> dict:
    r = SESSION.get(url, timeout=35, allow_redirects=True)
    r.raise_for_status()

    final_url = r.url
    host = _host(final_url)

    if host in BLOCK_DOMAINS:
        raise RuntimeError(f"Blocked domain: {host}")

    ctype = r.headers.get("Content-Type", "").lower()

    # PDF Path
    if "application/pdf" in ctype or final_url.lower().endswith(".pdf"):
        # BytesIO ensures we can handle both direct bytes and remote file
        if isinstance(r.content, (bytes, bytearray)):
            text = extract_text(BytesIO(r.content))
        else:
            text = extract_text(final_url)
        return _summarize_pdf(text)

    # HTML Path
    return _summarize_html(final_url, r.text)
