# app/watchers/press_wires.py
import time, re
from typing import Iterable, Optional
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
from .base import Watcher, FoundItem
from ..config import GOOD_WIRE_DOMAINS, BLOCK_DOMAINS
from ..net import make_session

SESSION = make_session()

def _host(u: str) -> str:
    return urlparse(u).netloc.split(":")[0].lower()

RESULT_HINTS = re.compile(r"(q[1-4]\b|quarter|results|earnings|guidance|interim|half[- ]year|full year)", re.I)

class PressWireWatcher(Watcher):
    name = "wire"

    def __init__(self, page_url: str, must_include: Optional[str] = None):
        self.page_url = page_url
        self.must_include = (must_include or "").lower().strip()

    def poll(self) -> Iterable[FoundItem]:
        r = SESSION.get(self.page_url, timeout=(8, 30))
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "lxml")
        anchors = soup.select("a[href]") or []
        seen = set()

        for a in anchors:
            txt = (a.get_text(" ", strip=True) or "")
            href = a.get("href") or ""
            if not txt or not href:
                continue
            url = urljoin(self.page_url, href)
            h = _host(url)
            if h in BLOCK_DOMAINS or (h not in GOOD_WIRE_DOMAINS):
                continue
            lt = txt.lower()
            if self.must_include and self.must_include not in lt:
                continue
            if not RESULT_HINTS.search(lt) and not RESULT_HINTS.search(href):
                continue
            if url in seen:
                continue
            seen.add(url)
            yield FoundItem(self.page_url, txt, url, int(time.time()))

class GoogleNewsWatcher(Watcher):
    name = "gnews"
    # (Keep disabled in your YAML to avoid media write-ups.)
    def __init__(self, query: str):
        self.query = query
    def poll(self) -> Iterable[FoundItem]:
        return []  # Intentionally empty when FIRST_PARTY_ONLY=true
