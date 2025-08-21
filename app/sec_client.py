# app/sec_client.py
import time, requests

class SecClient:
    def __init__(self, ua: str, max_retries=5):
        self.s = requests.Session()
        self.s.headers.update({
            "User-Agent": ua,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Encoding": "gzip, deflate",
            "Connection": "keep-alive",
        })
        self.max_retries = max_retries

    def get(self, url, **kw):
        backoff = 0.5
        for i in range(self.max_retries):
            r = self.s.get(url, timeout=30, **kw)
            if r.status_code in (429, 403):
                retry_after = r.headers.get("Retry-After")
                sleep = float(retry_after) if retry_after else backoff
                time.sleep(sleep)
                backoff = min(backoff * 2, 8.0)
                continue
            r.raise_for_status()
            time.sleep(0.2)  # SEC politeness
            return r
        r.raise_for_status()
