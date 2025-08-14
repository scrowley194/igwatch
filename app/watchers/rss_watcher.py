import time, email.utils
from typing import Iterable
import feedparser
from .base import Watcher, FoundItem

class RSSWatcher(Watcher):
    name = "rss"
    def __init__(self, rss_url: str):
        self.rss_url = rss_url
    def poll(self) -> Iterable[FoundItem]:
        feed = feedparser.parse(self.rss_url)
        for e in feed.entries[:20]:
            ts = None
            if hasattr(e, "published"):
                try:
                    ts = int(time.mktime(email.utils.parsedate(e.published)))
                except Exception:
                    ts = None
            yield FoundItem(self.rss_url, e.get("title","").strip(), e.get("link","").strip(), ts)

class RSSPageWatcher(Watcher):
    name = "rss_page"
    def __init__(self, page_url: str):
        self.page_url = page_url
    def _discover_feeds(self) -> list[str]:
        import requests, re
        html = requests.get(self.page_url, timeout=20, headers={"User-Agent":"NEXT.io Earnings Watcher"}).text
        feeds = []
        feeds += [m for m in re.findall(r'<link[^>]+type=["\']application/(?:rss|atom)\+xml["\'][^>]+href=["\']([^"\']+)', html, flags=re.I)]
        feeds += [m for m in re.findall(r'href=["\'](.*?\.xml)["\']', html, flags=re.I)]
        # de-dup
        seen=set(); out=[]
        for f in feeds:
            if f not in seen:
                seen.add(f); out.append(f)
        return out
    def poll(self) -> Iterable[FoundItem]:
        for url in self._discover_feeds():
            yield from RSSWatcher(url).poll()
