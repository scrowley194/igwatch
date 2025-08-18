# app/net/fetchers.py
import re
from typing import Tuple, Optional

# 1) Try curl_cffi (browser-grade TLS); 2) fall back to requests
try:
    from curl_cffi import requests as curl
    _HAS_CURL = True
except Exception:
    import requests as curl  # type: ignore
    _HAS_CURL = False

import requests  # plain requests for Jina fallback

DEFAULT_TIMEOUT = (12, 30)  # (connect, read) seconds

BOTWALL_SIGNS = re.compile(
    r"(captcha|verify you are human|access denied|forbidden|"
    r"cloudflare|akamai|perimeterx|datadome|incapsula|attention required)",
    re.I,
)

def http_get(url: str, headers: Optional[dict] = None, timeout: Tuple[int, int] = DEFAULT_TIMEOUT):
    """
    Return (final_url, text, content_type). Uses curl_cffi with Chrome impersonation when available.
    """
    if _HAS_CURL:
        r = curl.get(url, impersonate="chrome", headers=headers or {}, timeout=timeout, allow_redirects=True)
    else:
        r = curl.get(url, headers=headers or {}, timeout=timeout, allow_redirects=True)  # requests
    r.raise_for_status()
    final = getattr(r, "url", url)
    ctype = r.headers.get("content-type", "")
    return final, r.text, ctype

def looks_like_botwall(html: str) -> bool:
    if not html:
        return True
    if len(html) < 800 and BOTWALL_SIGNS.search(html):
        return True
    return bool(BOTWALL_SIGNS.search(html))

def fetch_text_via_jina(url: str, api_key: Optional[str] = None, timeout: int = 30) -> str:
    """
    Jina Reader converts URL->clean text/markdown. Great fallback for blocked pages & PDFs.
    https://r.jina.ai  (20 RPM no key, ~500 RPM with key, higher with premium)
    """
    reader_url = "https://r.jina.ai/" + url
    headers = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    r = requests.get(reader_url, headers=headers, timeout=timeout)
    r.raise_for_status()
    return r.text
