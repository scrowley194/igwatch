# page_watcher.py
import re, time
from typing import Iterable, List, Optional
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse

from .base import Watcher, FoundItem
from ..config import BROWSER_UA, FIRST_PARTY_ONLY, GOOD_WIRE_DOMAINS, BLOCK_DOMAINS

KEYWORDS = [
    "results","earnings","quarter","q1","q2","q3","q4",
    "trading update","interim","half-year","half year","full year","annual",
    "preliminary","interim report","trading statement"
]

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": BROWSER_UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Connection": "keep-alive",
})

def _parse_time(soup: BeautifulSoup) -> Optional[int]:
    t = soup.find("time", attrs={"datetime": True})
    if t and t.get("datetime"):
        try:
            from dateutil import parser as d
            return int(d.parse(t["datetime"]).timestamp())
        except Exception:
            pass
    return None

def _link_is_results(text: str) -> bool:
    lt = (text or "").lower()
    return any(k in lt for k in KEYWORDS)

def _host(netloc: str) -> str:
    return netloc.split(":")[0].lower()

def _allowed(base_url: str, candidate_url: str) -> bool:
    host = _host(urlparse(candidate_url).netloc)
    if host in BLOCK_DOMAINS:
        return False
    base_host = _host(urlparse(base_url).netloc)

    # Same-site is always allowed
    if host.endswith(base_host):
        return True

    # If not same-site, allow only known wires when FIRST_PARTY_ONLY is on
    if FIRST_PARTY_ONLY:
        return host in GOOD_WIRE_DOMAINS

    return True  # permissive if FIRST_PARTY_ONLY=false

class PageWatcher(Watcher):
    name = "page"

    def __init__(self, page_url: str, allowed_domains: Optional[List[str]] = None):
        self.page_url = page_url
        self.allowed_domains = [d.lower() for d in (allowed_domains or [])]

    def poll(self) -> Iterable[FoundItem]:
        res = SESSION.get(self.page_url, timeout=25)
        res.raise_for_status()
        soup = BeautifulSoup(res.text, "lxml")
        page_ts = _parse_time(soup) or int(time.time())

        anchors = soup.select("article a, main a, .content a, #content a, .news a") or soup.find_all("a", href=True)
        seen = set()
        for a in anchors:
            title = a.get_text(" ", strip=True)
            href = a.get("href", "")
            if not title or not href:
                continue
            if not _link_is_results(title):
                continue
            url = urljoin(self.page_url, href)
            host = _host(urlparse(url).netloc)
            # Per-company allowlist (optional)
            if self.allowed_domains and not any(host.endswith(d) for d in self.allowed_domains):
                continue
            if not _allowed(self.page_url, url):
                continue
            if url in seen:
                continue
            seen.add(url)
            yield FoundItem(self.page_url, title, url, page_ts)
