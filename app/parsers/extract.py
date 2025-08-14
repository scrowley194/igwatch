import re, io
from typing import Dict, List, Tuple
from bs4 import BeautifulSoup
from pdfminer.high_level import extract_text as pdf_extract_text
import requests

CURRENCY = r"\$|€|£"

CONTRO_KEYS = [
    "guidance", "withdraw", "impairment", "restructur", "investigation",
    "material weakness", "restatement", "fine", "penalt", "sanction", "tax",
    "lawsuit", "litigation", "license", "licence", "regulator", "covenant",
    "going concern", "cyber", "breach", "aml", "responsible gambling",
    "rgc", "delay", "downgrade", "closure", "write-?down"
]

def _fetch_pdf_text(url: str) -> str:
    r = requests.get(url, timeout=60, headers={"User-Agent":"NEXT.io Earnings Watcher"})
    r.raise_for_status()
    b = io.BytesIO(r.content)
    return pdf_extract_text(b) or ""

def _html_main_text(html: str, base_url: str) -> str:
    soup = BeautifulSoup(html, "lxml")
    for el in soup(["script","style","noscript","header","footer","nav","aside"]):
        el.decompose()
    # Prefer article/main/content blocks
    candidates = soup.select("article, main, .content, #content, .c-article, .news-article, .post, .press-release")
    if not candidates:
        candidates = soup.select("section, .container, .wrapper, .content-area")
    # Choose the node with the most text
    best = None; best_len = 0
    for node in candidates or [soup.body or soup]:
        txt = node.get_text("\n", strip=True)
        if len(txt) > best_len:
            best_len = len(txt); best = txt
    text = best or soup.get_text("\n", strip=True)
    # collapse extra newlines
    text = re.sub(r"\n{2,}", "\n", text)
    return text

def _fetch_text(url: str) -> str:
    # If link is PDF, read it; otherwise read HTML and follow obvious "Download PDF" links once
    lower = url.lower()
    if lower.endswith(".pdf"):
        return _fetch_pdf_text(url)
    res = requests.get(url, timeout=30, headers={"User-Agent":"NEXT.io Earnings Watcher"})
    res.raise_for_status()
    html = res.text
    soup = BeautifulSoup(html, "lxml")
    # Try to find a PDF link inside and follow
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "pdf" in href.lower():
            pdf_url = requests.compat.urljoin(url, href)
            if pdf_url.lower().endswith(".pdf"):
                try:
                    return _fetch_pdf_text(pdf_url)
                except Exception:
                    pass
    return _html_main_text(html, url)

def _find_number_pair(text: str, label_regex: str) -> Tuple[str,str,str]:
    curr = prior = yoy = ""
    # Look for "Adjusted EBITDA" or "EBITDA" etc.
    reg = re.compile(rf"(?i)({label_regex})[^\n]*?({CURRENCY}\s?\d[\d,\.]*\s?(?:million|bn|b|m)?)")
    m = reg.search(text)
    if m:
        curr = m.group(2)
        span = text[m.end(): m.end()+500]
        # Prior-year number
        m2 = re.search(rf"(?i)(?:prior|last|previous|ly|yoy)[^\n\d$€£%]*({CURRENCY}\s?\d[\d,\.]*\s?(?:million|bn|b|m)?)", span)
        if m2:
            prior = m2.group(1)
        # YoY percentage
        m3 = re.search(r"(?i)([+−\-]?\d{1,3}(?:\.\d+)?\s?%)\s*yoy", span)
        if m3:
            yoy = m3.group(1) + " YoY"
    return curr, prior, yoy

def _first_sentences(text: str, n=2) -> str:
    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    return " ".join(parts[:n])[:500]

def _extract_breakdowns(text: str, kind: str) -> List[str]:
    lines = []
    hits = 0
    for ln in text.splitlines():
        low = ln.lower()
        if any(k in low for k in ["us","u.s.","united states","uk","italy","sweden","europe","row","rest of world","canada","australia","latam","germany","spain","france","denmark"]):
            if re.search(rf"{CURRENCY}\s?\d", ln) or re.search(r"\b\d{{1,3}}%\b", ln):
                lines.append(ln.strip()); hits += 1
        if any(k in low for k in ["sportsbook","sports betting","igaming","i-gaming","online casino","casino","poker","bingo","retail","land-based","lottery","interactive","gaming operations"]):
            if re.search(rf"{CURRENCY}\s?\d", ln) or re.search(r"\b\d{{1,3}}%\b", ln):
                lines.append(ln.strip()); hits += 1
        if hits >= 8:
            break
    return list(dict.fromkeys(lines))  # de-dup preserve order

def summarize(text: str, title_hint: str = "") -> Dict:
    headline = title_hint or (text.split("\n",1)[0][:160] if text else "")
    short = _first_sentences(text, 2)

    # Prefer Adjusted EBITDA if present
    e_curr, e_prior, e_yoy = _find_number_pair(text, "adjusted\s+ebitda|ebitda")
    r_curr, r_prior, r_yoy = _find_number_pair(text, "total\s+revenue|revenue|net\s+revenue|net\s+revenues")

    # Controversial points (short lines around flagged words)
    found=[]
    for ln in text.splitlines():
        low = ln.lower()
        if any(k in low for k in CONTRO_KEYS):
            if 30 <= len(ln) <= 220:
                found.append(ln.strip())
        if len(found) >= 5:
            break

    geo = _extract_breakdowns(text, "geo")
    prod = _extract_breakdowns(text, "product")

    return {
        "headline": headline.strip() or "Results Update",
        "short_summary": short or "No summary available.",
        "controversial_points": found[:5],
        "ebitda": {"current": e_curr or "Not found", "prior": e_prior or "Not found", "yoy": e_yoy or "YoY n/a"},
        "revenue": {"current": r_curr or "Not found", "prior": r_prior or "Not found", "yoy": r_yoy or "YoY n/a"},
        "geo_breakdown": geo or ["Not disclosed in release."],
        "product_breakdown": prod or ["Not disclosed in release."],
        "final_thoughts": "Early read: watch OSB/iCasino trajectory, regulation, and supplier order books; refine after full transcript."
    }

def fetch_and_summarize(url: str, title_hint: str = "") -> Dict:
    text = _fetch_text(url)
    return summarize(text, title_hint)
