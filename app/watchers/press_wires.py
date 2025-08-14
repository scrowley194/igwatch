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
# Google News RSS watcher (Rewritten to use the proxy for the main feed and for resolving redirects)
# ---------------------------
class GoogleNewsWatcher(Watcher):
    """
    Uses Google News RSS and resolves the redirect to the final publisher URL
    using a proxy to avoid being blocked.
    """
    name = "gnews"

    def __init__(self, query: str):
        self.query = query

    def _feed_url(self) -> str:
        params = {"q": self.query, "hl": "en-US", "gl": "US", "ceid": "US:en"}
        return "https://news.google.com/rss/search?" + urlencode(params)

    def poll(self) -> Iterable[FoundItem]:
        feed_url = self._feed_url()
        
        # **FIX**: Fetch the main RSS feed through the proxy to avoid being blocked by Google.
        feed_content = ""
        try:
            if SCRAPING_API_KEY:
                proxy_url = "http://api.scraperapi.com"
                params = {"api_key": SCRAPING_API_KEY, "url": feed_url, "country_code": "us"}
                r = SESSION.get(proxy_url, params=params, timeout=60)
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
            link = (e.get("link") or "").strip()
            if not title or not link:
                continue

            # Resolve Google News redirect to the real article URL using the proxy
            final_url = link
            try:
                if "news.google.com" in link and SCRAPING_API_KEY:
                    proxy_url = "http://api.scraperapi.com"
                    params = {"api_key": SCRAPING_API_KEY, "url": link, "country_code": "us"}
                    # Use a HEAD request for efficiency, we only need the final URL
                    r = SESSION.head(proxy_url, params=params, timeout=20, allow_redirects=True)
                    # ScraperAPI doesn't follow redirects on HEAD, so we parse its response URL
                    final_url = r.url
                elif "news.google.com" in link: # Fallback for local testing without proxy
                    r = SESSION.get(link, timeout=(5, 15), allow_redirects=True)
                    final_url = r.url
            except Exception:
                pass

            ts = None
            pub = e.get("published")
            if pub:
                try:
                    ts = int(time.mktime(email.utils.parsedate(pub)))
                except Exception:
                    ts = None

            yield FoundItem(feed_url, title, final_url, ts)
