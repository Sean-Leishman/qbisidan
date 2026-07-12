"""Resumable state for /crawl runs — survives crashes and process restarts.

One state file holds every crawl key (channel+date-range+topic), since a user
may kick off more than one crawl before either finishes. Saved after every
video, so writes go through a temp-file + os.replace so a crash mid-write
can't leave the file half-written.
"""
import json
import logging
import os
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class CrawlRunState:
    """Progress for one crawl key. All fields default to an empty list."""

    processed_ids: list = field(default_factory=list)
    filtered_out_ids: list = field(default_factory=list)
    created_titles: list = field(default_factory=list)
    skipped_titles: list = field(default_factory=list)
    errors: list = field(default_factory=list)


class CrawlStateStore:
    """JSON-backed store of CrawlRunState, keyed by CrawlStateStore.make_key(...)."""

    def __init__(self, state_file: str):
        self.path = Path(state_file)

    @staticmethod
    def make_key(channel: str, date_from_str: str, date_to_str: str, topic: str | None) -> str:
        return f"{channel}|{date_from_str}|{date_to_str}|{topic or ''}"

    def _read_all(self) -> dict:
        if not self.path.exists():
            return {}
        try:
            return json.loads(self.path.read_text())
        except (json.JSONDecodeError, OSError) as e:
            # ponytail: a corrupt state file just means "start fresh", not a crash.
            logger.error(f"Crawl state file unreadable, starting fresh: {e}")
            return {}

    def _write_all(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, indent=2))
        os.replace(tmp, self.path)  # atomic on POSIX — no half-written file on crash

    def load(self, key: str) -> CrawlRunState:
        data = self._read_all().get(key)
        if not data:
            return CrawlRunState()
        known = {f.name for f in fields(CrawlRunState)}
        return CrawlRunState(**{k: v for k, v in data.items() if k in known})

    def save(self, key: str, state: CrawlRunState) -> None:
        data = self._read_all()
        data[key] = asdict(state)
        self._write_all(data)

    def clear(self, key: str) -> None:
        data = self._read_all()
        if key in data:
            del data[key]
            self._write_all(data)
