import re
from bs4 import BeautifulSoup

def html_to_text(html: str) -> str:
    soup = BeautifulSoup(html, "lxml")
    # remove scripts and styles
    for el in soup(["script", "style", "noscript"]):
        el.decompose()
    text = soup.get_text("\n", strip=True)
    # collapse extra newlines
    text = re.sub(r"\n{2,}", "\n", text)
    return text

def first_sentences(text: str, n_sentences: int = 2) -> str:
    # very light sentence split
    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    return " ".join(parts[:n_sentences])
