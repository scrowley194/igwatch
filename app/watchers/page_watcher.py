import re, time
from typing import Iterable
import requests
from bs4 import BeautifulSoup
from .base import Watcher, FoundItem

KEYWORDS = [
    "results","earnings","quarter","q1","q2","q3","q4",
    "trading update","interim","half-year","half year","full year","annual"
]

def _parse_time(soup: BeautifulSoup) -> int | None:
    # Try to find a page-level published time
    # Look for <time datetime=""> or data attributes
    t = soup.find("time", attrs={"datetime": True})
    if t and t.get("datetime"):
        import email.utils, datetime
        text = t.get("datetime")
        try:
            # Try multiple formats
            from dateutil import parser as d
            dt = d.parse(text)
            return int(dt.timestamp())
        except Exception:
            pass
    return None

def _link_is_results(text: str) -> bool:
    lt = (text or "").lower()
    return any(k in lt for k in KEYWORDS)

class PageWatcher(Watcher):
    name = "page"
    def __init__(self, page_url: str):
        self.page_url = page_url

    def poll(self) -> Iterable[FoundItem]:
        res = requests.get(self.page_url, timeout=20, headers={"User-Agent":"NEXT.io Earnings Watcher"})
        soup = BeautifulSoup(res.text, "lxml")
        page_ts = _parse_time(soup)
        # Scan anchors; prefer those within article/main lists
        candidates = []
        scopes = soup.select("article a, main a, .content a, #content a, .news a") or soup.find_all("a", href=True)
        for a in scopes:
            title = a.get_text(" ", strip=True)
            href = a.get("href","")
            if not href or not title: 
                continue
            if not _link_is_results(title):
                continue
            url = requests.compat.urljoin(self.page_url, href)
            ts = page_ts or int(time.time())
            candidates.append((title, url, ts))
        # De-dup and yield a few most recent-looking items (page order heuristic)
        seen=set()
        for title, url, ts in candidates[:20]:
            if url in seen: 
                continue
            seen.add(url)
            yield FoundItem(self.page_url, title, url, ts)
