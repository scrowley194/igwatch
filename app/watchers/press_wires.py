# app/watchers/press_wires.py
import time
import urllib.parse as up
from typing import Iterable, Optional
from dataclasses import dataclass

import feedparser
from .base import Watcher, FoundItem
from ..net import make_session
from ..config import GOOD_WIRE_DOMAINS

SESSION = make_session()

# Small helper to build a GNews RSS query that biases to Business Wire / GlobeNewswire
def _gnews_rss_query(raw_term: str) -> str:
    """
    Accepts either a full BusinessWire/GlobeNewswire search URL or a plain term.
    Extracts 'searchTerm'/'keyword' if present; otherwise uses raw_term as-is.
    """
    # If user passed a full URL (legacy YAML), try to extract the query term
    try:
        parsed = up.urlparse(raw_term)
        q = up.parse_qs(parsed.query)
        term = q.get("searchTerm", q.get("keyword", [raw_term]))[0]
    except Exception:
        term = raw_term

    # Bias to wires; prefer earnings-y language
    # You can tune 'when:90d' to your START_FROM_DAYS if you want
    gq = f'site:businesswire.com OR site:globenewswire.com "{term}" (earnings OR results OR quarter OR FY OR "interim") when:90d'
    params = {
        "q": gq,
        "hl": "en-US",
        "gl": "US",
        "ceid": "US:en",
    }
    return "https://news.google.com/rss/search?" + up.urlencode(params)

@dataclass
class _RssItem:
    title: str
    link: str
    published_ts: Optional[int]

def _iter_gnews_items(q_url: str):
    fp = feedparser.parse(q_url)
    for e in fp.entries:
        link = getattr(e, "link", "")
        title = getattr(e, "title", "") or link
        ts = None
        try:
            ts = int(time.mktime(e.published_parsed)) if getattr(e, "published_parsed", None) else None
        except Exception:
            ts = None
        yield _RssItem(title=title, link=link, published_ts=ts)

def _host(url: str) -> str:
    from urllib.parse import urlparse
    return (urlparse(url).netloc or "").lower()

class PressWireWatcher(Watcher):
    """
    YAML:
      - type: wire
        url: <either the old BusinessWire/GlobeNewswire 'search' URL or just a term like "DraftKings">

    We ignore the HTML search page entirely and query Google News RSS instead.
    """
    name = "wire"

    def __init__(self, url: str):
        # Store the original "url" field; we use it as the term
        self.term_or_url = url
        self.gnews_url = _gnews_rss_query(url)

    def poll(self) -> Iterable[FoundItem]:
        for it in _iter_gnews_items(self.gnews_url):
            # Keep only direct links to GOOD_WIRE_DOMAINS
            h = _host(it.link)
            if GOOD_WIRE_DOMAINS and not any(h == d or h.endswith("." + d) for d in GOOD_WIRE_DOMAINS):
                continue
            yield FoundItem(self.gnews_url, it.title, it.link, it.published_ts or int(time.time()))
