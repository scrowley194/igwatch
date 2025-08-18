import time
import feedparser
from urllib.parse import urlparse
from .base import Watcher, FoundItem
from ..utils.log import get_logger
from ..config import GOOD_WIRE_DOMAINS, BLOCK_DOMAINS
import re

logger = get_logger("press_wires")

# --------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------
def _is_primary_source(url: str) -> bool:
    """Allow only trusted wires or obvious IR/press sections."""
    h = urlparse(url).netloc.lower()
    if any(h == d or h.endswith("." + d) for d in GOOD_WIRE_DOMAINS):
        return True
    path = urlparse(url).path.lower()
    if re.search(r"/investors?/|/investor-relations?/|/press(-releases?)?/|/news(room|centre|center)/|/media(-center|-centre|-room)?/|/financial[-_]reports?/|/results?/", path):
        return True
    return False

# --------------------------------------------------------------------
# Google News Watcher
# --------------------------------------------------------------------
class GoogleNewsWatcher(Watcher):
    def __init__(self, query: str):
        self.query = query
        self.feed_url = f"https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en"

    def poll(self):
        logger.info("Polling Google News for query: %s", self.query)
        feed = feedparser.parse(self.feed_url)
        for entry in feed.entries:
            url = entry.link
            if not _is_primary_source(url):
                logger.info("GoogleNews skip (not primary): %s", url)
                continue
            if any(bad in url for bad in BLOCK_DOMAINS):
                logger.info("GoogleNews skip (blocked domain): %s", url)
                continue
            ts = None
            if hasattr(entry, 'published_parsed') and entry.published_parsed:
                ts = int(time.mktime(entry.published_parsed))
            yield FoundItem("googlenews", entry.title, url, ts)

# --------------------------------------------------------------------
# Press Wire Watcher
# --------------------------------------------------------------------
class PressWireWatcher(Watcher):
    def __init__(self, listing_url: str):
        self.listing_url = listing_url

    def poll(self):
        logger.info("Polling PressWire listing: %s", self.listing_url)
        feed = feedparser.parse(self.listing_url)
        for entry in feed.entries:
            url = entry.link
            if not _is_primary_source(url):
                logger.info("PressWire skip (not primary): %s", url)
                continue
            if any(bad in url for bad in BLOCK_DOMAINS):
                logger.info("PressWire skip (blocked domain): %s", url)
                continue
            ts = None
            if hasattr(entry, 'published_parsed') and entry.published_parsed:
                ts = int(time.mktime(entry.published_parsed))
            yield FoundItem("presswire", entry.title, url, ts)
