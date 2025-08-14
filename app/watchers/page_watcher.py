# app/watchers/page_watcher.py
import re
import time
from typing import Iterable, Optional, List
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
from .base import Watcher, FoundItem
from ..config import FIRST_PARTY_ONLY, GOOD_WIRE_DOMAINS, BLOCK_DOMAINS
from ..net import make_session

SESSION = make_session()

# --------------------------------------------------------------------
# Detection constants
# --------------------------------------------------------------------
RESULT_WORDS = [
    "results", "earnings", "quarter", "q1", "q2", "q3", "q4",
    "trading update", "interim", "half-year", "half year",
    "full year", "annual", "preliminary", "interim report",
    "trading statement", "fy"
]

HREF_SIGNALS = re.compile(
    r"(news-details|press-releases|event-details|events|financials|quarterly-results|"
    r"static-files|/q[1-4]\b|/earnings|/results|fy\d{2,4})",
    re.I
)

TITLE_YEAR_QUARTER = re.compile(
    r"\b(q[1-4]\s+\d{4}|fy\d{2,4}|full\s+year\s+\d{4})\b", re.I
)

# --------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------
def _host(url: str) -> str:
    return urlparse(url).netloc.split(":")[0].lower()

def _allowed(base_url: str, candidate_url: str) -> bool:
    h = _host(candidate_url)
    if h in BLOCK_DOMAINS:
        return False
    base = _host(base_url)
    if h.endswith(base):
        return True
    if FIRST_PARTY_ONLY:
        return h in GOOD_WIRE_DOMAINS
    return True

def _parse_time(soup: BeautifulSoup) -> Optional[int]:
    t = soup.find("time", attrs={"datetime": True})
    if t and t.get("datetime"):
        try:
            from dateutil import parser as d
            return int(d.parse(t["datetime"]).timestamp())
        except Exception:
            return None
    return None

def _looks_like_results(text: str, href: str) -> bool:
    lt = (text or "").lower()
    if any(w in lt for w in RESULT_WORDS):
        return True
    if TITLE_YEAR_QUARTER.search(lt):
        return True
    return bool(HREF_SIGNALS.search(href or ""))

# --------------------------------------------------------------------
# Page Watcher
# --------------------------------------------------------------------
class PageWatcher(Watcher):
    name = "page"

    def __init__(
        self,
        page_url: str,
        allowed_domains: Optional[List[str]] = None,
        follow_detail: bool = False
    ):
        self.page_url = page_url
        self.allowed_domains = [d.lower() for d in (allowed_domains or [])]
        self.follow_detail = follow_detail

    def poll(self) -> Iterable[FoundItem]:
        res = SESSION.get(
            self.page_url,
            timeout=(10, 45),
            headers={"Referer": self.page_url}
        )
        res.raise_for_status()

        soup = BeautifulSoup(res.text, "lxml")
        page_ts = _parse_time(soup) or int(time.time())

        anchors = soup.select("a[href]")
        seen = set()

        for a in anchors:
            title = a.get_text(" ", strip=True) or ""
            href = a.get("href") or ""
            if not href:
                continue

            url = urljoin(self.page_url, href)
            host = _host(url)

            # Check allowed domains
            if self.allowed_domains and not any(
                host == d or host.endswith("." + d) for d in self.allowed_domains
            ):
                continue
            if not _allowed(self.page_url, url):
                continue
            if not _looks_like_results(title, href):
                continue
            if url in seen:
                continue
            seen.add(url)

            # Follow detail page if enabled
            if self.follow_detail:
                try:
                    detail_res = SESSION.get(url, timeout=(10, 30))
                    detail_res.raise_for_status()
                    detail_soup = BeautifulSoup(detail_res.text, "lxml")
                    detail_ts = _parse_time(detail_soup) or page_ts
                    yield FoundItem(self.page_url, title or href, url, detail_ts)
                    continue
                except Exception:
                    pass  # Fall back to page_ts if detail fetch fails

            yield FoundItem(self.page_url, title or href, url, page_ts)
