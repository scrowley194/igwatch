import time
import re
import requests
from bs4 import BeautifulSoup
from urllib.parse import urlparse, urljoin
from .base import Watcher, FoundItem
from ..utils.log import get_logger
from ..config import GOOD_WIRE_DOMAINS, BLOCK_DOMAINS

logger = get_logger("page_watcher")

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
# Page Watcher
# --------------------------------------------------------------------
class PageWatcher(Watcher):
    def __init__(self, base_url: str, allowed_domains=None, follow_detail=False):
        self.base_url = base_url
        self.allowed_domains = allowed_domains or []
        self.follow_detail = follow_detail

    def poll(self):
        logger.info("Polling HTML page: %s", self.base_url)
        try:
            r = requests.get(self.base_url, timeout=30)
            r.raise_for_status()
        except Exception as e:
            logger.error("PageWatcher failed to fetch %s: %s", self.base_url, e)
            return

        soup = BeautifulSoup(r.text, "lxml")
        for a in soup.find_all("a", href=True):
            url = urljoin(self.base_url, a["href"])
            if not _is_primary_source(url):
                logger.info("PageWatcher skip (not primary): %s", url)
                continue
            if any(bad in url for bad in BLOCK_DOMAINS):
                logger.info("PageWatcher skip (blocked domain): %s", url)
                continue
            title = (a.get_text() or "").strip()
            if not title:
                continue
            yield FoundItem("page", title, url, None)
