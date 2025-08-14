# page_watcher.py
import re
import time
import requests
from typing import Iterable
from bs4 import BeautifulSoup
from .base import Watcher, FoundItem

KEYWORDS = [
    "results", "earnings", "quarter", "q1", "q2", "q3", "q4",
    "trading update", "interim", "half-year", "half year",
    "full year", "annual"
]

USER_AGENT = "NEXT.io Earnings Watcher (contact: stuatnext@gmail.com)"


def _parse_time(soup: BeautifulSoup) -> int | None:
    """
    Attempt to extract a page-level published time.
    Looks for <time datetime=""> or other date attributes.
    """
    t = soup.find("time", attrs={"datetime": True})
    if t and t.get("datetime"):
        from dateutil import parser as date_parser
        try:
            dt = date_parser.parse(t["datetime"])
            return int(dt.timestamp())
        except Exception:
            pass
    return None


def _link_is_results(text: str) -> bool:
    """Return True if the link text contains any of the result-related keywords."""
    lt = (text or "").lower()
    return any(k in lt for k in KEYWORDS)


class PageWatcher(Watcher):
    name = "page"

    def __init__(self, page_url: str):
        self.page_url = page_url

    def poll(self) -> Iterable[FoundItem]:
        """
        Fetch the page, look for relevant news/result links,
        and return them as FoundItem objects.
        """
        try:
            res = requests.get(
                self.page_url, timeout=20, headers={"User-Agent": USER_AGENT}
            )
            res.raise_for_status()
        except requests.RequestException:
            return []

        soup = BeautifulSoup(res.text, "lxml")
        page_ts = _parse_time(soup)

        # Candidate anchors from relevant content areas
        scopes = soup.select(
            "article a, main a, .content a, #content a, .news a"
        ) or soup.find_all("a", href=True)

        candidates = []
        for a in scopes:
            title = a.get_text(" ", strip=True)
            href = a.get("href", "")
            if not href or not title:
                continue
            if not _link_is_results(title):
                continue

            url = requests.compat.urljoin(self.page_url, href)
            ts = page_ts or int(time.time())
            candidates.append((title, url, ts))

        # Deduplicate and yield most recent-looking items
        seen = set()
        for title, url, ts in candidates[:20]:  # Limit to first 20 matches
            if url in seen:
                continue
            seen.add(url)
            yield FoundItem(self.page_url, title, url, ts)
