import time
import feedparser
from urllib.parse import urlparse
from .base import Watcher, FoundItem
from ..utils.log import get_logger
from ..config import GOOD_WIRE_DOMAINS, BLOCK_DOMAINS
from datetime import datetime, timezone, timedelta
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

class GoogleNewsWatcher:
    def __init__(self, start_days: int | None = None, **_):
        self.start_days = int(start_days or 0)

    def poll(self):
        cutoff = None
        if self.start_days > 0:
            cutoff = datetime.now(timezone.utc) - timedelta(days=self.start_days)
        # fetch items...
        # if cutoff is set, filter out items older than cutoff
        # yield (url, title)

# --------------------------------------------------------------------
# Press Wire Watcher
# --------------------------------------------------------------------
class PressWireWatcher:
    def __init__(self, start_days: int | None = None, **_):
        self.start_days = int(start_days or 0)

    def poll(self):
        cutoff = None
        if self.start_days > 0:
            cutoff = datetime.now(timezone.utc) - timedelta(days=self.start_days)
        # fetch items...
        # filter by cutoff if set
        # yield (url, title)
