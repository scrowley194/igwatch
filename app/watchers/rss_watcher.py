# rss_watchers.py
import time
import email.utils
import re
import requests
import feedparser
from typing import Iterable, List
from .base import Watcher, FoundItem

USER_AGENT = "NEXT.io Earnings Watcher (contact: stuatnext@gmail.com)"


class RSSWatcher(Watcher):
    """Watches a given RSS/Atom feed for new items."""

    name = "rss"

    def __init__(self, rss_url: str):
        self.rss_url = rss_url

    def poll(self) -> Iterable[FoundItem]:
        try:
            feed = feedparser.parse(self.rss_url)
        except Exception:
            return []

        for entry in feed.entries[:20]:
            ts = None
            if hasattr(entry, "published"):
                try:
                    ts = int(time.mktime(email.utils.parsedate(entry.published)))
                except Exception:
                    ts = None

            yield FoundItem(
                self.rss_url,
                entry.get("title", "").strip(),
                entry.get("link", "").strip(),
                ts,
            )


class RSSPageWatcher(Watcher):
    """
    Finds RSS/Atom feeds on a given page and polls them.
    Useful when a site doesn't provide a known static feed URL.
    """

    name = "rss_page"

    def __init__(self, page_url: str):
        self.page_url = page_url

    def _discover_feeds(self) -> List[str]:
        """Scrape the page to find feed URLs."""
        try:
            res = requests.get(self.page_url, timeout=20, headers={"User-Agent": USER_AGENT})
            res.raise_for_status()
            html = res.text
        except requests.RequestException:
            return []

        # Match link tags with RSS/Atom type
        feed_links = re.findall(
            r'<link[^>]+type=["\']application/(?:rss|atom)\+xml["\'][^>]+href=["\']([^"\']+)',
            html,
            flags=re.I,
        )

        # Match any href ending with .xml
        xml_links = re.findall(r'href=["\'](.*?\.xml)["\']', html, flags=re.I)

        # Combine, de-duplicate, and return
        seen = set()
        feeds = []
        for f in feed_links + xml_links:
            if f not in seen:
                seen.add(f)
                feeds.append(f)

        return feeds

    def poll(self) -> Iterable[FoundItem]:
        for url in self._discover_feeds():
            yield from RSSWatcher(url).poll()
