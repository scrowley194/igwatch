# app/watchers/page_watcher.py
import re, time
from typing import Iterable, Optional, List
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
from .base import Watcher, FoundItem
from ..config import FIRST_PARTY_ONLY, GOOD_WIRE_DOMAINS, BLOCK_DOMAINS, BROWSER_UA
from ..net import make_session

SESSION = make_session()

RESULT_WORDS = [
    "results","earnings","quarter","q1","q2","q3","q4",
    "trading update","interim","half-year","half year","full year","annual",
    "preliminary","interim report","trading statement"
]

# href patterns used by Q4/GCS-Web sites
HREF_SIGNALS = re.compile(
    r"(news-details|press-releases|event-details|events|financials|quarterly-results|static-files|/q[1-4]\b|/earnings|/results)",
    re.I
)

def _host(u: str) -> str:
    return urlparse(u).netloc.split(":")[0].lower()

def _allowed(base_url: str, candidate_url: str) -> bool:
    h = _host(candidate_url)
    if h in BLOCK_DOMAINS:
        return False
    base = _host(base_url)
    if h.endswith(base):           # same site
        return True
    if FIRST_PARTY_ONLY:
        return h in GOOD_WIRE_DOMAINS
    return True

def _parse_time(soup: BeautifulSoup) -> Optional[int]:
    t = soup.find("time", attrs={"datetime": True})
    if t and t.get("datetime"):
        try:
            from dateutil import parser as d
            return int(d.parse(t["datetime"]).timestamp())
        except Exception:
            return None
    return None

def _looks_like_results(text: str, href: str) -> bool:
    lt = (text or "").lower()
    if any(w in lt for w in RESULT_WORDS):
        return True
    return bool(HREF_SIGNALS.search(href or ""))

class PageWatcher(Watcher):
    name = "page"

    def __init__(self, page_url: str, allowed_domains: Optional[List[str]] = None):
        self.page_url = page_url
        self.allowed_domains = [d.lower() for d in (allowed_domains or [])]

    def poll(self) -> Iterable[FoundItem]:
        # generous read timeout; Q4 pages can be slow
        res = SESSION.get(self.page_url, timeout=(10, 45), headers={"Referer": self.page_url})
        res.raise_for_status()

        soup = BeautifulSoup(res.text, "lxml")
        page_ts = _parse_time(soup) or int(time.time())

        anchors = soup.select("a[href]")  # broad; we filter below
        seen = set()
        for a in anchors:
            title = a.get_text(" ", strip=True) or ""
            href = a.get("href") or ""
            if not href:
                continue
            url = urljoin(self.page_url, href)

            # domain allowlist (per-company)
            host = _host(url)
            if self.allowed_domains and not any(host == d or host.endswith("." + d) for d in self.allowed_domains):
                continue
            if not _allowed(self.page_url, url):
                continue
            if not _looks_like_results(title, href):
                continue
            if url in seen:
                continue
            seen.add(url)
            yield FoundItem(self.page_url, title or href, url, page_ts)
