# app/watchers/rns_lse.py
# RNS watcher for London-listed companies — yields (url, title) for results/trading updates.
# No edits to main yet. We'll wire later.

from __future__ import annotations
import os
import re
import time
import logging
from typing import List, Tuple, Dict
from datetime import datetime, timedelta, timezone

import requests
from bs4 import BeautifulSoup as BS

LOG = logging.getLogger("igwatch")

# Base endpoints
LSE_NEWS_LIST = "https://www.londonstockexchange.com/news"
LSE_COMP_NEWS = "https://www.londonstockexchange.com/stock/{epic}/company-news"

# Titles/text we consider relevant to results/financials
RESULT_PATTERNS = re.compile(
    r"(results|interim|half[-\s]?year|half[-\s]?yr|final results|preliminary|trading update|Q[1-4]|quarter|H[12]|FY|annual report|interims)",
    re.I,
)

# Some issuers use RNS categories; keep these if available
GOOD_CATEGORIES = {
    "Half-year Report",
    "Annual Financial Report",
    "Quarterly Results",
    "Trading Update",
    "Holding(s) in Company",  # optional; sometimes trading updates ride along
    "Miscellaneous",
}

DEFAULT_LOOKBACK_DAYS = int(os.getenv("START_FROM_DAYS", "90"))
DEFAULT_TIMEOUT = 30
POLITE_DELAY = 0.3

UA = os.getenv("LSE_USER_AGENT") or os.getenv("SEC_USER_AGENT") or "igwatch (contact: support@example.com)"


class RnsLseWatcher:
    """
    Polls LSE RNS pages for a set of EPICs (tickers) and yields (url, title).

    Env configuration (wire later):
      - LSE_EPICS=FLTR,ENT,888,BRAG,FDJ (comma-separated EPICs)
      - START_FROM_DAYS=90 (lookback)
      - LSE_USER_AGENT=... (optional; falls back to SEC_USER_AGENT)
    """

    def __init__(self, epics: List[str] | None = None, start_days: int | None = None):
        self.epics = epics or self._epics_from_env()
        self.start_days = int(start_days if start_days is not None else DEFAULT_LOOKBACK_DAYS)
        self.s = requests.Session()
        self.s.headers.update({
            "User-Agent": UA,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Encoding": "gzip, deflate",
            "Connection": "keep-alive",
        })

    def _epics_from_env(self) -> List[str]:
        env = os.getenv("LSE_EPICS", "")
        return [x.strip().upper() for x in env.split(",") if x.strip()]

    # ---- Public API (matches your pattern) ----
    def poll(self) -> List[Tuple[str, str]]:
        if not self.epics:
            LOG.info("RnsLseWatcher: no EPICs configured (LSE_EPICS).")
            return []
        cutoff = datetime.now(timezone.utc) - timedelta(days=self.start_days)
        out: List[Tuple[str, str]] = []
        for epic in self.epics:
            try:
                out.extend(self._poll_epic(epic, cutoff))
            except Exception as e:  # pragma: no cover
                LOG.exception("RNS LSE: failed for %s (%s)", epic, e)
        return out

    # ---- Internal helpers ----
    def _poll_epic(self, epic: str, cutoff: datetime) -> List[Tuple[str, str]]:
        url = LSE_COMP_NEWS.format(epic=epic)
        html = self._get(url)
        soup = BS(html, "lxml")

        # LSE uses cards/rows; we probe various selectors for robustness
        items = []
        items.extend(soup.select("article a[href*='/news-article/']"))
        items.extend(soup.select(".news__item a[href*='/news-article/']"))
        items.extend(soup.select("a[href*='/news-article/']"))

        seen_links = set()
        results: List[Tuple[str, str]] = []

        for a in items:
            href = a.get("href") or ""
            if "/news-article/" not in href:
                continue
            if href in seen_links:
                continue
            seen_links.add(href)

            title = (a.get_text() or "").strip()
            if not title:
                # try parent text if anchor empty
                parent = a.find_parent(["article", "li", "div"]) or a.parent
                title = (parent.get_text(" ", strip=True) or "").strip()

            # Find closest date text near the card
            date = self._extract_date_near(a)
            if date and date < cutoff:
                continue

            if not (RESULT_PATTERNS.search(title or "") or self._category_good(a)):
                continue

            full_url = self._absolutize(href)
            results.append((full_url, f"RNS {epic}: {title}"))

        time.sleep(POLITE_DELAY)
        return results

    def _absolutize(self, href: str) -> str:
        if href.startswith("http"):
            return href
        return f"https://www.londonstockexchange.com{href}"

    def _get(self, url: str) -> str:
        r = self.s.get(url, timeout=DEFAULT_TIMEOUT)
        if r.status_code in (403, 429):
            # Gentle backoff/retry once; LSE can rate-limit
            time.sleep(1.0)
            r = self.s.get(url, timeout=DEFAULT_TIMEOUT)
        r.raise_for_status()
        return r.text

    def _category_good(self, a) -> bool:
        # Sometimes the category appears as a sibling span or within the card
        txt = " ".join(x.get_text(" ", strip=True) for x in a.parents if getattr(x, "get_text", None))
        for cat in GOOD_CATEGORIES:
            if cat.lower() in txt.lower():
                return True
        return False

    def _extract_date_near(self, a) -> datetime | None:
        # Look for sibling/parent date nodes (common LSE patterns)
        candidates = []
        # sibling time/date tags
        candidates += a.find_all_next(["time", "span"], limit=3)
        # parent-contained
        parent = a.find_parent(["article", "li", "div"]) or a.parent
        if parent:
            candidates += parent.find_all(["time", "span", "p"], limit=5)
        for el in candidates:
            t = (el.get("datetime") or el.get_text(" ", strip=True) or "").strip()
            dt = self._parse_date(t)
            if dt:
                return dt
        return None

    def _parse_date(self, s: str) -> datetime | None:
        s = s.strip()
        if not s:
            return None
        # Try ISO first (often in <time datetime="...")
        try:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
            if not dt.tzinfo:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except Exception:
            pass
        # Common UK date like "21 August 2025" or "21 Aug 2025"
        for fmt in ("%d %B %Y", "%d %b %Y", "%d/%m/%Y"):
            try:
                dt = datetime.strptime(s, fmt).replace(tzinfo=timezone.utc)
                return dt
            except Exception:
                continue
        return None
