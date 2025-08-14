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

# Keywords that indicate results/earnings
RESULT_WORDS = [
    "results", "earnings", "quarter", "q1", "q2", "q3", "q4",
    "trading update", "interim", "half-year", "half year", "full year", "annual",
    "preliminary", "interim report", "trading statement",
    "fy",  # for "FY2025"
]

# Regex patterns for link URLs that often indicate results pages
HREF_SIGNALS = re.compile(
    r"(news-details|press-releases|event-details|events|financials|quarterly-results|"
    r"static-files|/q[1-4]\b|/earnings|/results|fy\d{2,4})",
    re.I
)

# Regex to match "Q2 2025", "FY2024", "Full Year 2023" patterns
TITLE_YEAR_QUARTER = re.compile(
    r"\b(q[1-4]\s+\d{4}|fy\d{2,4}|full\s+year\s+\d{4})\b", re.I
)


def _host(u: str) -> str:
    return urlparse(u).netloc.split(":")[0].lower()


def _allowed(base_url: str, candidate_url: str) -> bool:
    h = _host(candidate_url)
    if h in BLOCK_DOMAINS:
        return False
    base = _host(base_url)
    if h.endswith(base):  # same site
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
    # Match any keyword OR link pattern OR title with explicit quarter/year reference
    if any(w in lt for w in RESULT_WORDS):
        return True
    if TITLE_YEAR_QUARTER.search(lt):
        return True
    return bool(HREF_SIGNALS.search(href or ""))


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
        # generous read timeout; Q4 pages can be slow
        res = SESSION.get(self.page_url, timeout=(10, 45), headers={"Referer": self.page_url})
        res.raise_for_status()

        soup = BeautifulSoup(res.text, "lxml")
        page_ts = _parse_time(soup) or int(time.time())

        anchors = soup.select("a[href]")  # broad; we filter below
        seen = set()

        for a in anchors:
            title = a.get_text(" ", strip=True) or ""
            href = a.get("href") or ""
            if not href:
                continue

            url = urljoin(self.page_url, href)
            host = _host(url)

            # domain allowlist (per-company)
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

            # If follow_detail is enabled, try to get the detail page timestamp
            if self.follow_detail:
                try:
                    detail_res = SESSION.get(url, timeout=(10, 30))
                    detail_res.raise_for_status()
                    detail_soup = BeautifulSoup(detail_res.text, "lxml")
                    detail_ts = _parse_time(detail_soup) or page_ts
                    yield FoundItem(self.page_url, title or href, url, detail_ts)
                    continue
                except Exception:
                    # If detail page fails, still yield with page_ts
                    pass

            yield FoundItem(self.page_url, title or href, url, page_ts)
