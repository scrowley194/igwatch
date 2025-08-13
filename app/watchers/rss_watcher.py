import time, email.utils, re
from typing import Iterable, Optional
import feedparser, requests
from .base import Watcher, FoundItem

def _http_get(url: str) -> requests.Response:
    return requests.get(url, timeout=20, headers={"User-Agent": "NEXT.io Earnings Watcher"})

class RSSWatcher(Watcher):
    name = "rss"

    def __init__(self, rss_url: str):
        self.rss_url = rss_url

    def poll(self) -> Iterable[FoundItem]:
        feed = feedparser.parse(self.rss_url)
        for e in feed.entries[:10]:
            published_ts = None
            if hasattr(e, "published"):
                try:
                    published_ts = int(time.mktime(email.utils.parsedate(e.published)))
                except Exception:
                    published_ts = None
            title = e.get("title", "").strip()
            href = e.get("link", "").strip()
            yield FoundItem(source=self.rss_url, title=title, url=href, published_ts=published_ts)

class RSSPageWatcher(Watcher):
    """Discover RSS/Atom links on a page (e.g., Q4/GCS RSS listing) and read them."""
    name = "rss_page"

    def __init__(self, page_url: str):
        self.page_url = page_url

    def _discover_feeds(self) -> list[str]:
        html = _http_get(self.page_url).text
        feeds = re.findall(r'href=["\'](.*?\.xml)["\']', html, flags=re.I)
        # Include explicit RSS endpoints if present in tags
        for m in re.findall(r'<link[^>]+type=["\']application/(?:rss|atom)\+xml["\'][^>]+href=["\']([^"\']+)', html, flags=re.I):
            feeds.append(m)
        # de-dup
        seen = []
        out = []
        for f in feeds:
            if f not in seen:
                seen.append(f)
                out.append(f)
        return out

    def poll(self) -> Iterable[FoundItem]:
        for feed in self._discover_feeds():
            sub = RSSWatcher(feed)
            for item in sub.poll():
                yield item
