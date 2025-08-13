import requests
from time import sleep

class PoliteSession(requests.Session):
    def __init__(self, user_agent: str | None = None):
        super().__init__()
        if user_agent:
            self.headers.update({"User-Agent": user_agent})

    def get_polite(self, url, **kw):
        # Respect ETag/Last-Modified forwarding
        return super().get(url, timeout=kw.pop("timeout", 20), **kw)

def backoff(status_code: int, attempt: int) -> float:
    # Simple exponential backoff
    base = 1.5
    return min(300.0, base ** attempt)
