import re
import logging
from urllib.parse import urlparse, parse_qs
from io import BytesIO

from bs4 import BeautifulSoup
from pdfminer.high_level import extract_text

# Import config defensively so new fields are optional
from .. import config as CFG
from ..net import make_session

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

def _host(u: str) -> str:
    try:
        return urlparse(u).netloc.split(":")[0].lower()
    except Exception:
        return ""


def _norm(s: str) -> str:
    return re.sub(r"[\s\n]+", " ", (s or "").strip())


def is_blocked_domain(url: str) -> bool:
    netloc = _host(url)
    if not netloc:
        return False
    return any(netloc == d or netloc.endswith("." + d) for d in BLOCK_DOMAINS)


def _is_wire_or_first_party(host: str) -> bool:
    if not host:
        return False
    if host in GOOD_WIRE_DOMAINS or any(host.endswith("." + d) for d in GOOD_WIRE_DOMAINS):
        return True
    if re.match(r"^(ir|investor|investors|corporate|press|media)\.\w[\w.-]*", host):
        return True
    return False


def _is_junk_domain(host: str) -> bool:
    return host in JUNK_DOMAINS or any(host.endswith("." + d) for d in JUNK_DOMAINS)


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


def _canonical_from_html(soup: BeautifulSoup) -> str | None:
    # Try canonical then og:url
    link = soup.find("link", rel=lambda v: v and "canonical" in v.lower())
    if link and link.get("href"):
        return link["href"].strip()
    og = soup.find("meta", attrs={"property": "og:url"})
    if og and og.get("content"):
        return og["content"].strip()
    return None


def _final_url_from_response(r, request_url: str, html: str | None) -> str:
    # ScraperAPI sometimes sets these headers
    for h in ("X-ScraperAPI-Final-Url", "X-Final-Url", "X-Target-Url"):
        v = r.headers.get(h)
        if v:
            return v
    # If we proxied a Google News link, fall back to the original url param
    req_host = _host(request_url)
    if req_host.endswith("news.google.com"):
        try:
            # Prefer canonical in HTML first
            if html:
                soup = BeautifulSoup(html, "lxml")
                can = _canonical_from_html(soup)
                if can:
                    return can
            qs = parse_qs(urlparse(request_url).query)
            # As a last resort, keep the underlying target if provided (not always present)
            u = qs.get("url", [None])[0]
            if u:
                return u
        except Exception:
            pass
    # Otherwise, canonical from HTML if available
    if html:
        try:
            soup = BeautifulSoup(html, "lxml")
            can = _canonical_from_html(soup)
            if can:
                return can
        except Exception:
            pass
    # Fallback: original request URL
    return request_url

# -------------------------------
# Metric extraction
# -------------------------------

def _extract_numbers(text: str) -> dict:
    out = {
        "revenue": {"current": "Not found", "prior": "Not found", "yoy": "n/a"},
        "ebitda": {"current": "Not found", "prior": "Not found", "yoy": "n/a"},
        "net_income": {"current": "Not found", "prior": "Not found", "yoy": "n/a"},
        "eps": {"current": "Not found", "prior": "Not found", "yoy": "n/a"},
        "geo_breakdown": [],
        "product_breakdown": [],
        "controversial_points": [],
    }

    # Limit to the first ~6k chars to avoid legal boilerplate
    text = (text or "")[:6000]

    # Revenue / Net sales / Turnover
    if m := re.search(r"(?:revenue|net sales|turnover)[^.\n]*?(" + _MONEY + ")", text, flags=re.I):
        out["revenue"]["current"] = _norm(m.group(1))
    if y := re.search(r"(?:revenue)[^.\n]*?(" + _PCT + r"){1}\s*(?:yoy|year[- ]over[- ]year|vs\.?\s*prior)", text, flags=re.I):
        out["revenue"]["yoy"] = _norm(y.group(1))

    # EBITDA
    if m := re.search(r"(?:adjusted\s*)?ebitda[^.\n]*?(" + _MONEY + ")", text, flags=re.I):
        out["ebitda"]["current"] = _norm(m.group(1))
    if y := re.search(r"ebitda[^.\n]*?(" + _PCT + r"){1}\s*(?:yoy|year[- ]over[- ]year|vs\.?\s*prior)", text, flags=re.I):
        out["ebitda"]["yoy"] = _norm(y.group(1))

    # Net income / profit / loss
    if m := re.search(r"(?:net\s+(?:income|profit)|profit\s+after\s+tax|loss)[^.\n]*?(" + _MONEY + ")", text, flags=re.I):
        out["net_income"]["current"] = _norm(m.group(1))
    if y := re.search(r"(?:net\s+(?:income|profit)|loss)[^.\n]*?(" + _PCT + r"){1}\s*(?:yoy|year[- ]over[- ]year|vs\.?\s*prior)", text, flags=re.I):
        out["net_income"]["yoy"] = _norm(y.group(1))

    # EPS
    if m := re.search(r"(?:diluted\s+)?(?:eps|earnings\s+per\s+share)[^.\n]*?(" + _NUM + ")", text, flags=re.I):
        out["eps"]["current"] = _norm(m.group(1))
    if y := re.search(r"(?:eps|earnings\s+per\s+share)[^.\n]*?(" + _PCT + r"){1}\s*(?:yoy|year[- ]over[- ]year|vs\.?\s*prior)", text, flags=re.I):
        out["eps"]["yoy"] = _norm(y.group(1))

    # Compact breakdown lines (avoid buyback noise and marketing)
    for line in text.splitlines():
        line_norm = _norm(line)
        if len(line_norm) > 220:
            continue
        has_metric = re.search(_PCT, line) or re.search(_MONEY, line)
        if not has_metric:
            continue
        if re.search(r"buyback|repurchase", line_norm, re.I):
            continue
        if re.search(r"\b(US|USA|UK|Europe|EU|Canada|Australia|LatAm|LATAM|North America|Asia|MEA)\b", line_norm, re.I):
            out["geo_breakdown"].append(line_norm)
        if re.search(r"\b(OSB|Sportsbook|iCasino|Casino|Gaming|Lottery|Media|B2B|B2C)\b", line_norm, re.I):
            out["product_breakdown"].append(line_norm)

    # Controversy (narrower to reduce false positives)
    for kw in ["investigation", "fine", "penalty", "sanction", "lawsuit", "restatement"]:
        if re.search(rf"\b{re.escape(kw)}\b", text, re.I):
            out["controversial_points"].append(f"Mentions: {kw}")

    return out

# -------------------------------
# HTML & PDF summarizers
# -------------------------------

def _extract_highlights(article_root: BeautifulSoup, host: str) -> list[str]:
    # Only trust <li> bullets on wires or first-party IR; elsewhere, derive numeric sentences
    highlights: list[str] = []

    if _is_wire_or_first_party(host) and not _is_junk_domain(host):
        for li in article_root.select("li"):
            t = _norm(li.get_text(" ", strip=True))
            if not (25 <= len(t) <= 220):
                continue
            if any(p.lower() in t.lower() for p in SPAM_PHRASES):
                continue
            if re.search(_PCT, t) or re.search(_MONEY, t) or re.search(r"\b(revenue|ebitda|profit|loss|eps|guidance)\b", t, re.I):
                highlights.append(t)
    else:
        # Derive from numeric sentences in main paragraphs
        paras = [p.get_text(" ", strip=True) for p in article_root.select("p")]
        for p in paras:
            t = _norm(p)
            if not (25 <= len(t) <= 220):
                continue
            if any(pv.lower() in t.lower() for pv in SPAM_PHRASES):
                continue
            if re.search(_PCT, t) or re.search(_MONEY, t):
                highlights.append(t)

    # De-dupe and cap
    deduped = []
    seen = set()
    for h in highlights:
        if h.lower() in seen:
            continue
        seen.add(h.lower())
        deduped.append(h)
        if len(deduped) >= 8:
            break
    return deduped


def _summarize_html(url: str, html: str) -> dict:
    soup = BeautifulSoup(html, "lxml")
    soup = _strip_junk(soup)
    article_root = _pick_article_root(soup)

    title_el = article_root.find("h1") or soup.find("h1") or soup.find("title")
    headline = _norm(title_el.get_text(" ", strip=True) if title_el else "")

    # Summary: first 2-3 content paragraphs with some substance
    paras = [
        _norm(p.get_text(" ", strip=True)) for p in article_root.select("p")
        if len(_norm(p.get_text(" ", strip=True))) > 40
    ]
    summary = " ".join(paras[:3])

    host = _host(url)
    highlights = _extract_highlights(article_root, host)

    text_for_metrics = article_root.get_text("\n")
    data = _extract_numbers(text_for_metrics)

    return {
        "headline": headline or "Earnings/Results",
        "short_summary": summary or "Press release / results page",
        "key_highlights": highlights,
        "final_url": url,
        **data,
        "final_thoughts": "Auto-extracted; verify against release/PDF if critical.",
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
    html = None
    if "application/pdf" in ctype or request_url.lower().endswith(".pdf"):
        final_url = _final_url_from_response(r, request_url, None)
        text = extract_text(BytesIO(r.content))
        if is_blocked_domain(final_url):
            logger.info("Skipping blocked domain (after redirect): %s", final_url)
            return None
        return _summarize_pdf(final_url, text)

    # HTML path
    html = r.text
    final_url = _final_url_from_response(r, request_url, html)

    # Domain policies
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
