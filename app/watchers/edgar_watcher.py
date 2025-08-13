import time, re, json, requests
from typing import Iterable
from .base import Watcher, FoundItem

SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"

def _load_ticker_map() -> dict[str, str]:
    # Returns uppercase ticker -> 10-digit CIK string
    r = requests.get(SEC_TICKERS_URL, timeout=30, headers={"User-Agent": "NEXT.io Earnings Watcher"})
    data = r.json()
    # file is a list-like dict: {0:{'cik_str':..., 'ticker':..., 'title':...}, ...}
    out = {}
    for _k, row in data.items():
        t = row.get("ticker")
        if not t:
            continue
        cik = int(row.get("cik_str"))
        out[t.upper()] = f"{cik:010d}"
    return out

def _edgar_atom_url(cik10: str, form: str) -> str:
    # SEC Atom feed for company filings of a type
    # e.g. https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=0001590895&type=8-K&count=40&owner=exclude&output=atom
    return f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={cik10}&type={form}&count=25&owner=exclude&output=atom"

class EdgarWatcher(Watcher):
    name = "edgar_atom"

    def __init__(self, ticker: str):
        self.ticker = ticker.upper()

    def poll(self) -> Iterable[FoundItem]:
        tmap = _load_ticker_map()
        cik10 = tmap.get(self.ticker)
        if not cik10:
            return []
        items = []
        for form in ("10-Q", "8-K", "6-K"):
            url = _edgar_atom_url(cik10, form)
            try:
                r = requests.get(url, timeout=30, headers={"User-Agent": "NEXT.io Earnings Watcher"})
                if r.status_code != 200:
                    continue
                import feedparser, time as _t, email.utils
                feed = feedparser.parse(r.text)
                for e in feed.entries[:10]:
                    title = e.get("title", "")
                    link = e.get("link", "")
                    # prefer 8-K items that look like earnings
                    if form == "8-K":
                        lt = title.lower()
                        if not any(k in lt for k in ["earnings", "results", "quarter", "q1", "q2", "q3", "q4"]):
                            continue
                    ts = None
                    if hasattr(e, "published"):
                        try:
                            ts = int(_t.mktime(email.utils.parsedate(e.published)))
                        except Exception:
                            ts = None
                    items.append(FoundItem(source=url, title=title, url=link, published_ts=ts))
            except Exception:
                continue
        return items
