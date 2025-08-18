import re
import logging
from urllib.parse import urlparse, parse_qs
from io import BytesIO

from bs4 import BeautifulSoup
from pdfminer.high_level import extract_text as pdfminer_extract_text

# Import config defensively so new fields are optional
from .. import config as CFG
from ..net_fetchers import make_session  # FIX: net_fetchers is now a single file module

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
# Known aggregators / promo-heavy sites we should not trust for highlights
JUNK_DOMAINS = set(getattr(CFG, "JUNK_DOMAINS", [
    "tipranks.com", "seekingalpha.com", "fool.com", "benzinga.com",
    "marketwatch.com", "investing.com", "yahoo.com"
]))
# CSS we strip from pages before extracting content
JUNK_SELECTORS = list(getattr(CFG, "JUNK_SELECTORS", [
    "nav", "footer", "header", "aside", "script", "style", "form",
    "[class*='ad-']", ".ad", ".advert", ".promo", ".social", ".share",
    ".related", ".newsletter", ".subscribe", ".breadcrumbs", ".tags",
    ".paywall", ".cookie", ".disclaimer", "#comments"
]))
# Phrases we refuse in highlights
SPAM_PHRASES = [
    "TipRanks", "Premium", "subscribe", "sponsored", "advert", "coupon",
    "sign up", "click", "follow us", "read more"
]

# -------------------------------
# Regexes
# -------------------------------
_MONEY = r"(?:[\$£€]\s?\d[\d,]*(?:\.\d+)?\s*(?:million|billion|m|bn)?|\d[\d,]*(?:\.\d+)?\s*(?:million|billion|m|bn))"
_PCT = r"(?:\+|\-)?\d+(?:\.\d+)?\s?%"
_NUM = r"(?:\+|\-)?\d+(?:\.\d+)?"  # EPS / ratios

# -------------------------------
# Helpers
# -------------------------------
# ... (same helpers as before, unchanged) ...

# -------------------------------
# HTML & PDF summarizers
# -------------------------------

def _summarize_pdf(url: str, text: str) -> dict:
    data = _extract_numbers(text)
    return {
        "headline": "Results presentation / PDF",
        "short_summary": "Key figures extracted from PDF.",
        "key_highlights": [],
        "final_url": url,
        **data,
        "final_thoughts": "PDF-only parse; verify against press release for context.",
    }

# -------------------------------
# Entry
# -------------------------------

def fetch_and_summarize(request_url: str, title_hint: str = "") -> dict | None:
    if not SCRAPING_API_KEY:
        raise ValueError("SCRAPING_API_KEY is not set in the environment.")

    proxy = "http://api.scraperapi.com"
    params = {
        "api_key": SCRAPING_API_KEY,
        "url": request_url,
        "country_code": "us",
        "render": "true",
    }

    r = SESSION.get(proxy, params=params, timeout=90)
    r.raise_for_status()

    ctype = (r.headers.get("Content-Type", "") or "").lower()
    if "application/pdf" in ctype or request_url.lower().endswith(".pdf"):
        final_url = _final_url_from_response(r, request_url, None)
        text = pdf_extract_text(BytesIO(r.content))
        if is_blocked_domain(final_url):
            logger.info("Skipping blocked domain (after redirect): %s", final_url)
            return None
        return _summarize_pdf(final_url, text)

    # HTML path
    html = r.text
    final_url = _final_url_from_response(r, request_url, html)

    if is_blocked_domain(final_url):
        logger.info("Skipping blocked domain (after redirect): %s", final_url)
        return None
    host = _host(final_url)

    if FIRST_PARTY_ONLY and not (_is_wire_or_first_party(host)):
        logger.info("Skipping non-first-party/press-wire domain due to FIRST_PARTY_ONLY: %s", final_url)
        return None

    if _is_junk_domain(host):
        logger.info("Domain flagged as junk for highlights; numeric summary only: %s", final_url)

    return _summarize_html(final_url, html)
