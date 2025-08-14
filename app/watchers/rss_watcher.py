# app/watchers/rss_watcher.py
import time, email.utils, re
from typing import Iterable, Optional, List
from urllib.parse import urlparse

import feedparser
from .base import Watcher, FoundItem
from ..config import FIRST_PARTY_ONLY, GOOD_WIRE_DOMAINS, BLOCK_DOMAINS, BROWSER_UA
from ..net import make_session

SESSION = make_session()

def _host(url: str) -> str:
    return urlparse(url).netloc.split(":")[0].lower()

def _allowed(source_url: str, entry_url: str) -> bool:
    h = _host(entry_url)
    if h in BLOCK_DOMAINS:
        return False
    base = _host(source_url)
    if h.endswith(base):
        return True
    if FIRST_PARTY_ONLY:
        return h in GOOD_WIRE_DOMAINS
    return True

class RSSWatcher(Watcher):
    name = "rss"

    def __init__(self, rss_url: str, allowed_domains: Optional[List[str]] = None):
        self.rss_url = rss_url
        self.allowed_domains = [d.lower() for d in (allowed_domains or [])]

    def poll(self) -> Iterable[FoundItem]:
        # Try with feedparser requesting headers; fall back to manual GET then parse.
        fp = feedparser.parse(self.rss_url, request_headers={"User-Agent": BROWSER_UA})
        if not getattr(fp, "entries", None):
            r = SESSION.get(self.rss_url, timeout=(8, 30))
            r.raise_for_status()
            fp = feedparser.parse(r.text)

        for e in fp.entries[:50]:
            title = (e.get("title") or "").strip()
            link  = (e.get("link")  or "").strip()
            if not title or not link:
                continue

            h = _host(link)
            if self.allowed_domains and not any(h == d or h.endswith("." + d) for d in self.allowed_domains):
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
        r = SESSION.get(self.page_url, timeout=(8, 30))
        r.raise_for_status()
        html = r.text
        feeds = []
        feeds += re.findall(r'<link[^>]+type=["\']application/(?:rss|atom)\+xml["\'][^>]+href=["\']([^"\']+)', html, flags=re.I)
        feeds += re.findall(r'href=["\'](.*?\.xml)["\']', html, flags=re.I)
        out, seen = [], set()
        for f in feeds:
            if f not in seen:
                seen.add(f); out.append(f)
        return out

    def poll(self) -> Iterable[FoundItem]:
        for url in self._discover_feeds():
            yield from RSSWatcher(url).poll()
