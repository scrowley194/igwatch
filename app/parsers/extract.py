# app/parsers/extract.py
# Fetch a primary-source document (HTML or PDF), extract key KPIs, detect the
# reporting period, and compose a concise summary payload for email.
#
# Public API:
#   fetch_and_summarize(url: str, title_hint: str | None = None) -> dict
#
# Output keys used by the existing email template:
#   - headline: str
#   - final_url: str
#   - short_summary: str
#   - key_highlights: List[str]
#   - revenue / ebitda / net_income / eps: {"current": str, "yoy": str}
#   - final_thoughts: Optional[str]
#
# Dependencies: requests, beautifulsoup4, lxml, pdfminer.six, python-dateutil

from __future__ import annotations
import io
import os
import re
import time
import html
import logging
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlparse, urljoin

import requests
from bs4 import BeautifulSoup
from dateutil import parser as dateparser
from pdfminer.high_level import extract_text as pdf_extract_text

LOG = logging.getLogger("igwatch")

# ------------------------------- Config -------------------------------
DEFAULT_UA = (
    os.getenv("SEC_USER_AGENT")
    or os.getenv("IR_USER_AGENT")
    or os.getenv("LSE_USER_AGENT")
    or "igwatch (contact: support@example.com)"
)
HTTP_MAX_RETRIES = int(os.getenv("HTTP_MAX_RETRIES", "4"))
HTTP_POLITE_DELAY = float(os.getenv("HTTP_POLITE_DELAY", "0.2"))

# Optional: very light HTML junk removal (kept local so we don't depend on other modules)
JUNK_SELECTORS = os.getenv(
    "JUNK_SELECTORS",
    "nav,footer,header,aside,script,style,form,.ad,.advert,[class*='ad-'],.promo,.newsletter,.subscribe,.related,.social,.share,.breadcrumbs,.tags,.paywall,.cookie,.disclaimer,#comments",
)

# ------------------------------ HTTP ---------------------------------

class _Http:
    def __init__(self, ua: Optional[str] = None, max_retries: int = HTTP_MAX_RETRIES, polite_delay: float = HTTP_POLITE_DELAY):
        self.s = requests.Session()
        self.s.headers.update({
            "User-Agent": ua or DEFAULT_UA,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,application/pdf;q=0.9,*/*;q=0.8",
            "Accept-Encoding": "gzip, deflate",
            "Connection": "keep-alive",
        })
        self.max_retries = max_retries
        self.polite_delay = polite_delay

    def get(self, url: str, **kw) -> requests.Response:
        backoff = 0.5
        last = None
        for _ in range(self.max_retries):
            r = self.s.get(url, timeout=40, **kw)
            if r.status_code in (403, 429):
                sleep = float(r.headers.get("Retry-After") or backoff)
                LOG.debug("HTTP backoff %ss for %s (%s)", sleep, url, r.status_code)
                time.sleep(sleep)
                backoff = min(backoff * 2, 8.0)
                last = r
                continue
            r.raise_for_status()
            time.sleep(self.polite_delay)
            return r
        if last is not None:
            last.raise_for_status()
        raise RuntimeError("HTTP request failed and no response to raise")


# ---------------------------- Text Utils ------------------------------

_CURRENCY = r"[$£€]"
_NUM = r"-?\d{1,3}(?:[,\s]\d{3})*(?:\.\d+)?"  # 1,234.56 or 1234 or -1.2
_UNIT = r"(?:billion|bn|millions?|mn|m|thousand|k)\b"
_PCT = r"-?\d{1,3}(?:\.\d+)?\s?%"

RE_WHITESPACE = re.compile(r"\s+")

METRIC_PATTERNS = {
    "revenue": re.compile(rf"\b(total\s+)?revenue\b[^\n\r]{{0,160}}(({_CURRENCY}\s*)?{_NUM}(?:\s*{_UNIT})?)", re.I),
    "ebitda": re.compile(rf"\b(adjusted\s+)?ebitda\b[^\n\r]{{0,160}}(({_CURRENCY}\s*)?{_NUM}(?:\s*{_UNIT})?)", re.I),
    "net_income": re.compile(rf"\b(net\s+(income|loss))\b[^\n\r]{{0,160}}(({_CURRENCY}\s*)?{_NUM}(?:\s*{_UNIT})?)", re.I),
    "eps": re.compile(rf"\b(adjusted\s+)?(diluted\s+)?eps\b[^\n\r]{{0,120}}(({_CURRENCY}\s*)?{_NUM})", re.I),
}

YOY_PAT = re.compile(rf"(yoy|year[-\s]?over[-\s]?year|vs\.\s*prior\s*year|prior\s*year)[^\n\r]{{0,40}}((up|down|increase|decrease|grew|rose|fell)\s+)?({_PCT})", re.I)
PCT_VERB_PAT = re.compile(rf"\b(up|down|increase(?:d)?|decrease(?:d)?|grew|rose|fell)\s+({_PCT})\b", re.I)

PERIOD_PATTERNS = [
    re.compile(r"\b(Q[1-4])\s*(20\d{2})\b", re.I),
    re.compile(r"\b(H[12])\s*(20\d{2})\b", re.I),
    re.compile(r"\b(first|second|third|fourth)\s+quarter\s+(20\d{2})\b", re.I),
    re.compile(r"\b(full[-\s]?year|FY)\s*(20\d{2})\b", re.I),
]

GUIDANCE_PAT = re.compile(r"\b(guidance|outlook|reaffirm|raise[sd]?|lower[sd]?|update[sd]? guidance)\b[^\n\r]{0,200}", re.I)


def _squash_spaces(s: str) -> str:
    return RE_WHITESPACE.sub(" ", s).strip()


def _clean_html(html_text: str) -> str:
    soup = BeautifulSoup(html_text, "lxml")
    # Drop junk blocks
    for sel in JUNK_SELECTORS.split(","):
        try:
            for node in soup.select(sel.strip()):
                node.decompose()
        except Exception:
            continue
    text = soup.get_text(" \n ")
    return _squash_spaces(html.unescape(text))


def _extract_pdf_text(data: bytes) -> str:
    try:
        txt = pdf_extract_text(io.BytesIO(data)) or ""
        return _squash_spaces(txt)
    except Exception as e:
        LOG.warning("PDF text extraction failed: %s", e)
        return ""


# ---------------------------- Parsing KPIs ----------------------------

@dataclass
class Metric:
    current: Optional[str] = None
    yoy: Optional[str] = None

    def to_dict(self) -> Dict[str, str]:
        out = {}
        if self.current:
            out["current"] = self.current
        if self.yoy:
            out["yoy"] = self.yoy
        return out


def _find_metric(text: str, key: str) -> Metric:
    m = Metric()
    pat = METRIC_PATTERNS[key]
    hit = pat.search(text)
    if hit:
        m.current = _squash_spaces(hit.group(2))
        # search nearby for YoY change
        window_start = max(0, hit.start() - 200)
        window_end = min(len(text), hit.end() + 200)
        window = text[window_start:window_end]
        yoy = None
        mdir = PCT_VERB_PAT.search(window)
        yoy2 = YOY_PAT.search(window)
        if yoy2:
            yoy = yoy2.group(4)
            verb = yoy2.group(2) or ""
            if verb:
                yoy = f"{verb.strip()} {yoy}"
        elif mdir:
            yoy = f"{mdir.group(1)} {mdir.group(2)}"
        if yoy:
            m.yoy = _squash_spaces(yoy)
    return m


def _detect_period(title_hint: Optional[str], text: str) -> Optional[str]:
    candidates = []
    if title_hint:
        candidates.append(title_hint)
    # Take the top of the document as well
    candidates.append(text[:1000])
    for blob in candidates:
        for pat in PERIOD_PATTERNS:
            h = pat.search(blob)
            if h:
                # Normalize spellings
                g1 = h.group(1)
                g2 = h.group(2)
                if g1 and g1.lower().startswith("first"):
                    q = "Q1"
                elif g1 and g1.lower().startswith("second"):
                    q = "Q2"
                elif g1 and g1.lower().startswith("third"):
                    q = "Q3"
                elif g1 and g1.lower().startswith("fourth"):
                    q = "Q4"
                else:
                    q = g1.upper() if g1 else "FY"
                return f"{q} {g2}"
    return None


def _find_guidance(text: str, max_items: int = 2) -> List[str]:
    out: List[str] = []
    for m in GUIDANCE_PAT.finditer(text):
        # Expand to sentence boundaries
        s = m.start()
        e = m.end()
        # find previous period
        ps = text.rfind(".", 0, s)
        pe = text.find(".", e)
        snippet = text[max(0, ps + 1): (pe + 1 if pe != -1 else min(len(text), e + 160))]
        snippet = _squash_spaces(snippet)
        if snippet and snippet not in out:
            out.append(snippet)
        if len(out) >= max_items:
            break
    return out


# ------------------------------ Summary ------------------------------

def _compose_summary(headline: str, period: Optional[str], metrics: Dict[str, Metric]) -> Tuple[str, List[str]]:
    parts: List[str] = []
    bullets: List[str] = []

    if period:
        parts.append(f"{period} results:")

    def fmt(name: str, m: Metric) -> Optional[str]:
        if not (m.current or m.yoy):
            return None
        s = name
        if m.current:
            s += f" {m.current}"
        if m.yoy:
            s += f" ({m.yoy})"
        return s

    for k, label in ("revenue", "Revenue"), ("ebitda", "Adj. EBITDA"), ("net_income", "Net income"), ("eps", "EPS"):
        f = fmt(label, metrics.get(k, Metric()))
        if f:
            parts.append(f)
            bullets.append(f"{label}: {f.split(' ', 1)[1] if ' ' in f else ''}".strip())

    short = " ".join(parts) if parts else headline
    return short, bullets


# ------------------------------- Public ------------------------------

@dataclass
class _Doc:
    final_url: str
    text: str
    title_html: Optional[str]


def _fetch_text(url: str) -> _Doc:
    client = _Http()
    r = client.get(url, allow_redirects=True)
    final_url = str(r.url)
    ctype = (r.headers.get("Content-Type") or "").lower()

    # If it's a PDF, extract text
    if "application/pdf" in ctype or final_url.lower().endswith(".pdf"):
        text = _extract_pdf_text(r.content)
        return _Doc(final_url=final_url, text=text, title_html=None)

    # Otherwise treat as HTML
    html_text = r.text
    soup = BeautifulSoup(html_text, "lxml")
    title_tag = soup.find("title")
    title_html = title_tag.get_text(strip=True) if title_tag else None

    try:
        # Clean the soup for better signal
        for sel in JUNK_SELECTORS.split(","):
            for node in soup.select(sel.strip()):
                node.decompose()
    except Exception:
        pass

    text = _squash_spaces(soup.get_text(" \n "))
    return _Doc(final_url=final_url, text=text, title_html=title_html)


def fetch_and_summarize(url: str, title_hint: Optional[str] = None) -> Dict[str, object]:
    """Fetch a filing/IR page/press release and return a summary payload.

    Returns a dict that the existing email renderer expects.
    """
    doc = _fetch_text(url)
    text = doc.text

    # Headline selection: prefer hint, then HTML <title>, else URL
    headline = (title_hint or doc.title_html or url)

    # Primary KPIs
    metrics: Dict[str, Metric] = {
        "revenue": _find_metric(text, "revenue"),
        "ebitda": _find_metric(text, "ebitda"),
        "net_income": _find_metric(text, "net_income"),
        "eps": _find_metric(text, "eps"),
    }

    # Period + guidance
    period = _detect_period(title_hint, text)
    guidance_bits = _find_guidance(text)

    # Compose short summary + default bullets
    short_summary, bullets = _compose_summary(headline, period, metrics)

    # Add guidance bullets (limited)
    for g in guidance_bits:
        if g not in bullets:
            bullets.append(g)

    # Ensure bullets are concise and unique
    dedup: List[str] = []
    seen = set()
    for b in bullets:
        b = b.strip()
        if b and b not in seen:
            seen.add(b)
            dedup.append(b)
    bullets = dedup[:6]

    # Convert metrics to dicts
    m_out = {k: v.to_dict() for k, v in metrics.items() if (v.current or v.yoy)}

    payload: Dict[str, object] = {
        "headline": headline,
        "final_url": doc.final_url,
        "short_summary": short_summary,
        "key_highlights": bullets,
        **m_out,
    }

    # Optional note if nothing was confidently extracted
    if not m_out:
        payload["final_thoughts"] = "Could not confidently extract KPIs; included link and summary context."

    return payload
