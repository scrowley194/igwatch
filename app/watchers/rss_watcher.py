# app/watchers/rss_watcher.py
import time, email.utils, re, logging
from urllib.parse import urlparse

import feedparser

from .base import Watcher, FoundItem
from ..config import FIRST_PARTY_ONLY, GOOD_WIRE_DOMAINS, BLOCK_DOMAINS

logger = logging.getLogger(__name__)

def _host(u: str) -> str:
    return urlparse(u).netloc.split(":")[0].lower()

def _allowed(source_url: str, entry_url: str, allowed_domains=None) -> bool:
    allowed_domains = [d.lower() for d in (allowed_domains or [])]
    host = _host(entry_url)
    if host in BLOCK_DOMAINS:
        return False
    base_host = _host(source_url)
    if allowed_domains and not any(host == d or host.endswith("." + d) for d in allowed_domains):
        return False
    if host == base_host or host.endswith("." + base_host):
        return True
    if FIRST_PARTY_ONLY:
        return host in GOOD_WIRE_DOMAINS
    return True

class RSSWatcher(Watcher):
    name = "rss"

    def __init__(self, rss_url, allowed_domains=None):
        self.rss_url = rss_url
        self.allowed_domains = allowed_domains or []

    def poll(self):
        try:
            feed = feedparser.parse(self.rss_url)
        except Exception as e:
            logger.debug("RSS parse failed for %s: %s", self.rss_url, e)
            return []

        for e in feed.entries[:25]:
            title = (e.get("title") or "").strip()
            link = (e.get("link") or "").strip()
            if not title or not link:
                continue
            if not _allowed(self.rss_url, link, self.allowed_domains):
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

    def __init__(self, page_url):
        self.page_url = page_url

    def _discover_feeds(self):
        # light discovery; we leave heavy fetching to PageWatcher
        import requests
        try:
            r = requests.get(self.page_url, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
            html = r.text
        except Exception:
            return []
        feeds = []
        feeds += re.findall(r'<link[^>]+type=["\']application/(?:rss|atom)\+xml["\'][^>]+href=["\']([^"\']+)', html, flags=re.I)
        feeds += re.findall(r'href=["\'](.*?\.xml)["\']', html, flags=re.I)
        seen = set(); out = []
        for f in feeds:
            if f not in seen:
                seen.add(f); out.append(f)
        return out

    def poll(self):
        for url in self._discover_feeds():
            yield from RSSWatcher(url).poll()
