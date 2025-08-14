from dataclasses import dataclass
from typing import Iterable, Optional

@dataclass
class FoundItem:
    source: str
    title: str
    url: str
    published_ts: int | None

class Watcher:
    name: str = "base"
    def poll(self) -> Iterable[FoundItem]:
        raise NotImplementedError
