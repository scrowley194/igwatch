# rss_watchers.py
import time, email.utils, re
from typing import Iterable, List, Optional
import feedparser
from urllib.parse import urlparse
from .base import Watcher, FoundItem
from ..config import FIRST_PARTY_ONLY, GOOD_WIRE_DOMAINS, BLOCK_DOMAINS

def _host(url: str) -> str:
    return urlparse(url).netloc.split(":")[0].lower()

def _allowed(source_url: str, entry_url: str) -> bool:
    host = _host(entry_url)
    if host in BLOCK_DOMAINS:
        return False
    base_host = _host(source_url)
    if host.endswith(base_host):
        return True
    if FIRST_PARTY_ONLY:
        return host in GOOD_WIRE_DOMAINS
    return True

class RSSWatcher(Watcher):
    name = "rss"

    def __init__(self, rss_url: str, allowed_domains: Optional[List[str]] = None):
        self.rss_url = rss_url
        self.allowed_domains = [d.lower() for d in (allowed_domains or [])]

    def poll(self) -> Iterable[FoundItem]:
        feed = feedparser.parse(self.rss_url)
        for e in feed.entries[:25]:
            title = (e.get("title") or "").strip()
            link = (e.get("link") or "").strip()
            if not title or not link:
                continue
            host = _host(link)
            if self.allowed_domains and not any(host.endswith(d) for d in self.allowed_domains):
                continue
            if not _allowed(self.rss_url, link):
                continue
            ts = None
            if hasattr(e, "published"):
                try:
                    ts = int(time.mktime(email.utils.parsedate(e.published)))
                except Exception:
                    ts = None
            yield FoundItem(self.rss_url, title, link, ts)

class RSSPageWatcher(Watcher):
    name = "rss_page"
    def __init__(self, page_url: str):
        self.page_url = page_url
    def _discover_feeds(self) -> list[str]:
        import requests
        html = requests.get(self.page_url, timeout=20, headers={"User-Agent":"Mozilla/5.0"}).text
        feeds = []
        feeds += re.findall(r'<link[^>]+type=["\']application/(?:rss|atom)\+xml["\'][^>]+href=["\']([^"\']+)', html, flags=re.I)
        feeds += re.findall(r'href=["\'](.*?\.xml)["\']', html, flags=re.I)
        seen = set(); out=[]
        for f in feeds:
            if f not in seen:
                seen.add(f); out.append(f)
        return out
    def poll(self) -> Iterable[FoundItem]:
        for url in self._discover_feeds():
            yield from RSSWatcher(url).poll()
