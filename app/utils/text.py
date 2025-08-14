import re, json, time, datetime
from bs4 import BeautifulSoup

MONTHS = "(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"

def html_to_text(html: str) -> str:
    soup = BeautifulSoup(html, "lxml")
    for el in soup(["script","style","noscript","header","footer","nav","aside"]):
        el.decompose()
    candidates = []
    for tag in soup.find_all(["article", "section", "div", "main"]):
        text = tag.get_text("\n", strip=True)
        if len(text) > 400:
            candidates.append((len(text), text))
    if candidates:
        text = max(candidates, key=lambda x: x[0])[1]
    else:
        text = soup.get_text("\n", strip=True)
    text = re.sub(r"\n{2,}", "\n", text)
    return text

def first_sentences(text: str, n_sentences: int = 2) -> str:
    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    return " ".join(parts[:n_sentences])

def extract_published_ts(html: str) -> int | None:
    soup = BeautifulSoup(html, "lxml")
    t = soup.find("time", attrs={"datetime": True})
    if t and t.get("datetime"):
        try:
            dt = datetime.datetime.fromisoformat(t["datetime"].replace("Z","+00:00"))
            return int(dt.timestamp())
        except Exception:
            pass
    meta = soup.find("meta", attrs={"property": "article:published_time"}) or soup.find("meta", attrs={"name":"date"})
    if meta and meta.get("content"):
        try:
            dt = datetime.datetime.fromisoformat(meta["content"].replace("Z","+00:00"))
            return int(dt.timestamp())
        except Exception:
            pass
    for sc in soup.find_all("script", attrs={"type": "application/ld+json"}):
        try:
            data = json.loads(sc.get_text(strip=True))
            if isinstance(data, dict):
                dp = data.get("datePublished") or data.get("dateModified")
                if dp:
                    dt = datetime.datetime.fromisoformat(str(dp).replace("Z","+00:00"))
                    return int(dt.timestamp())
            elif isinstance(data, list):
                for it in data:
                    dp = it.get("datePublished") or it.get("dateModified")
                    if dp:
                        dt = datetime.datetime.fromisoformat(str(dp).replace("Z","+00:00"))
                        return int(dt.timestamp())
        except Exception:
            continue
    m = re.search(rf"(\d{{1,2}}\s+{MONTHS}\s+\d{{4}})", soup.get_text(" ", strip=True), flags=re.I)
    if m:
        try:
            dt = datetime.datetime.strptime(m.group(1), "%d %B %Y")
            return int(dt.replace(tzinfo=datetime.timezone.utc).timestamp())
        except Exception:
            pass
    return None

def detect_period_key(title: str, text: str) -> str | None:
    s = f"{title}\n{text}".lower()
    q_map = {"first": "Q1","second":"Q2","third":"Q3","fourth":"Q4"}
    m = re.search(r"\bq([1-4])\s*(20\d{2})\b", s, flags=re.I)
    if m:
        return f"{m.group(2)}-Q{m.group(1)}"
    m = re.search(r"\b(20\d{2})\s*q([1-4])\b", s, flags=re.I)
    if m:
        return f"{m.group(1)}-Q{m.group(2)}"
    m = re.search(r"\b(first|second|third|fourth)\s+quarter\s+(20\d{2})\b", s)
    if m:
        return f"{m.group(2)}-{q_map[m.group(1)]}"
    m = re.search(r"\b(h1|first half|interim report(?:\s+january\s*[–-]\s*june)?)\b.*?(20\d{2})", s)
    if m:
        return f"{m.group(2)}-H1"
    m = re.search(r"\b(h2|second half)\b.*?(20\d{2})", s)
    if m:
        return f"{m.group(2)}-H2"
    m = re.search(r"\b(fy|full year)\b.*?(20\d{2})", s)
    if m:
        return f"{m.group(2)}-FY"
    return None
