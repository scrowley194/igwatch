import re
from urllib.parse import urlparse, urlencode
from io import BytesIO

import requests
from bs4 import BeautifulSoup
from pdfminer.high_level import extract_text

from ..config import BROWSER_UA, GOOD_WIRE_DOMAINS, BLOCK_DOMAINS, SCRAPING_API_KEY
from ..net import make_session

# --------------------------------------------------------------------
# Session Setup
# --------------------------------------------------------------------
SESSION = make_session()

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

def _norm(s: str) -> str:
    """Normalize whitespace and remove excessive newlines."""
    return re.sub(r"[\s\n]+", " ", (s or "").strip())

# --------------------------------------------------------------------
# Extraction Logic
# --------------------------------------------------------------------
def _extract_numbers(text: str) -> dict:
    """More robust heuristics for finding key financial data."""
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

    # Geography / Product breakdown (Improved to look for lines with money or percentages)
    for line in text.splitlines():
        line_norm = _norm(line)
        if len(line_norm) > 250: continue # Skip very long lines
        
        has_metric = re.search(_PCT, line) or re.search(_MONEY, line)
        if not has_metric: continue

        if re.search(r"\b(US|United States|UK|United Kingdom|Europe|Italy|Spain|Germany|Nordics|Canada|Australia|LatAm|APAC|North America)\b", line, re.I):
            out["geo_breakdown"].append(line_norm)
        if re.search(r"\b(OSB|Sportsbook|iCasino|Casino|Retail|B2B|B2C|Gaming|Lottery|Bingo|Poker|Slots|Live Dealer|Media)\b", line, re.I):
            out["product_breakdown"].append(line_norm)

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

    # **IMPROVEMENT**: Get a better summary by combining the first few paragraphs.
    summary_paragraphs = soup.select("article p, main p, .content p, #content p")
    summary = " ".join([_norm(p.get_text(" ", strip=True)) for p in summary_paragraphs[:3]])

    # **NEW**: Extract key highlights from bulleted lists.
    highlights = []
    list_items = soup.select("article li, main li, .content li, #content li")
    for item in list_items:
        text = _norm(item.get_text(" ", strip=True))
        if 15 < len(text) < 300: # Filter for meaningful, concise points
            highlights.append(text)

    text = soup.get_text("\n")
    data = _extract_numbers(text)

    return {
        "headline": headline or "Earnings/Results",
        "short_summary": summary or "Press release / results page",
        "key_highlights": highlights, # Add new highlights to the output
        **data,
        "final_thoughts": "Early read: watch OSB/iCasino trajectory, regulation, and supplier order books; refine after call transcript."
    }

def _summarize_pdf(text: str) -> dict:
    data = _extract_numbers(text)
    return {
        "headline": "Results presentation / PDF",
        "short_summary": "Key figures extracted from PDF.",
        "key_highlights": [], # PDFs are harder to parse for highlights
        **data,
        "final_thoughts": "PDF-only parse; verify against press release for context."
    }

# --------------------------------------------------------------------
# Main Entry
# --------------------------------------------------------------------
def fetch_and_summarize(url: str, title_hint: str = "") -> dict:
    if not SCRAPING_API_KEY:
        raise ValueError("SCRAPING_API_KEY is not set in the environment.")

    proxy_url = "http://api.scraperapi.com"
    params = {
        "api_key": SCRAPING_API_KEY,
        "url": url,
        "country_code": "us",
        "render": "true"
    }
    
    r = SESSION.get(proxy_url, params=params, timeout=90)
    r.raise_for_status()

    final_url = r.url
    host = _host(final_url)

    if host in BLOCK_DOMAINS:
        raise RuntimeError(f"Blocked domain: {host}")

    ctype = r.headers.get("Content-Type", "").lower()

    if "application/pdf" in ctype or final_url.lower().endswith(".pdf"):
        text = extract_text(BytesIO(r.content))
        return _summarize_pdf(text)

    return _summarize_html(final_url, r.text)
