# app/utils/state.py
# Simple URL de-dup state with atomic JSON persistence.
# You already prototyped an inline version in main; this makes it reusable.

from __future__ import annotations
import json
import logging
import tempfile
from pathlib import Path
from typing import Set

LOG = logging.getLogger("igwatch")


class State:
    """Atomic JSON-backed set for seen URLs (or arbitrary strings).

    File format: JSON array of strings (sorted on write). Backward-compatible with
    newline-delimited text: each non-empty line is treated as one entry.
    If the file isn't valid UTF-8 (legacy SQLite or binary), it will be moved aside
    with a .bak suffix and we start fresh.
    """

    def __init__(self, path: str | Path):
        self._path = Path(path)
        self._seen: Set[str] = set()
        self._load()

    def has(self, key: str) -> bool:
        return key in self._seen

    def add(self, key: str) -> None:
        self._seen.add(key)

    def save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        data = json.dumps(sorted(self._seen), ensure_ascii=False)
        with tempfile.NamedTemporaryFile("w", dir=str(self._path.parent), delete=False) as tmp:
            tmp.write(data + "\n")
            tmp_path = Path(tmp.name)
        tmp_path.replace(self._path)

    # ----------------------- internal -----------------------

    def _load(self) -> None:
        try:
            if not self._path.exists():
                return
            raw_bytes = self._path.read_bytes()
            if not raw_bytes:
                return
            try:
                raw = raw_bytes.decode("utf-8").strip()
            except UnicodeDecodeError:
                bak = self._path.with_suffix(self._path.suffix + ".bak")
                try:
                    self._path.replace(bak)
                    LOG.error("State file %s is not UTF-8 text; moved to %s and starting fresh.", self._path, bak)
                except Exception:
                    LOG.exception("Failed to back up non-UTF8 state file %s; starting fresh without backup.", self._path)
                self._seen = set()
                return

            if not raw:
                return
            if raw[0] in "[{}":
                # JSON array or {"seen": [...]} fallback
                data = json.loads(raw)
                if isinstance(data, list):
                    self._seen = set(map(str, data))
                elif isinstance(data, dict) and "seen" in data and isinstance(data["seen"], list):
                    self._seen = set(map(str, data["seen"]))
                else:
                    LOG.warning("Unexpected JSON structure in %s; starting fresh.", self._path)
                    self._seen = set()
            else:
                # Backward compat: newline-delimited
                self._seen = set(x for x in raw.splitlines() if x.strip())
        except Exception:
            LOG.exception("Failed to load state from %s; starting fresh.", self._path)
            self._seen = set()
