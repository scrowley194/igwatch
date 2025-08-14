# edgar_watcher.py
import time
import requests
import feedparser
import email.utils
from typing import Iterable
from .base import Watcher, FoundItem

SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
USER_AGENT = "NEXT.io Earnings Watcher (contact: stuatnext@gmail.com)"


def _load_ticker_map() -> dict[str, str]:
    """Load a mapping of TICKER → 10-digit CIK from SEC."""
    r = requests.get(SEC_TICKERS_URL, timeout=30, headers={"User-Agent": USER_AGENT})
    r.raise_for_status()
    data = r.json()

    return {
        row["ticker"].upper(): f"{int(row['cik_str']):010d}"
        for row in data.values()
        if row.get("ticker") and row.get("cik_str")
    }


def _edgar_atom_url(cik10: str, form: str) -> str:
    """Build an EDGAR Atom feed URL for a given CIK and form type."""
    return (
        "https://www.sec.gov/cgi-bin/browse-edgar"
        f"?action=getcompany&CIK={cik10}&type={form}"
        "&count=25&owner=exclude&output=atom"
    )


class EdgarWatcher(Watcher):
    """Watcher for SEC EDGAR filings."""

    name = "edgar_atom"

    def __init__(self, ticker: str):
        self.ticker = ticker.upper()

    def poll(self) -> Iterable[FoundItem]:
        """Poll EDGAR Atom feeds for relevant forms."""
        tmap = _load_ticker_map()
        cik10 = tmap.get(self.ticker)
        if not cik10:
            return []

        items = []
        for form in ("10-Q", "8-K", "6-K"):
            url = _edgar_atom_url(cik10, form)
            try:
                r = requests.get(url, timeout=30, headers={"User-Agent": USER_AGENT})
                if r.status_code != 200:
                    continue
            except requests.RequestException:
                continue

            feed = feedparser.parse(r.text)
            for e in feed.entries[:15]:
                title = e.get("title", "")
                link = e.get("link", "")
                ts = None
                if hasattr(e, "published"):
                    try:
                        ts = int(time.mktime(email.utils.parsedate(e.published)))
                    except Exception:
                        pass

                # Only keep 8-K entries if they look like earnings/results
                if form == "8-K" and not any(
                    k in title.lower()
                    for k in ["earnings", "results", "quarter", "q1", "q2", "q3", "q4"]
                ):
                    continue

                items.append(FoundItem(url, title, link, ts))

        return items
