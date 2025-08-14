# app/watchers/press_wires.py
import time, email.utils, re
from typing import Iterable, Optional
from urllib.parse import urlparse, urljoin, urlencode

import feedparser
from bs4 import BeautifulSoup

from .base import Watcher, FoundItem
from ..net import make_session
from ..config import SCRAPING_API_KEY # Import the API key

SESSION = make_session()

def _host(u: str) -> str:
    return urlparse(u).netloc.split(":")[0].lower()

# ---------------------------
# PressWire listing watcher
# ---------------------------
class PressWireWatcher(Watcher):
    """
    Scrapes a press-wire listing/search page (Business Wire / GlobeNewswire / PR Newswire)
    and yields article links.
    """
    name = "wire"

    def __init__(self, listing_url: str):
        self.listing_url = listing_url

    def poll(self) -> Iterable[FoundItem]:
        res = SESSION.get(self.listing_url, timeout=(10, 30))
        res.raise_for_status()
        soup = BeautifulSoup(res.text, "lxml")

        anchors = (
            soup.select('a[href*="businesswire.com/news/"]')
            or soup.select('a[href*="globenewswire.com/news-release/"]')
            or soup.select('a[href*="prnewswire.com/news-releases/"]')
            or soup.select("a[href]")
        )

        seen = set()
        now_ts = int(time.time())
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
            yield FoundItem(self.listing_url, title, url, now_ts)

# ---------------------------
# Google News RSS watcher (Rewritten for reliability)
# ---------------------------
class GoogleNewsWatcher(Watcher):
    """
    Uses Google News RSS. Fetches the feed via a proxy with JS rendering enabled
    to bypass blocking, then yields the direct Google News links for processing.
    """
    name = "gnews"

    def __init__(self, query: str):
        self.query = query

    def _feed_url(self) -> str:
        params = {"q": self.query, "hl": "en-US", "gl": "US", "ceid": "US:en"}
        return "https://news.google.com/rss/search?" + urlencode(params)

    def poll(self) -> Iterable[FoundItem]:
        feed_url = self._feed_url()
        feed_content = ""
        
        try:
            if SCRAPING_API_KEY:
                # **FIX**: Use the proxy with JS rendering enabled to fetch the main feed.
                proxy_url = "http://api.scraperapi.com"
                params = {
                    "api_key": SCRAPING_API_KEY, 
                    "url": feed_url, 
                    "country_code": "us",
                    "render": "true" # Enable JavaScript rendering
                }
                r = SESSION.get(proxy_url, params=params, timeout=90) # Increased timeout for JS rendering
                r.raise_for_status()
                feed_content = r.text
            else: # Fallback for local testing
                r = SESSION.get(feed_url, timeout=20)
                r.raise_for_status()
                feed_content = r.text
        except Exception as e:
            print(f"Failed to fetch Google News RSS feed: {e}")
            return # Exit if the feed can't be fetched

        feed = feedparser.parse(feed_content)
        
        for e in feed.entries[:30]:
            title = (e.get("title") or "").strip()
            # **FIX**: Yield the original Google News link. The redirect will be handled
            # by the fetch_and_summarize function, which also uses the proxy.
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

            yield FoundItem(feed_url, title, link, ts)
