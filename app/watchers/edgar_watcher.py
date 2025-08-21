# edgar_watcher.py
from datetime import datetime, timedelta, timezone
import json, re
from app.sec_client import SecClient

FORMS = {"10-Q","10-K","8-K","6-K","20-F","40-F"}
ITEM_8K_EARNINGS = re.compile(r"\bItem\s*2\.02\b", re.I)

def _pad(cik: str) -> str: return f"{int(cik):010d}"
def _acc_folder(acc: str) -> str: return acc.replace("-", "")
def _primary_url(cik, acc, primary): 
    return f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{_acc_folder(acc)}/{primary}"
def _index_url(cik, acc):
    return f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{_acc_folder(acc)}/{acc}-index.html"

def run(config, seen_db, emit):
    ua = config.get("SEC_USER_AGENT") or "igwatch (contact: email@example.com)"
    client = SecClient(ua)
    issuers = config.get("issuers", [])  # [{ticker: "DKNG"} or {cik: "0001464343"}]
    cutoff = datetime.now(timezone.utc) - timedelta(days=int(config.get("START_FROM_DAYS", 90)))

    # fresh ticker->CIK map each run
    tick_map = client.get("https://www.sec.gov/files/company_tickers.json").json()
    by_ticker = {v["ticker"].lower(): f'{int(v["cik_str"]):010d}' for v in tick_map.values()}

    for iss in issuers:
        cik = iss.get("cik") or by_ticker.get(iss.get("ticker","").lower())
        if not cik:
            continue

        subs = client.get(f"https://data.sec.gov/submissions/CIK{_pad(cik)}.json").json()
        rec = subs.get("filings",{}).get("recent",{})
        for form, fdate, acc, primary in zip(rec.get("form",[]), rec.get("filingDate",[]),
                                            rec.get("accessionNumber",[]), rec.get("primaryDocument",[])):
            if form not in FORMS:
                continue
            # date gate
            d = datetime.strptime(fdate, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            if d < cutoff:
                continue

            key = f"sec:{cik}:{form}:{acc}"
            if seen_db.seen(key):
                continue

            # try primary doc first
            url = _primary_url(cik, acc, primary)
            try:
                r = client.get(url)
                body = r.text
                # light 8-K filter: only emit if earnings item present (optional)
                if form == "8-K" and not ITEM_8K_EARNINGS.search(body):
                    # try to locate EX-99.1 press release link from index page
                    idx = client.get(_index_url(cik, acc)).text
                    m = re.search(r'href="([^"]+?)"[^>]*>\s*EX-99\.1', idx, re.I)
                    if m:
                        url = _primary_url(cik, acc, m.group(1))
                emit({"title": f"{form} {iss.get('ticker', iss.get('name',''))} ({fdate})",
                      "url": url, "date": fdate, "source": "SEC"})
                seen_db.mark(key)
            except Exception as e:
                # fallback to index page if primary blocked or 404
                idx_url = _index_url(cik, acc)
                try:
                    client.get(idx_url)  # ensure reachable
                    emit({"title": f"{form} {iss.get('ticker', iss.get('name',''))} ({fdate})",
                          "url": idx_url, "date": fdate, "source": "SEC"})
                    seen_db.mark(key)
                except Exception:
                    # log and keep going
                    print(f"[EDGAR] failed for {cik} {form} {acc}: {e}")
                    continue
