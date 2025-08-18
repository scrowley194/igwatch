import json
import logging
import tempfile
from pathlib import Path
from typing import Iterable, List, Set, Tuple

import os
import time
from datetime import datetime, timezone

# --- Local Imports from your project ---
from .watchers.press_wires import GoogleNewsWatcher, PressWireWatcher
from .utils.log import get_logger
# from .utils.state import State  # <-- Not needed; inlined below
from .emailers import smtp_oauth
from .config import (
    DRY_RUN,
    MAIL_FROM,
    MAIL_TO,
    START_FROM_DAYS,
    USE_JINA_READER_FALLBACK,
    JINA_API_KEY,
)
from .parsers.extract import fetch_and_summarize
from .net_fetchers import http_get, looks_like_botwall, fetch_text_via_jina, BROWSER_UA


# --------------------------------------------------------------------
# Minimal inline State (no separate state.py required)
# --------------------------------------------------------------------

class State:
    """
    Simple URL de-dup state with atomic JSON persistence.
    File format: JSON array of strings (sorted on write).
    """
    def __init__(self, path: str | Path):
        self._path = Path(path)
        self._seen: Set[str] = set()
        self._load()

    def has(self, url: str) -> bool:
        return url in self._seen

    def add(self, url: str) -> None:
        self._seen.add(url)

    def save(self) -> None:
        """Atomically write to disk so partial writes don't corrupt the file."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        data = json.dumps(sorted(self._seen), ensure_ascii=False)
        with tempfile.NamedTemporaryFile("w", dir=str(self._path.parent), delete=False) as tmp:
            tmp.write(data + "\n")
            tmp_path = Path(tmp.name)
        tmp_path.replace(self._path)

    def _load(self) -> None:
        log = logging.getLogger("igwatch")
        try:
            if not self._path.exists():
                return
            raw_bytes = self._path.read_bytes()
            if not raw_bytes:
                return
            try:
                raw = raw_bytes.decode("utf-8").strip()
            except UnicodeDecodeError:
                # Legacy/binary file (e.g., old SQLite or corrupted cache) – back it up and start fresh
                bak = self._path.with_suffix(self._path.suffix + ".bak")
                try:
                    self._path.replace(bak)
                    log.error("State file %s is not UTF-8 text; moved to %s and starting fresh.", self._path, bak)
                except Exception:
                    log.exception("Failed to back up non-UTF8 state file %s; starting fresh without backup.", self._path)
                self._seen = set()
                return

            if not raw:
                return
            if raw[0] in "[{":
                # JSON array (preferred) or JSON object fallback
                data = json.loads(raw)
                if isinstance(data, list):
                    self._seen = set(map(str, data))
                elif isinstance(data, dict) and "seen" in data and isinstance(data["seen"], list):
                    self._seen = set(map(str, data["seen"]))
                else:
                    log.warning("Unexpected JSON structure in %s; starting fresh.", self._path)
                    self._seen = set()
            else:
                # Backward compat: newline-delimited
                self._seen = set(x for x in raw.splitlines() if x.strip())
        except Exception:
            logging.getLogger("igwatch").exception("Failed to load state from %s; starting fresh.", self._path)
            self._seen = set()

# --------------------------------------------------------------------
# Setup (use JSON by default, migrate old .db once)
# --------------------------------------------------------------------
DEFAULT_STATE_PATH = os.getenv("STATE_FILE", "data/seen.json")

# one-time migration: if old binary-looking data/seen.db exists but no JSON yet
_old = Path("data/seen.db")
_new = Path(DEFAULT_STATE_PATH)
if _old.exists() and not _new.exists():
    try:
        bak = _old.with_suffix(_old.suffix + ".legacy")
        _old.replace(bak)
        logging.getLogger("igwatch").warning("Found legacy state at %s; moved to %s.", _old, bak)
    except Exception:
        logging.getLogger("igwatch").exception("Could not move legacy state %s; proceeding.", _old)

logger = get_logger("igwatch")
state = State(DEFAULT_STATE_PATH)
DIV = "-" * 72


# --------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------
def render_email(item: dict) -> str:
    """Render a dict result into a plaintext email body."""
    lines = [
        f"Headline: {item.get('headline','')}",
        f"URL: {item.get('final_url','')}",
        "",
        f"Summary: {item.get('short_summary','')}",
        "",
        "Key Highlights:",
    ]
    for h in item.get("key_highlights", []):
        lines.append(f" - {h}")
    lines.append("")

    metrics = []
    for k in ["revenue", "ebitda", "net_income", "eps"]:
        v = item.get(k, {})
        if isinstance(v, dict):
            metrics.append(f"{k.upper()}: {v.get('current','')} (YoY: {v.get('yoy','')})")
    if metrics:
        lines.append("Metrics:")
        lines.extend([" - " + m for m in metrics])

    if item.get("final_thoughts"):
        lines.append("")
        lines.append("Notes: " + item["final_thoughts"])

    return "\n".join(lines)


def send_email(subject: str, body: str):
    """Send email using smtp_oauth, unless DRY_RUN is set."""
    if DRY_RUN:
        logger.info("[DRY RUN] Would send email: %s\n%s", subject, body)
        return
    to = MAIL_TO.split(",") if isinstance(MAIL_TO, str) else MAIL_TO
    smtp_oauth.send_plaintext(subject, body, to, mail_from=MAIL_FROM)


# --------------------------------------------------------------------
# Main Application Logic
# --------------------------------------------------------------------
def process_item(url: str, title: str):
    if state.has(url):
        logger.debug("Already processed: %s", url)
        return

    result = None
    try:
        result = fetch_and_summarize(url, title_hint=title)
    except Exception:
        logger.exception("fetch_and_summarize failed for %s", url)
        return

    if not result:
        logger.warning("No result from summarizer for %s", url)
        return

    state.add(url)

    subject = f"[Earnings Watch] {result.get('headline','Update')}"
    body = render_email(result)
    send_email(subject, body)
    logger.info("Sent email for %s", url)


def _iter_items_from_watcher(w) -> Iterable[Tuple[str, str]]:
    """
    Safely get (url, title) pairs from a watcher.
    - If watcher.poll() returns None or raises, return empty iterable.
    - If any item is malformed, skip it with a warning.
    """
    try:
        items = w.poll()
    except Exception:
        logger.exception("Watcher %s.poll() raised", w.__class__.__name__)
        return []

    if not items:
        logger.info("Watcher %s returned no items.", w.__class__.__name__)
        return []

    safe: List[Tuple[str, str]] = []
    for idx, it in enumerate(items):
        try:
            url, title = it
            if not url:
                raise ValueError("empty url")
            safe.append((url, title or ""))
        except Exception as e:
            logger.warning(
                "Watcher %s item[%d] malformed (%s); skipping. Item=%r",
                w.__class__.__name__,
                idx,
                e,
                it,
            )
    return safe


def main_loop():
    watchers = [
        GoogleNewsWatcher(start_days=START_FROM_DAYS),
        PressWireWatcher(start_days=START_FROM_DAYS),
    ]

    for w in watchers:
        logger.info(DIV)
        logger.info("Checking %s", w.__class__.__name__)
        for url, title in _iter_items_from_watcher(w):
            try:
                process_item(url, title)
            except Exception:
                logger.exception("process_item failed for %s", url)

    # Persist state without risking a crash
    try:
        state.save()
    except Exception:
        logger.exception("Failed to persist state")


if __name__ == "__main__":
    main_loop()
