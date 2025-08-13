import time, re
from typing import Iterable
import requests
from bs4 import BeautifulSoup
from .base import Watcher, FoundItem

KEYWORDS = [
    "results", "interim", "quarter", "q1", "q2", "q3", "q4",
    "trading update", "earnings", "report", "financial", "interim report"
]

class PageWatcher(Watcher):
    name = "page"

    def __init__(self, page_url: str):
        self.page_url = page_url

    def poll(self) -> Iterable[FoundItem]:
        res = requests.get(self.page_url, timeout=20, headers={"User-Agent": "NEXT.io Earnings Watcher"})
        soup = BeautifulSoup(res.text, "lxml")
        links = []
        for a in soup.find_all("a", href=True):
            txt = (a.get_text(" ", strip=True) or "").lower()
            href = a["href"]
            if any(k in txt for k in KEYWORDS):
                links.append((a.get_text(" ", strip=True), requests.compat.urljoin(self.page_url, href)))
        # de-dup and take the most recent-looking (page top bias)
        seen = set()
        for title, url in links[:10]:
            if url in seen:
                continue
            seen.add(url)
            yield FoundItem(source=self.page_url, title=title, url=url, published_ts=int(time.time()))
