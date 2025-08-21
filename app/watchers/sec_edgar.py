# app/watchers/sec_edgar.py
# Robust SEC EDGAR watcher that yields (url, title) for primary-source filings.
# No edits to main are required. Wire this watcher later when we reach the orchestrator step.

from __future__ import annotations
import os
import re
import time
import logging
from typing import List, Tuple, Dict, Any
from datetime import datetime, timedelta, timezone

import requests
try:
    import yaml  # optional for YAML issuer lists
except Exception:  # pragma: no cover
    yaml = None

LOG = logging.getLogger("igwatch")

# Default set of forms relevant to earnings/financial updates
DEFAULT_FORMS: set[str] = {"10-Q", "10-K", "8-K", "6-K", "20-F", "40-F"}
# If present in 8-K body, this indicates an earnings release/update
ITEM_8K_EARNINGS = re.compile(r"\bItem\s*2\.02\b", re.I)


# ---------------------------- URL helpers ----------------------------

def _pad_cik(cik: str | int) -> str:
    return f"{int(cik):010d}"


def _acc_folder(acc: str) -> str:
    # 0001234567-25-000123 -> 000123456725000123
    return acc.replace("-", "")


def _primary_url(cik: str, acc: str, primary: str) -> str:
    return f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{_acc_folder(acc)}/{primary}"


def _index_url(cik: str, acc: str) -> str:
    return f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{_acc_folder(acc)}/{acc}-index.html"


# ---------------------------- HTTP client ----------------------------

class _SecClient:
    """Small HTTP client with polite pacing and retry for SEC endpoints."""

    def __init__(self, ua: str | None, max_retries: int = 5, polite_delay: float = 0.2):
        self.s = requests.Session()
        self.s.headers.update({
            "User-Agent": ua or "igwatch (contact: support@example.com)",
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
                LOG.debug("SEC backoff %ss for %s (%s)", sleep, url, r.status_code)
                time.sleep(sleep)
                backoff = min(backoff * 2, 8.0)
                last = r
                continue
            r.raise_for_status()
            time.sleep(self.polite_delay)  # be nice to sec.gov
            return r
        if last is not None:
            last.raise_for_status()
        raise RuntimeError("SEC request failed and no response to raise")


# ---------------------------- Issuer config ----------------------------

def _load_issuers_from_yaml(yaml_path: str | None) -> List[Dict[str, Any]]:
    if not yaml_path or not os.path.exists(yaml_path) or yaml is None:
        return []
    with open(yaml_path, "r") as f:
        y = yaml.safe_load(f) or {}
    issuers: List[Dict[str, Any]] = []
    for it in (y.get("issuers") or []):
        issuers.append({
            "name": it.get("name"),
            "ticker": it.get("ticker"),
            "cik": it.get("cik"),
        })
    return issuers


def _issuers_from_env() -> List[Dict[str, Any]]:
    """Combine YAML issuers + SEC_TICKERS env list; de-dup by CIK/ticker."""
    out: List[Dict[str, Any]] = []

    # Optional YAML list (path via SEC_ISSUERS_YAML)
    out.extend(_load_issuers_from_yaml(os.getenv("SEC_ISSUERS_YAML")))

    # Quick env list of tickers (comma-separated)
    env_list = os.getenv("SEC_TICKERS", "")
    if env_list:
        for t in [x.strip() for x in env_list.split(",") if x.strip()]:
            out.append({"ticker": t})

    # De-duplicate
    seen = set()
    uniq: List[Dict[str, Any]] = []
    for it in out:
        key = it.get("cik") or (it.get("ticker") or "").lower()
        if not key or key in seen:
            continue
        seen.add(key)
        uniq.append(it)
    return uniq


def _forms_set() -> set[str]:
    env = os.getenv("SEC_FORMS", "")
    if not env:
        return set(DEFAULT_FORMS)
    return {x.strip().upper() for x in env.split(",") if x.strip()}


# ---------------------------- Watcher ----------------------------

class SecEdgarWatcher:
    """
    Polls SEC EDGAR submissions JSON for configured issuers and yields (url, title).

    Environment variables used (wire later in CI or main):
      - SEC_TICKERS=DKNG,PENN,CZR,... (simple setup)  OR SEC_ISSUERS_YAML=data/issuers.yaml
      - SEC_FORMS=10-Q,10-K,8-K (optional override; default includes 6-K/20-F/40-F)
      - SEC_USER_AGENT=NEXT.io Earnings Watcher (contact: you@domain)
      - START_FROM_DAYS (lookback window; if absent, defaults to 90)
    """

    def __init__(self, start_days: int | None = None):
        lookback = start_days if start_days is not None else int(os.getenv("START_FROM_DAYS", "90"))
        self.start_days = int(lookback)
        self.ua = os.getenv("SEC_USER_AGENT") or "igwatch (contact: support@example.com)"
        self.client = _SecClient(self.ua)
        self.forms = _forms_set()

    def _ticker_map(self) -> Dict[str, str]:
        data = self.client.get("https://www.sec.gov/files/company_tickers.json").json()
        # map ticker (lower) -> zero-padded CIK
        return {v["ticker"].lower(): f"{int(v['cik_str']):010d}" for v in data.values()}

    def poll(self) -> List[Tuple[str, str]]:
        issuers = _issuers_from_env()
        if not issuers:
            LOG.info("SecEdgarWatcher: no issuers configured (SEC_TICKERS or SEC_ISSUERS_YAML).")
            return []

        cutoff = datetime.now(timezone.utc) - timedelta(days=self.start_days)
        tmap = self._ticker_map()
        out: List[Tuple[str, str]] = []

        for iss in issuers:
            cik = iss.get("cik")
            ticker = (iss.get("ticker") or "").lower()
            if not cik and ticker:
                cik = tmap.get(ticker)
            if not cik:
                continue

            subs = self.client.get(f"https://data.sec.gov/submissions/CIK{_pad_cik(cik)}.json").json()
            recent = subs.get("filings", {}).get("recent", {})
            rows = zip(
                recent.get("form", []),
                recent.get("filingDate", []),
                recent.get("accessionNumber", []),
                recent.get("primaryDocument", []),
            )

            for form, fdate, acc, primary in rows:
                if form not in self.forms:
                    continue
                try:
                    d = datetime.strptime(fdate, "%Y-%m-%d").replace(tzinfo=timezone.utc)
                except Exception:
                    continue
                if d < cutoff:
                    continue

                # Prefer primary document; otherwise fall back to index
                url = _primary_url(cik, acc, primary)
                try:
                    body = self.client.get(url).text
                    # For 8-K, try to ensure it's an earnings-related exhibit
                    if form == "8-K" and not ITEM_8K_EARNINGS.search(body or ""):
                        idx = self.client.get(_index_url(cik, acc)).text
                        # Prefer Exhibit 99.1 if present (common for earnings PRs)
                        m = re.search(r'href=\"([^\"]+?)\"[^>]*>\s*(?:EX|Exhibit)[-\s]?99\.1', idx, re.I)
                        if m:
                            url = _primary_url(cik, acc, m.group(1))
                except Exception:
                    # Primary may be blocked or missing; use index as a fallback
                    try:
                        self.client.get(_index_url(cik, acc))
                        url = _index_url(cik, acc)
                    except Exception as e2:  # pragma: no cover
                        LOG.warning("SEC: failed both primary/index for %s %s %s (%s)", cik, form, acc, e2)
                        continue

                # Compose a readable title
                title_bits = [form]
                if iss.get("ticker"):
                    title_bits.append(iss["ticker"])
                elif iss.get("name"):
                    title_bits.append(iss["name"])
                title_bits.append(f"({fdate})")
                title = " ".join(title_bits)

                out.append((url, title))

        return out
