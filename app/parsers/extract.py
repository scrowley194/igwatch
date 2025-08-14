import re
from urllib.parse import urlparse
import requests
from bs4 import BeautifulSoup
from pdfminer.high_level import extract_text
from ..config import BROWSER_UA, GOOD_WIRE_DOMAINS, BLOCK_DOMAINS

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": BROWSER_UA,
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9"
})

_MONEY = r"(?:[\$£€]\s?\d[\d,]*(?:\.\d+)?\s*(?:million|billion|m|bn)?|\d[\d,]*(?:\.\d+)?\s*(?:million|billion|m|bn))"
_PCT   = r"(?:\+|\-)?\d+(?:\.\d+)?\s?%"

def _host(u: str) -> str:
    return urlparse(u).netloc.split(":")[0].lower()

def _canonical_url(soup: BeautifulSoup, fallback: str) -> str:
    c = soup.find("link", rel="canonical")
    href = (c.get("href") if c else None) or fallback
    return href

def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip())

def _extract_numbers(text: str) -> dict:
    # Very simple heuristics for revenue/EBITDA and YoY
    out = {
        "revenue": {"current": "Not found", "prior": "Not found", "yoy": "n/a"},
        "ebitda":  {"current": "Not found", "prior": "Not found", "yoy": "n/a"},
        "geo_breakdown": [],
        "product_breakdown": [],
        "controversial_points": []
    }
    t = text

    # revenue
    m = re.search(r"(?:revenue|net sales|turnover)[^.\n]*?(" + _MONEY + ")", t, flags=re.I)
    if m:
        out["revenue"]["current"] = _norm(m.group(1))
    y = re.search(r"(?:revenue)[^.\n]*?(" + _PCT + r")\s*(?:yoy|year[- ]over[- ]year|vs\.?\s*prior)", t, flags=re.I)
    if y:
        out["revenue"]["yoy"] = _norm(y.group(1))

    # ebitda
    m = re.search(r"(?:adjusted\s*)?ebitda[^.\n]*?(" + _MONEY + ")", t, flags=re.I)
    if m:
        out["ebitda"]["current"] = _norm(m.group(1))
    y = re.search(r"ebitda[^.\n]*?(" + _PCT + r")\s*(?:yoy|year[- ]over[- ]year|vs\.?\s*prior)", t, flags=re.I)
    if y:
        out["ebitda"]["yoy"] = _norm(y.group(1))

    # geo / product bullets
    for line in t.splitlines():
        if re.search(r"\b(US|United States|UK|United Kingdom|Europe|Italy|Spain|Germany|Nordics|Canada|Australia|LatAm|APAC)\b", line, re.I) and re.search(_PCT, line):
            out["geo_breakdown"].append(_norm(line))
        if re.search(r"\b(OSB|Sportsbook|iCasino|Casino|Retail|B2B|B2C|Gaming|Lottery|Bingo|Poker|Slots|Live Dealer)\b", line, re.I) and re.search(_PCT, line):
            out["product_breakdown"].append(_norm(line))

    # controversies
    for kw in ["regulatory", "investigation", "fine", "penalty", "data breach", "governance", "restatement"]:
        if re.search(rf"\b{re.escape(kw)}\b", t, re.I):
            out["controversial_points"].append(f"Mentions: {kw}")

    return out

def _summarize_html(url: str, html: str) -> dict:
    soup = BeautifulSoup(html, "lxml")
    title = soup.find("h1") or soup.find("title")
    headline = _norm(title.get_text(" ", strip=True) if title else "")
    summary = ""
    p = soup.select_one("article p, main p, .content p, #content p")
    if p:
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

def fetch_and_summarize(url: str, title_hint: str = "") -> dict:
    r = SESSION.get(url, timeout=35, allow_redirects=True)
    r.raise_for_status()
    final_url = r.url
    host = _host(final_url)
    if host in BLOCK_DOMAINS:
        raise RuntimeError(f"Blocked domain: {host}")

    ctype = r.headers.get("Content-Type", "").lower()
    if "application/pdf" in ctype or final_url.lower().endswith(".pdf"):
        text = extract_text(r.content if isinstance(r.content, (bytes, bytearray)) else None) \
               if isinstance(r.content, (bytes, bytearray)) else extract_text(final_url)
        return _summarize_pdf(text)

    html = r.text
    return _summarize_html(final_url, html)
