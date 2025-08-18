import re
import logging
import requests
from urllib.parse import urlparse

# Optional: curl_cffi for browser-like TLS
try:
    from curl_cffi import requests as curl
    _HAS_CURL = True
except Exception:
    curl = requests  # fallback
    _HAS_CURL = False

DEFAULT_TIMEOUT = (12, 30)

BOTWALL_SIGNS = re.compile(
    r"(captcha|verify you are human|access denied|forbidden|"
    r"cloudflare|akamai|perimeterx|datadome|incapsula|attention required)",
    re.I,
)

# Provide a default browser-like UA if config doesn’t have one
BROWSER_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"

logger = logging.getLogger(__name__)

# -------------------------------
# Session Factory
# -------------------------------
def make_session():
    """
    Create and return a curl_cffi (or requests) session with a default User-Agent.
    """
    if _HAS_CURL:
        s = curl.Session()
    else:
        s = requests.Session()
    s.headers.update({"User-Agent": BROWSER_UA})
    return s

# -------------------------------
# Fetchers
# -------------------------------
def http_get(url: str, headers: dict = None, timeout=DEFAULT_TIMEOUT):
    if headers is None:
        headers = {"User-Agent": BROWSER_UA}

    if _HAS_CURL:
        r = curl.get(url, impersonate="chrome", headers=headers, timeout=timeout, allow_redirects=True)
    else:
        r = requests.get(url, headers=headers, timeout=timeout, allow_redirects=True)

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


def fetch_text_via_jina(url: str, api_key: str = None, timeout: int = 30) -> str:
    reader_url = "https://r.jina.ai/" + url
    headers = {"User-Agent": BROWSER_UA}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    r = requests.get(reader_url, headers=headers, timeout=timeout)
    r.raise_for_status()
    return r.text
