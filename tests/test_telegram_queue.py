"""Tests for TelegramQueue activity tracking."""
import json
import tempfile
from pathlib import Path

import pytest

from src.telegram_queue import TelegramQueue


@pytest.fixture
def queue(tmp_path):
    state_file = tmp_path / "data" / "state.json"
    return TelegramQueue(
        bot_token="dummy",
        allowed_chat_ids=[123],
        state_file=str(state_file),
    )


class TestActivityLogging:
    def test_log_activity_creates_entry(self, queue):
        queue.log_activity("note")
        stats = queue.get_today_stats()
        assert stats["note"] == 1

    def test_log_activity_increments(self, queue):
        queue.log_activity("todo")
        queue.log_activity("todo")
        queue.log_activity("todo")
        assert queue.get_today_stats()["todo"] == 3

    def test_log_multiple_types(self, queue):
        queue.log_activity("note")
        queue.log_activity("todo")
        queue.log_activity("event")
        stats = queue.get_today_stats()
        assert stats["note"] == 1
        assert stats["todo"] == 1
        assert stats["event"] == 1

    def test_get_today_stats_empty(self, queue):
        assert queue.get_today_stats() == {}

    def test_activity_persisted_to_disk(self, queue):
        queue.log_activity("note")
        # Re-create queue from same file — data should survive
        queue2 = TelegramQueue(
            bot_token="dummy",
            allowed_chat_ids=[123],
            state_file=queue.state_file,
        )
        assert queue2.get_today_stats()["note"] == 1

    def test_last_update_id_preserved_after_log(self, queue):
        """log_activity must not clobber last_update_id."""
        queue.last_update_id = 999
        queue._save_last_update_id()
        queue.log_activity("note")
        # Reload
        state = json.loads(Path(queue.state_file).read_text())
        assert state["last_update_id"] == 999

    def test_activity_isolated_by_date(self, queue):
        """Entries for yesterday must not bleed into today."""
        from datetime import date, timedelta
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        # Manually write a yesterday entry
        state = {"last_update_id": 0, "activity": {yesterday: {"note": 5}}}
        Path(queue.state_file).parent.mkdir(parents=True, exist_ok=True)
        Path(queue.state_file).write_text(json.dumps(state))

        queue.log_activity("note")
        today_stats = queue.get_today_stats()
        assert today_stats.get("note", 0) == 1  # only today's entry
