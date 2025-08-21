# app/watchers/ir_sources.py
# Company IR watcher (RSS first, HTML fallback) for primary-source results updates.
# Produces (url, title) pairs filtered by keywords and a lookback window.
# Standalone: no edits to main yet. Wire later alongside other watchers.

from __future__ import annotations
import os
import re
import time
import logging
from typing import List, Tuple, Dict, Any, Optional, Iterable
from datetime import datetime, timedelta, timezone

import requests
import feedparser
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
from dateutil import parser as dateparser

LOG = logging.getLogger("igwatch")

# ---------------------------------------------------------------------
# Defaults & Config
# ---------------------------------------------------------------------
DEFAULT_KEYWORDS = re.compile(
    r"(results|earnings|trading update|quarter|q[1-4]|interim|half[-\s]?year|halfyear|h1|h2|full[-\s]?year|fy|financial statements|annual report)",
    re.I,
)


class _Http:
    """Polite HTTP client with retry/backoff."""

    def __init__(self, ua: Optional[str] = None, max_retries: int = 5, polite_delay: float = 0.2):
        self.s = requests.Session()
        self.s.headers.update({
            "User-Agent": ua or os.getenv("IR_USER_AGENT") or os.getenv("SEC_USER_AGENT") or "igwatch (contact: support@example.com)",
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
                LOG.debug("IR backoff %ss for %s (%s)", sleep, url, r.status_code)
                time.sleep(sleep)
                backoff = min(backoff * 2, 8.0)
                last = r
                continue
            r.raise_for_status()
            time.sleep(self.polite_delay)
            return r
        if last is not None:
            last.raise_for_status()
        raise RuntimeError("IR request failed and no response to raise")


# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------

def _safe_parse_date(text: Optional[str]) -> Optional[datetime]:
    if not text:
        return None
    try:
        dt = dateparser.parse(text, fuzzy=True)
        if not dt.tzinfo:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def _abs_url(base: str, href: str) -> str:
    try:
        return urljoin(base, href)
    except Exception:
        # Fall back: if href already absolute, return as-is
        if href.startswith("http"):
            return href
        return href


def _load_issuers_from_yaml(yaml_path: str) -> List[Dict[str, Any]]:
    issuers: List[Dict[str, Any]] = []
    if not yaml_path or not os.path.exists(yaml_path):
        LOG.info("IR: YAML not found or unset (%s)", yaml_path)
        return issuers
    try:
        import yaml
    except Exception:
        LOG.warning("IR: PyYAML not installed; cannot read %s", yaml_path)
        return issuers
    try:
        with open(yaml_path, "r") as f:
            y = yaml.safe_load(f) or {}
        for it in (y.get("issuers") or []):
            ir = it.get("ir") or {}
            issuers.append({
                "name": it.get("name"),
                "ticker": it.get("ticker"),
                "rss": ir.get("rss"),
                "page": ir.get("page"),
                "item_selector": ir.get("item_selector"),
                "date_selector": ir.get("date_selector"),
            })
    except Exception as e:
        LOG.exception("IR: failed to load YAML %s (%s)", yaml_path, e)
    return issuers


def _extract_items_from_html(html: str, base_url: str, item_selector: Optional[str], date_selector: Optional[str]) -> List[Tuple[str, str, Optional[datetime]]]:
    soup = BeautifulSoup(html, "lxml")
    out: List[Tuple[str, str, Optional[datetime]]] = []

    # Use provided selector or a reasonable default for IR lists
    selectors: List[str] = []
    if item_selector:
        selectors.append(item_selector)
    selectors.extend([
        "article a",
        "li a",
        ".news a",
        "a.news__link",
        "a.rss__link",
        ".press-release a",
    ])

    seen = set()
    for sel in selectors:
        for a in soup.select(sel):
            href = a.get("href") or ""
            title = a.get_text(strip=True)
            if not href or not title:
                continue
            url = _abs_url(base_url, href)
            # de-dup
            if url in seen:
                continue
            seen.add(url)

            # Try to find a date nearby
            dt: Optional[datetime] = None
            if date_selector:
                dtag = a if a.select_one(date_selector) else a.find_next(date_selector)
                if dtag and dtag.get_text(strip=True):
                    dt = _safe_parse_date(dtag.get_text(strip=True))
            if dt is None:
                # climb up: look for <time> or elements with date-ish classes
                parent = a
                for _ in range(3):
                    parent = parent.parent if parent else None
                    if not parent:
                        break
                    t = parent.find("time")
                    if t and (t.get("datetime") or t.get_text(strip=True)):
                        dt = _safe_parse_date(t.get("datetime") or t.get_text(strip=True))
                        if dt:
                            break
                    dspan = parent.find(lambda tag: tag.name in ("span", "div") and re.search(r"date|time|posted|publish", " ".join(tag.get("class", []) or []), re.I))
                    if dspan and dspan.get_text(strip=True):
                        dt = _safe_parse_date(dspan.get_text(strip=True))
                        if dt:
                            break
            if dt is None:
                # meta tag fallback
                meta = soup.find("meta", {"property": "article:published_time"})
                if meta and meta.get("content"):
                    dt = _safe_parse_date(meta["content"])

            out.append((url, title, dt))

    return out


# ---------------------------------------------------------------------
# Watcher
# ---------------------------------------------------------------------

class IrSourcesWatcher:
    """
    Company IR watcher that prefers RSS feeds and falls back to HTML pages.

    Configuration (wire later via env or YAML):
      - IR_SOURCES_YAML=data/issuers.yaml (preferred; see keys below)
        issuers:
          - name: DraftKings
            ticker: DKNG
            ir:
              rss: https://investors.draftkings.com/rss/news-releases.xml
          - name: Penn Entertainment
            ticker: PENN
            ir:
              page: https://investors.pennentertainment.com/news-releases
              item_selector: "article a"
              date_selector: "time"
      - START_FROM_DAYS (lookback; default 90)
      - IR_KEYWORDS (override regex; default matches results/earnings/Qx/Hx/FY)
      - IR_MAX_PAGES (unused by default; some sites paginate via query params)
      - IR_USER_AGENT (optional UA)
    """

    def __init__(self, start_days: Optional[int] = None):
        self.start_days = int(start_days if start_days is not None else os.getenv("START_FROM_DAYS", 90))
        self.client = _Http()
        kw_env = os.getenv("IR_KEYWORDS", "")
        self.keywords = re.compile(kw_env, re.I) if kw_env else DEFAULT_KEYWORDS
        self.yaml_path = os.getenv("IR_SOURCES_YAML", "data/issuers.yaml")

    # ---- RSS path ----
    def _poll_rss(self, feed_url: str) -> Iterable[Tuple[str, str, Optional[datetime]]]:
        try:
            fp = feedparser.parse(feed_url)
        except Exception as e:
            LOG.warning("IR RSS parse failed: %s (%s)", feed_url, e)
            return []
        out: List[Tuple[str, str, Optional[datetime]]] = []
        for e in fp.entries:
            title = (e.get("title") or "").strip()
            link = e.get("link")
            if not title or not link:
                continue
            # Date
            dt = None
            for key in ("published_parsed", "updated_parsed"):
                tm = e.get(key)
                if getattr(tm, "tm_year", None):
                    dt = datetime(tm.tm_year, tm.tm_mon, tm.tm_mday, tzinfo=timezone.utc)
                    break
            if not dt:
                dt = _safe_parse_date(e.get("published") or e.get("updated") or e.get("dc:date"))
            out.append((link, title, dt))
        return out

    # ---- HTML path ----
    def _poll_html(self, page_url: str, item_sel: Optional[str], date_sel: Optional[str]) -> Iterable[Tuple[str, str, Optional[datetime]]]:
        try:
            html = self.client.get(page_url).text
        except Exception as e:
            LOG.warning("IR page fetch failed: %s (%s)", page_url, e)
            return []
        return _extract_items_from_html(html, page_url, item_sel, date_sel)

    def poll(self) -> List[Tuple[str, str]]:
        issuers = _load_issuers_from_yaml(self.yaml_path)
        if not issuers:
            LOG.info("IrSourcesWatcher: no issuers configured in %s", self.yaml_path)
            return []

        cutoff = datetime.now(timezone.utc) - timedelta(days=self.start_days)
        results: List[Tuple[str, str]] = []
        seen_urls: set[str] = set()

        for it in issuers:
            rss = it.get("rss")
            page = it.get("page")
            item_sel = it.get("item_selector")
            date_sel = it.get("date_selector")

            items: List[Tuple[str, str, Optional[datetime]]] = []
            if rss:
                items.extend(self._poll_rss(rss))
            if page:
                items.extend(self._poll_html(page, item_sel, date_sel))

            for url, title, dt in items:
                if not self.keywords.search(title or ""):
                    continue
                if dt is not None and dt < cutoff:
                    continue
                if url in seen_urls:
                    continue
                seen_urls.add(url)
                results.append((url, title))

        return results
