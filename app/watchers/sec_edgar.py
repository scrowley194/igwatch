from __future__ import annotations
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


# Prefer primary doc; else index and try EX-99.1 for earnings PRs
url = _primary_url(cik, acc, primary)
try:
body = self.client.get(url).text
if form == "8-K" and not ITEM_8K_EARNINGS.search(body):
idx = self.client.get(_index_url(cik, acc)).text
m = re.search(r'href="([^"]+?)"[^>]*>\s*EX-99\.1', idx, re.I)
if m:
url = _primary_url(cik, acc, m.group(1))
except Exception:
# primary not reachable → fall back to index
try:
self.client.get(_index_url(cik, acc))
url = _index_url(cik, acc)
except Exception as e2:
LOG.warning("SEC: failed both primary/index for %s %s %s (%s)", cik, form, acc, e2)
continue


title_bits = [form]
if iss.get("ticker"):
title_bits.append(iss["ticker"])
elif iss.get("name"):
title_bits.append(iss["name"])
title_bits.append(f"({fdate})")
title = " ".join(title_bits)


out.append((url, title))


return out
