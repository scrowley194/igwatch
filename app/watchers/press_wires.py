import time, email.utils, logging
from typing import Iterable
from urllib.parse import urlparse, urljoin, urlencode

import feedparser
from bs4 import BeautifulSoup

from .base import Watcher, FoundItem
from ..net import make_session
from .. import config as CFG

SESSION = make_session()
logger = logging.getLogger(__name__)

SCRAPING_API_KEY = getattr(CFG, "SCRAPING_API_KEY", None)


def _host(u: str) -> str:
    return urlparse(u).netloc.split(":")[0].lower()


class PressWireWatcher(Watcher):
    name = "wire"

    def __init__(self, listing_url: str):
        self.listing_url = listing_url

    def _fetch(self, url: str) -> str:
        if SCRAPING_API_KEY:
            proxy = "http://api.scraperapi.com"
            params = {"api_key": SCRAPING_API_KEY, "url": url, "country_code": "us", "render": "true"}
            r = SESSION.get(proxy, params=params, timeout=90)
        else:
            r = SESSION.get(url, timeout=(10, 30))
        r.raise_for_status()
        return r.text

    def poll(self) -> Iterable[FoundItem]:
        html = self._fetch(self.listing_url)
        soup = BeautifulSoup(html, "lxml")

        anchors = (
            soup.select('a[href*="businesswire.com/news/"]')
            or soup.select('a[href*="globenewswire.com/news-release/"]')
            or soup.select('a[href*="prnewswire.com/news-releases/"]')
            or soup.select("a[href]")
        )

        seen = set()
        now_ts = int(time.time())
        count = 0
        for a in anchors:
            href = a.get("href") or ""
            if not href:
                continue
            url = urljoin(self.listing_url, href)
            h = _host(url)
            if h not in ("businesswire.com", "globenewswire.com", "prnewswire.com"):
                continue
            if url in seen:
                continue
            seen.add(url)
            title = (a.get_text(" ", strip=True) or url).strip()
            count += 1
            yield FoundItem(self.listing_url, title, url, now_ts)
        logger.debug("PressWireWatcher %s yielded %d items", self.listing_url, count)


class GoogleNewsWatcher(Watcher):
    name = "gnews"

    def __init__(self, query: str):
        self.query = query

    def _feed_url(self) -> str:
        params = {"q": self.query, "hl": "en-US", "gl": "US", "ceid": "US:en"}
        return "https://news.google.com/rss/search?" + urlencode(params)

    def poll(self) -> Iterable[FoundItem]:
        feed_url = self._feed_url()
        try:
            if SCRAPING_API_KEY:
                proxy = "http://api.scraperapi.com"
                params = {"api_key": SCRAPING_API_KEY, "url": feed_url, "country_code": "us", "render": "true"}
                r = SESSION.get(proxy, params=params, timeout=90)
            else:
                r = SESSION.get(feed_url, timeout=20)
            r.raise_for_status()
            feed_content = r.text
        except Exception as e:
            logger.error("Failed to fetch Google News RSS feed: %s", e)
            return

        feed = feedparser.parse(feed_content)
        total = 0
        for e in feed.entries[:100]:
            title = (e.get("title") or "").strip()
            link = (e.get("link") or "").strip()
            if not title or not link:
                continue
            ts = None
            pub = e.get("published")
            if pub:
                try:
                    ts = int(time.mktime(email.utils.parsedate(pub)))
                except Exception:
                    ts = None
            total += 1
            yield FoundItem(feed_url, title, link, ts)
        logger.debug("GoogleNewsWatcher '%s' yielded %d entries", self.query, total)
