import sqlite3, hashlib, os, threading

class State:
    def __init__(self, db_path: str = "seen.db"):
        self.db_path = db_path
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        self._lock = threading.Lock()
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as con:
            con.execute("CREATE TABLE IF NOT EXISTS seen (id TEXT PRIMARY KEY, ts INTEGER)")

    def make_id(self, source: str, url: str, title: str) -> str:
        raw = f"{source}|{url}|{title}".encode("utf-8", "ignore")
        return hashlib.sha256(raw).hexdigest()

    def is_seen(self, item_id: str) -> bool:
        with self._lock, sqlite3.connect(self.db_path) as con:
            cur = con.execute("SELECT 1 FROM seen WHERE id=?", (item_id,))
            return cur.fetchone() is not None

    def mark_seen(self, item_id: str, ts: int):
        with self._lock, sqlite3.connect(self.db_path) as con:
            con.execute("INSERT OR IGNORE INTO seen (id, ts) VALUES (?, ?)", (item_id, ts))
