# app/watchers/page_watcher.py
import re
import time
import logging
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .base import Watcher, FoundItem
from ..config import BROWSER_UA, FIRST_PARTY_ONLY, GOOD_WIRE_DOMAINS, BLOCK_DOMAINS

logger = logging.getLogger(__name__)

# Titles like “Reports 24% revenue growth …” often don’t include “Q2”/“earnings”
KEYWORDS = [
    "q1", "q2", "q3", "q4",
    "quarter", "quarterly",
    "earnings", "results", "financial results",
    "trading update", "interim", "half-year", "half year", "full year",
    "preliminary", "interim report", "trading statement",
    "reports", "guidance", "revenue"  # allow strong finance phrasing
]

TIMEOUT = (10, 45)  # (connect, read) seconds

def _session():
    s = requests.Session()
    s.headers.update({
        "User-Agent": BROWSER_UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Connection": "keep-alive",
        "Referer": "https://www.google.com/"  # helps with some IR/CDN setups
    })
    retry = Retry(
        total=3,
        backoff_factor=0.8,
        status_forcelist=[403, 408, 429, 500, 502, 503, 504],
        allowed_methods=["GET", "HEAD"]
    )
    adapter = HTTPAdapter(max_retries=retry)
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    return s

SESSION = _session()

def _host(u: str) -> str:
    return urlparse(u).netloc.split(":")[0].lower()

def _allowed(base_url: str, candidate_url: str, allowed_domains=None) -> bool:
    allowed_domains = [d.lower() for d in (allowed_domains or [])]
    host = _host(candidate_url)
    if host in BLOCK_DOMAINS:
        return False

    # Per-watcher allowlist
    if allowed_domains and not any(host == d or host.endswith("." + d) for d in allowed_domains):
        return False

    base_host = _host(base_url)
    # Same-site is always ok
    if host == base_host or host.endswith("." + base_host):
        return True

    # If not same-site, only allow known wires in FIRST_PARTY_ONLY mode
    if FIRST_PARTY_ONLY:
        return host in GOOD_WIRE_DOMAINS

    return True

def _link_is_results(text: str, href: str) -> bool:
    lt = (text or "").lower()
    if any(k in lt for k in KEYWORDS):
        return True
    # Some sites encode quarter in URL path more than title
    h = (href or "").lower()
    if re.search(r"/(q[1-4]|first|second|third|fourth)[-_ ]quarter\b", h):
        return True
    return False

def _page_time(soup: BeautifulSoup):
    t = soup.find("time", attrs={"datetime": True})
    if t and t.get("datetime"):
        try:
            from dateutil import parser as d
            return int(d.parse(t["datetime"]).timestamp())
        except Exception:
            pass
    return None

def _pick_pdf_or_release(detail_url: str):
    """Fetch detail page, prefer a PDF 'deck' if present, else return the detail page itself."""
    try:
        r = SESSION.get(detail_url, timeout=TIMEOUT)
        r.raise_for_status()
    except Exception as e:
        logger.debug("Detail fetch failed for %s: %s", detail_url, e)
        return detail_url  # fallback to the detail page

    soup = BeautifulSoup(r.text, "lxml")

    # Prefer the investor 'static-files' PDF or any .pdf link labelled presentation/deck/results
    for a in soup.find_all("a", href=True):
        href = a["href"]
        text = a.get_text(" ", strip=True).lower()
        url = urljoin(detail_url, href)
        if url.lower().endswith(".pdf") or "/static-files/" in url.lower():
            if "presentation" in text or "deck" in text or "results" in text or "earnings" in text:
                return url

    # Accept Business Wire / GlobeNewswire mirrors if present
    for a in soup.find_all("a", href=True):
        href = a["href"]
        url = urljoin(detail_url, href)
        host = _host(url)
        if host in GOOD_WIRE_DOMAINS:
            return url

    return detail_url

class PageWatcher(Watcher):
    name = "page"

    def __init__(self, page_url, allowed_domains=None, follow_detail=False):
        self.page_url = page_url
        self.allowed_domains = [d.lower() for d in (allowed_domains or [])]
        self.follow_detail = bool(follow_detail)

    def poll(self):
        try:
            res = SESSION.get(self.page_url, timeout=TIMEOUT)
            res.raise_for_status()
        except Exception as e:
            # Many Q4 / gcs-web pages 403 under automation — swallow and move on
            logger.debug("PageWatcher fetch failed for %s: %s", self.page_url, e)
            return []

        soup = BeautifulSoup(res.text, "lxml")
        page_ts = _page_time(soup) or int(time.time())

        # Broad but safe scopes
        anchors = soup.select("article a, main a, .content a, #content a, .news a, .events a") or soup.find_all("a", href=True)

        seen = set()
        items = []
        for a in anchors:
            title = a.get_text(" ", strip=True)
            href = a.get("href", "")
            if not title or not href:
                continue
            if not _link_is_results(title, href):
                continue

            url = urljoin(self.page_url, href)
            if not _allowed(self.page_url, url, self.allowed_domains):
                continue
            if url in seen:
                continue
            seen.add(url)

            final_url = _pick_pdf_or_release(url) if self.follow_detail else url
            if not _allowed(self.page_url, final_url, self.allowed_domains):
                continue

            items.append(FoundItem(self.page_url, title, final_url, page_ts))

        return items
