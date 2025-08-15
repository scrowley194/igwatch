import re
import logging
from urllib.parse import urlparse
from io import BytesIO

from bs4 import BeautifulSoup
from pdfminer.high_level import extract_text

from ..config import BROWSER_UA, GOOD_WIRE_DOMAINS, BLOCK_DOMAINS, SCRAPING_API_KEY
from ..net import make_session

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------
# Session Setup
# --------------------------------------------------------------------
SESSION = make_session()

# --------------------------------------------------------------------
# Regex Patterns
# --------------------------------------------------------------------
_MONEY = r"(?:[\$£€]\s?\d[\d,]*(?:\.\d+)?\s*(?:million|billion|m|bn)?|\d[\d,]*(?:\.\d+)?\s*(?:million|billion|m|bn))"
_PCT = r"(?:\+|\-)?\d+(?:\.\d+)?\s?%"
_NUM = r"(?:\+|\-)?\d+(?:\.\d+)?"  # for EPS/ratios

# --------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------

def _host(u: str) -> str:
    return urlparse(u).netloc.split(":")[0].lower()


def _norm(s: str) -> str:
    return re.sub(r"[\s\n]+", " ", (s or "").strip())


def is_blocked_domain(url: str) -> bool:
    if not BLOCK_DOMAINS:
        return False
    netloc = urlparse(url).netloc.lower()
    return any(netloc == d or netloc.endswith("." + d) for d in BLOCK_DOMAINS)

# --------------------------------------------------------------------
# Extraction Logic
# --------------------------------------------------------------------

def _extract_numbers(text: str) -> dict:
    """Heuristics for key financial data (strict enough, but broader coverage)."""
    out = {
        "revenue": {"current": "Not found", "prior": "Not found", "yoy": "n/a"},
        "ebitda": {"current": "Not found", "prior": "Not found", "yoy": "n/a"},
        "net_income": {"current": "Not found", "prior": "Not found", "yoy": "n/a"},
        "eps": {"current": "Not found", "prior": "Not found", "yoy": "n/a"},
        "geo_breakdown": [],
        "product_breakdown": [],
        "controversial_points": [],
    }

    # Revenue / Net Sales / Turnover
    if m := re.search(r"(?:revenue|net sales|turnover)[^.\n]*?(" + _MONEY + ")", text, flags=re.I):
        out["revenue"]["current"] = _norm(m.group(1))
    if y := re.search(r"(?:revenue)[^.\n]*?(" + _PCT + r"){1}\s*(?:yoy|year[- ]over[- ]year|vs\.?\s*prior)", text, flags=re.I):
        out["revenue"]["yoy"] = _norm(y.group(1))

    # EBITDA
    if m := re.search(r"(?:adjusted\s*)?ebitda[^.\n]*?(" + _MONEY + ")", text, flags=re.I):
        out["ebitda"]["current"] = _norm(m.group(1))
    if y := re.search(r"ebitda[^.\n]*?(" + _PCT + r"){1}\s*(?:yoy|year[- ]over[- ]year|vs\.?\s*prior)", text, flags=re.I):
        out["ebitda"]["yoy"] = _norm(y.group(1))

    # Net income / profit / loss (accept synonyms and negative phrasing)
    if m := re.search(r"(?:net\s+(?:income|profit)|profit\s+after\s+tax|loss)[^.\n]*?(" + _MONEY + ")", text, flags=re.I):
        out["net_income"]["current"] = _norm(m.group(1))
    if y := re.search(r"(?:net\s+(?:income|profit)|loss)[^.\n]*?(" + _PCT + r"){1}\s*(?:yoy|year[- ]over[- ]year|vs\.?\s*prior)", text, flags=re.I):
        out["net_income"]["yoy"] = _norm(y.group(1))

    # EPS (diluted/basic) — usually a plain number, sometimes signed
    if m := re.search(r"(?:diluted\s+)?(?:eps|earnings\s+per\s+share)[^.\n]*?(" + _NUM + ")", text, flags=re.I):
        out["eps"]["current"] = _norm(m.group(1))
    if y := re.search(r"(?:eps|earnings\s+per\s+share)[^.\n]*?(" + _PCT + r"){1}\s*(?:yoy|year[- ]over[- ]year|vs\.?\s*prior)", text, flags=re.I):
        out["eps"]["yoy"] = _norm(y.group(1))

    # Compact "breakdown" lines
    for line in text.splitlines():
        line_norm = _norm(line)
        if len(line_norm) > 250:
            continue
        has_metric = re.search(_PCT, line) or re.search(_MONEY, line)
        if not has_metric:
            continue
        if re.search(r"\b(US|USA|UK|Europe|EU|Canada|Australia|LatAm|LATAM|North America|Asia|MEA)\b", line, re.I):
            out["geo_breakdown"].append(line_norm)
        if re.search(r"\b(OSB|Sportsbook|iCasino|Casino|Gaming|Lottery|Media|B2B|B2C)\b", line, re.I):
            out["product_breakdown"].append(line_norm)

    for kw in ["regulatory", "investigation", "fine", "penalty", "governance", "sanction", "compliance"]:
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

    summary_paragraphs = soup.select("article p, main p, .content p, #content p, .article p")
    summary = " ".join([_norm(p.get_text(" ", strip=True)) for p in summary_paragraphs[:3]])

    highlights = []
    list_items = soup.select("article li, main li, .content li, #content li, .article li")
    for item in list_items:
        text = _norm(item.get_text(" ", strip=True))
        if 15 < len(text) < 300:
            highlights.append(text)

    text = soup.get_text("\n")
    data = _extract_numbers(text)

    return {
        "headline": headline or "Earnings/Results",
        "short_summary": summary or "Press release / results page",
        "key_highlights": highlights,
        "final_url": url,
        **data,
        "final_thoughts": "Early read: watch OSB/iCasino trajectory, regulation, and supplier order books; refine after call transcript.",
    }


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

# --------------------------------------------------------------------
# Main Entry
# --------------------------------------------------------------------

def fetch_and_summarize(url: str, title_hint: str = "") -> dict | None:
    if not SCRAPING_API_KEY:
        raise ValueError("SCRAPING_API_KEY is not set in the environment.")

    proxy_url = "http://api.scraperapi.com"
    params = {
        "api_key": SCRAPING_API_KEY,
        "url": url,
        "country_code": "us",
        "render": "true",
    }

    r = SESSION.get(proxy_url, params=params, timeout=90)
    r.raise_for_status()

    # The final URL is the one the proxy resolved to.
    final_url = r.url

    # Block based on the *final* domain if necessary.
    if is_blocked_domain(final_url):
        logger.info("Skipping blocked domain (after redirect): %s", final_url)
        return None

    ctype = r.headers.get("Content-Type", "").lower()

    if "application/pdf" in ctype or final_url.lower().endswith(".pdf"):
        text = extract_text(BytesIO(r.content))
        return _summarize_pdf(final_url, text)

    return _summarize_html(final_url, r.text)
