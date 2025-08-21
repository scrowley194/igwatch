# app/watchers/sec_edgar.py
from datetime import datetime, timedelta, timezone
import time, re
import requests

FORMS = {"10-Q","10-K","8-K","6-K","20-F","40-F"}
ITEM_8K_EARNINGS = re.compile(r"\bItem\s*2\.02\b", re.I)

def _pad(cik: str) -> str: return f"{int(cik):010d}"
def _acc_folder(acc: str) -> str: return acc.replace("-", "")
def _primary_url(cik, acc, primary):
    return f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{_acc_folder(acc)}/{primary}"
def _index_url(cik, acc):
    return f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{_acc_folder(acc)}/{acc}-index.html"

class SecClient:
    def __init__(self, ua: str, max_retries=5):
        self.s = requests.Session()
        self.s.headers.update({
            "User-Agent": ua or "igwatch (contact: email@example.com)",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Encoding": "gzip, deflate",
            "Connection": "keep-alive",
        })
        self.max_retries = max_retries

    def get(self, url, **kw):
        backoff = 0.5
        for _ in range(self.max_retries):
            r = self.s.get(url, timeout=30, **kw)
            if r.status_code in (403, 429):
                sleep = float(r.headers.get("Retry-After") or backoff)
                time.sleep(sleep)
                backoff = min(backoff*2, 8.0)
                continue
            r.raise_for_status()
            time.sleep(0.2)  # SEC politeness
            return r
        r.raise_for_status()

def _load_ticker_map(client: SecClient):
    data = client.get("https://www.sec.gov/files/company_tickers.json").json()
    return {v["ticker"].lower(): f"{int(v['cik_str']):010d}" for v in data.values()}

def _issuers_from_env(env):
    # Supports either a YAML file (data/issuers.yaml) or SEC_TICKERS env list.
    import os, yaml
    issuers = []
    yaml_path = os.getenv("SEC_ISSUERS_YAML", "data/issuers.yaml")
    if os.path.exists(yaml_path):
        with open(yaml_path, "r") as f:
            y = yaml.safe_load(f) or {}
            for it in (y.get("issuers") or []):
                issuers.append({"name": it.get("name"), "ticker": it.get("ticker"), "cik": it.get("cik")})
    env_list = os.getenv("SEC_TICKERS", "")
    if env_list:
        for t in [x.strip() for x in env_list.split(",") if x.strip()]:
            issuers.append({"ticker": t})
    # de-dup by (cik or ticker)
    seen = set(); uniq=[]
    for it in issuers:
        k = it.get("cik") or (it.get("ticker") or "").lower()
        if not k or k in seen: continue
        seen.add(k); uniq.append(it)
    return uniq

def run(config, seen_db, emit, logger=None):
    ua = config.get("SEC_USER_AGENT")
    client = SecClient(ua)
    issuers = _issuers_from_env(config)  # pass os.environ into config when calling
    if logger:
        logger.info("SEC EDGAR: issuers configured = %d", len(issuers))
    if not issuers:
        return  # nothing to do

    cutoff = datetime.now(timezone.utc) - timedelta(days=int(config.get("START_FROM_DAYS", 90)))
    tick_map = _load_ticker_map(client)

    for iss in issuers:
        cik = iss.get("cik")
        tk = (iss.get("ticker") or "").lower()
        if not cik and tk:
            cik = tick_map.get(tk)
        if not cik:
            continue

        subs = client.get(f"https://data.sec.gov/submissions/CIK{_pad(cik)}.json").json()
        recent = subs.get("filings", {}).get("recent", {})
        for form, fdate, acc, primary in zip(
            recent.get("form", []),
            recent.get("filingDate", []),
            recent.get("accessionNumber", []),
            recent.get("primaryDocument", []),
        ):
            if form not in FORMS:
                continue
            d = datetime.strptime(fdate, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            if d < cutoff:
                continue

            key = f"sec:{cik}:{form}:{acc}"
            if seen_db.seen(key):
                continue

            url = _primary_url(cik, acc, primary)
            try:
                body = client.get(url).text
                if form == "8-K" and not ITEM_8K_EARNINGS.search(body):
                    # try EX-99.1 from index
                    idx = client.get(_index_url(cik, acc)).text
                    m = re.search(r'href="([^"]+?)"[^>]*>\s*EX-99\.1', idx, re.I)
                    if m:
                        url = _primary_url(cik, acc, m.group(1))
                emit({"title": f"{form} {iss.get('ticker', iss.get('name',''))} ({fdate})",
                      "url": url, "date": fdate, "source": "SEC"})
                seen_db.mark(key)
            except Exception as e:
                if logger: logger.warning("SEC EDGAR: primary failed %s %s %s (%s); using index",
                                          cik, form, acc, e)
                idx_url = _index_url(cik, acc)
                try:
                    client.get(idx_url)
                    emit({"title": f"{form} {iss.get('ticker', iss.get('name',''))} ({fdate})",
                          "url": idx_url, "date": fdate, "source": "SEC"})
                    seen_db.mark(key)
                except Exception as e2:
                    if logger: logger.error("SEC EDGAR: index failed %s %s %s (%s)", cik, form, acc, e2)
                    continue
