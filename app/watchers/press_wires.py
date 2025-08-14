# press_wires.py
import time
import re
import requests
import feedparser
import email.utils
from typing import Iterable, List
from bs4 import BeautifulSoup

from .base import Watcher, FoundItem

USER_AGENT = "NEXT.io Earnings Watcher (contact: stuatnext@gmail.com)"

RESULT_KEYWORDS = [
    "q1", "q2", "q3", "q4",
    "quarter", "quarterly", "earnings", "results",
    "trading update", "interim", "half-year", "half year",
    "full year", "annual"
]

SECTOR_KEYWORDS = [
    "igaming", "i-gaming", "i gaming", "online casino",
    "casino", "poker", "bingo", "sportsbook",
    "sports betting", "betting", "gaming", "lottery"
]


def _match_any(text: str, needles: List[str]) -> bool:
    t = (text or "").lower()
    return any(n in t for n in needles)


def _ts_from_rfc822(s: str) -> int | None:
    try:
        return int(time.mktime(email.utils.parsedate(s)))
    except Exception:
        return None


# -------------------------------------------------------------------
# 1) Press wires/category pages (HTML)
# -------------------------------------------------------------------
class PressWireWatcher(Watcher):
    """
    Generic watcher for press-wire category/listing pages.
    Example sources: Business Wire, PR Newswire, GlobeNewswire categories.
    It scans anchors in article/content areas and keeps links that match:
      (RESULT_KEYWORDS) AND (SECTOR_KEYWORDS)
    """
    name = "wire"

    def __init__(self, page_url: str):
        self.page_url = page_url

    def poll(self) -> Iterable[FoundItem]:
        try:
            res = requests.get(
                self.page_url,
                timeout=45,
                headers={"User-Agent": USER_AGENT},
            )
            res.raise_for_status()
        except requests.RequestException:
            return []

        soup = BeautifulSoup(res.text, "lxml")

        # Prefer focused content regions, then fall back to all anchors
        scopes = soup.select(
            "article a, main a, .content a, #content a, .news a, .results-list a, .press a"
        ) or soup.find_all("a", href=True)

        # Page-level time if present
        page_ts = None
        t = soup.find("time", attrs={"datetime": True})
        if t and t.get("datetime"):
            from dateutil import parser as d
            try:
                page_ts = int(d.parse(t["datetime"]).timestamp())
            except Exception:
                page_ts = None

        items = []
        for a in scopes:
            title = a.get_text(" ", strip=True)
            href = a.get("href", "")
            if not href or not title:
                continue

            # Skip obvious placeholders from some IR CDNs
            if "Powered-by-Q4" in href or "q4inc.com/Powered-by-Q4" in href:
                continue

            # Must match BOTH result and sector keywords
            if not _match_any(title, RESULT_KEYWORDS):
                continue
            if not _match_any(title, SECTOR_KEYWORDS):
                continue

            url = requests.compat.urljoin(self.page_url, href)
            ts = page_ts or int(time.time())
            items.append(FoundItem(self.page_url, title, url, ts))

        # De-dup on URL, keep page order
        seen = set()
        out = []
        for it in items:
            if it.url in seen:
                continue
            seen.add(it.url)
            out.append(it)

        return out[:25]


# -------------------------------------------------------------------
# 2) Google News RSS (no HTML scraping)
# -------------------------------------------------------------------
class GoogleNewsWatcher(Watcher):
    """
    Google News RSS watcher.
    Builds a Google News RSS URL from a query string, fetches the feed,
    and emits items whose titles match BOTH earnings and sector keywords.

    NOTE: This uses publicly documented Google News RSS endpoints, not HTML scraping.
    """
    name = "gnews"

    def __init__(self, query: str, hl: str = "en", gl: str = "US", ceid: str = "US:en"):
        """
        Args:
            query: raw query string, e.g.
                (Q1 OR Q2 OR Q3 OR Q4 OR "quarterly results" OR earnings)
                (casino OR igaming OR "sports betting")
                (company OR operator OR supplier)
            hl/gl/ceid: locale params for Google News RSS.
        """
        self.query = query
        self.hl = hl
        self.gl = gl
        self.ceid = ceid

    def _url(self) -> str:
        from urllib.parse import quote_plus
        q = quote_plus(self.query)
        # Example: https://news.google.com/rss/search?q=<query>&hl=en&gl=US&ceid=US:en
        return f"https://news.google.com/rss/search?q={q}&hl={self.hl}&gl={self.gl}&ceid={self.ceid}"

    def poll(self) -> Iterable[FoundItem]:
        url = self._url()
        try:
            feed = feedparser.parse(url)
        except Exception:
            return []

        items = []
        for e in feed.entries[:40]:
            title = (e.get("title") or "").strip()
            link = (e.get("link") or "").strip()
            if not title or not link:
                continue

            # Require BOTH sets of keywords in the title
            if not _match_any(title, RESULT_KEYWORDS):
                continue
            if not _match_any(title, SECTOR_KEYWORDS):
                continue

            ts = None
            if hasattr(e, "published"):
                ts = _ts_from_rfc822(e.published)

            items.append(FoundItem(url, title, link, ts))

        return items
