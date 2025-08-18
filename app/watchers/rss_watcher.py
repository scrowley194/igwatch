import time
import feedparser
from urllib.parse import urlparse
from .base import Watcher, FoundItem
from ..utils.log import get_logger
from ..config import GOOD_WIRE_DOMAINS, BLOCK_DOMAINS
import re

logger = get_logger("rss_watcher")

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
# RSS Watcher
# --------------------------------------------------------------------
class RSSPageWatcher(Watcher):
    def __init__(self, feed_url: str):
        self.feed_url = feed_url

    def poll(self):
        logger.info("Polling RSS feed: %s", self.feed_url)
        feed = feedparser.parse(self.feed_url)
        for entry in feed.entries:
            url = entry.link
            if not _is_primary_source(url):
                logger.info("RSS skip (not primary): %s", url)
                continue
            if any(bad in url for bad in BLOCK_DOMAINS):
                logger.info("RSS skip (blocked domain): %s", url)
                continue
            ts = None
            if hasattr(entry, 'published_parsed') and entry.published_parsed:
                ts = int(time.mktime(entry.published_parsed))
            yield FoundItem("rss", entry.title, url, ts)
