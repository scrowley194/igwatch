# app/watchers/rns_lse.py
# London Stock Exchange RNS watcher (primary-source company notices)
# Produces (url, title) pairs for results/trading updates within a lookback window.
# Standalone: no edits to main yet. Wire later alongside other watchers.

from __future__ import annotations
import os
import re
import time
import logging
from typing import List, Tuple, Optional
from datetime import datetime, timedelta, timezone

import requests
from bs4 import BeautifulSoup

LOG = logging.getLogger("igwatch")

# ------------------------- Config & Defaults -------------------------
DEFAULT_KEYWORDS = re.compile(
    r"(interim|half[-\s]?year|halfyear|h1|h2|q[1-4]|quarter|trading update|results|preliminary|full[-\s]?year|fy)",
    re.I,
)
LSE_BASE = "https://www.londonstockexchange.com"


class _Http:
    """Polite HTTP client with retry/backoff for LSE pages."""

    def __init__(self, ua: Optional[str] = None, max_retries: int = 5, polite_delay: float = 0.25):
        self.s = requests.Session()
        self.s.headers.update({
            "User-Agent": ua or os.getenv("LSE_USER_AGENT") or os.getenv("SEC_USER_AGENT") or "igwatch (contact: support@example.com)",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Encoding": "gzip, deflate",
            "Connection": "keep-alive",
        })
        self.max_retries = max_retries
        self.polite_delay = polite_delay

    def get(self, url: str, **kw) -> requests.Response:
        backoff = 0.5
        last = None
        for _ in range(self.max_retries):
            r = self.s.get(url, timeout=30, **kw)
            if r.status_code in (403, 429):
                sleep = float(r.headers.get("Retry-After") or backoff)
                LOG.debug("LSE backoff %ss for %s (%s)", sleep, url, r.status_code)
                time.sleep(sleep)
                backoff = min(backoff * 2, 8.0)
                last = r
                continue
            r.raise_for_status()
            time.sleep(self.polite_delay)
            return r
        if last is not None:
            last.raise_for_status()
        raise RuntimeError("LSE request failed and no response to raise")


# ------------------------- Parsing helpers -------------------------

def _abs_url(href: str) -> str:
    if href.startswith("http"):
        return href
    if not href.startswith("/"):
        href = "/" + href
    return LSE_BASE + href


def _parse_date(text: str) -> Optional[datetime]:
    text = (text or "").strip()
    # Common formats seen on LSE (examples): "21 Aug 2025 07:00"
    for fmt in ("%d %b %Y %H:%M", "%d %b %Y", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(text, fmt)
            if not dt.tzinfo:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except ValueError:
            continue
    return None


def _extract_items_from_list_html(html: str, epic: str) -> List[Tuple[str, str, Optional[datetime]]]:
    """Return list of (url, title, date) from an LSE company news list page."""
    soup = BeautifulSoup(html, "lxml")

    # Strategy 1: anchor cards that link to /news-article/{EPIC}/...
    anchors = soup.select(f'a[href*="/news-article/{epic.upper()}/"], a[href*="/news-article/{epic.lower()}/"]')
    out: List[Tuple[str, str, Optional[datetime]]] = []

    for a in anchors:
        href = a.get("href") or ""
        title = a.get_text(strip=True)
        if not href or not title:
            continue
        url = _abs_url(href)

        # Try to find a nearby date element
        date_txt = None
        parent = a.parent
        for _ in range(4):  # climb up a few levels looking for a time/date element
            if not parent:
                break
            time_el = parent.find("time")
            if time_el and (time_el.get("datetime") or time_el.get_text(strip=True)):
                date_txt = time_el.get("datetime") or time_el.get_text(strip=True)
                break
            # look for generic date spans
            dspan = parent.find(lambda tag: tag.name in ("span", "div") and "date" in " ".join(tag.get("class", [])))
            if dspan and dspan.get_text(strip=True):
                date_txt = dspan.get_text(strip=True)
                break
            parent = parent.parent

        dt = _parse_date(date_txt) if date_txt else None
        out.append((url, title, dt))

    # Strategy 2: generic news item containers (fallback)
    if not out:
        for item in soup.select(".news__item, .article, li, .component-news-item"):
            a = item.find("a", href=True)
            if not a:
                continue
            href = a["href"]
            if f"/news-article/{epic.upper()}/" not in href and f"/news-article/{epic.lower()}/" not in href:
                continue
            title = a.get_text(strip=True)
            if not title:
                continue
            url = _abs_url(href)
            dt = None
            t = item.find("time")
            if t and (t.get("datetime") or t.get_text(strip=True)):
                dt = _parse_date(t.get("datetime") or t.get_text(strip=True))
            else:
                dspan = item.find(lambda tag: tag.name in ("span", "div") and "date" in " ".join(tag.get("class", [])))
                if dspan:
                    dt = _parse_date(dspan.get_text(strip=True))
            out.append((url, title, dt))

    return out


def _fetch_detail_date(client: _Http, url: str) -> Optional[datetime]:
    try:
        html = client.get(url).text
    except Exception:
        return None
    soup = BeautifulSoup(html, "lxml")
    # Prefer <time datetime="..."></time>
    t = soup.find("time")
    if t and (t.get("datetime") or t.get_text(strip=True)):
        return _parse_date(t.get("datetime") or t.get_text(strip=True))
    # Sometimes date appears in a meta tag
    meta = soup.find("meta", {"property": "article:published_time"})
    if meta and meta.get("content"):
        return _parse_date(meta["content"])  # try ISO format
    return None


# ------------------------- Watcher -------------------------

class RnsLseWatcher:
    """
    Scrapes RNS (Regulatory News Service) entries from the London Stock Exchange
    company news pages for configured EPICs (tickers), and yields (url, title)
    for recent results-oriented announcements.

    Environment variables (wire later in CI/main):
      - LSE_EPICS=FLTR,ENT,PPB,... (comma-separated EPICs)
      - START_FROM_DAYS (lookback window; default 90)
      - LSE_MAX_PAGES (pagination depth per EPIC; default 2)
      - LSE_USER_AGENT (optional; falls back to SEC_USER_AGENT or default UA)
      - LSE_KEYWORDS (override keywords; regex or simple words separated by '|')
    """

    def __init__(self, start_days: Optional[int] = None):
        self.start_days = int(start_days if start_days is not None else os.getenv("START_FROM_DAYS", 90))
        self.max_pages = int(os.getenv("LSE_MAX_PAGES", 2))
        self.client = _Http()
        # Compile keyword regex
        kw_env = os.getenv("LSE_KEYWORDS", "")
        self.keywords = re.compile(kw_env, re.I) if kw_env else DEFAULT_KEYWORDS

    @staticmethod
    def _epics() -> List[str]:
        env = os.getenv("LSE_EPICS", "")
        return [x.strip().upper() for x in env.split(",") if x.strip()]

    @staticmethod
    def _list_url(epic: str, page: int) -> str:
        return f"{LSE_BASE}/stock/{epic}/company-news?page={page}"

    def poll(self) -> List[Tuple[str, str]]:
        epics = self._epics()
        if not epics:
            LOG.info("RnsLseWatcher: no EPICs configured (LSE_EPICS).")
            return []

        cutoff = datetime.now(timezone.utc) - timedelta(days=self.start_days)
        results: List[Tuple[str, str]] = []

        for epic in epics:
            for page in range(1, self.max_pages + 1):
                url = self._list_url(epic, page)
                try:
                    html = self.client.get(url).text
                except Exception as e:
                    LOG.warning("LSE list fetch failed for %s p%d: %s", epic, page, e)
                    continue

                items = _extract_items_from_list_html(html, epic)
                if not items:
                    # Stop paging if nothing found on page 1 (likely markup change or empty)
                    if page == 1:
                        LOG.info("LSE: no items found for %s (page 1)", epic)
                    break

                for item_url, title, dt in items:
                    # Quick keyword screen
                    if not self.keywords.search(title or ""):
                        continue
                    # Ensure we have a date; fetch detail if missing and title matched
                    if dt is None:
                        dt = _fetch_detail_date(self.client, item_url)
                    if dt is not None and dt < cutoff:
                        continue

                    results.append((item_url, title))

                # If the newest item on this page is already older than cutoff, stop paging
                newest_dt = None
                for _, _, dt in items:
                    if dt is None:
                        newest_dt = None
                        break
                    if newest_dt is None or dt > newest_dt:
                        newest_dt = dt
                if newest_dt is not None and newest_dt < cutoff:
                    break

        return results
