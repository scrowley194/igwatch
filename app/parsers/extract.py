import re
import logging
from urllib.parse import urlparse, parse_qs
from io import BytesIO

from bs4 import BeautifulSoup
from pdfminer.high_level import extract_text as pdf_extract_text

# Import config defensively so new fields are optional
from .. import config as CFG
from ..net_fetchers import make_session

logger = logging.getLogger(__name__)
SESSION = make_session()

# -------------------------------
# Config (with safe defaults)
# -------------------------------
BROWSER_UA = getattr(CFG, "BROWSER_UA", "NEXT.io Earnings Watcher")
SCRAPING_API_KEY = getattr(CFG, "SCRAPING_API_KEY", None)
GOOD_WIRE_DOMAINS = set(getattr(CFG, "GOOD_WIRE_DOMAINS", []))
BLOCK_DOMAINS = set(getattr(CFG, "BLOCK_DOMAINS", []))
FIRST_PARTY_ONLY = bool(getattr(CFG, "FIRST_PARTY_ONLY", False))
JUNK_DOMAINS = set(getattr(CFG, "JUNK_DOMAINS", []))
JUNK_SELECTORS = list(getattr(CFG, "JUNK_SELECTORS", []))

# -------------------------------
# Helpers (host, normalize, junk strip, etc.)
# -------------------------------

def _host(u: str) -> str:
    try:
        return urlparse(u).netloc.split(":")[0].lower()
    except Exception:
        return ""


def _norm(s: str) -> str:
    return re.sub(r"[\s\n]+", " ", (s or "").strip())


def _strip_junk(soup: BeautifulSoup) -> BeautifulSoup:
    for sel in JUNK_SELECTORS:
        for el in soup.select(sel):
            el.decompose()
    return soup


def _pick_article_root(soup: BeautifulSoup):
    for sel in ("article", "main", "#content", ".content", ".article"):
        el = soup.select_one(sel)
        if el:
            return el
    return soup.body or soup

# -------------------------------
# Parsers exposed to main.py
# -------------------------------

def parse_from_html(html: str, source_url: str = None) -> dict:
    soup = BeautifulSoup(html, "lxml")
    soup = _strip_junk(soup)
    article_root = _pick_article_root(soup)

    title_el = article_root.find("h1") or soup.find("h1") or soup.find("title")
    headline = _norm(title_el.get_text(" ", strip=True) if title_el else "")

    paras = [
        _norm(p.get_text(" ", strip=True)) for p in article_root.select("p")
        if len(_norm(p.get_text(" ", strip=True))) > 40
    ]
    summary = " ".join(paras[:3])

    return {
        "headline": headline or "Earnings/Results",
        "short_summary": summary or "Press release / results page",
        "final_url": source_url,
    }


def parse_from_clean_text(text: str, source_url: str = None) -> dict:
    text = (text or "")[:5000]
    lines = [l.strip() for l in text.splitlines() if len(l.strip()) > 40]
    headline = lines[0] if lines else "Results summary"
    summary = " ".join(lines[1:4])

    return {
        "headline": headline,
        "short_summary": summary or "Text-only parse",
        "final_url": source_url,
    }
