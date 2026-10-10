"""Tests for TelegramQueue activity tracking, inline keyboards, and callback polling."""
import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests

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


def _mock_response(result, status_code=200):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = {"result": result}
    resp.raise_for_status = MagicMock()
    return resp


class TestInlineKeyboardMethods:
    def test_send_buttons_returns_message_id(self, queue):
        resp = _mock_response({"message_id": 42})
        keyboard = [[{"text": "Start", "callback_data": "start"}]]
        with patch("src.telegram_queue.requests.post", return_value=resp) as mock_post:
            message_id = queue.send_buttons(123, "pick one", keyboard)

        assert message_id == 42
        assert "sendMessage" in mock_post.call_args[0][0]
        sent_markup = mock_post.call_args[1]["json"]["reply_markup"]
        assert sent_markup == {"inline_keyboard": keyboard}

    def test_send_buttons_returns_none_on_failure(self, queue):
        with patch("src.telegram_queue.requests.post", side_effect=requests.RequestException("boom")):
            assert queue.send_buttons(123, "text", [[]]) is None

    def test_edit_message_reply_markup_success(self, queue):
        resp = _mock_response({})
        keyboard = [[{"text": "x", "callback_data": "v0"}]]
        with patch("src.telegram_queue.requests.post", return_value=resp) as mock_post:
            ok = queue.edit_message_reply_markup(123, 42, keyboard)

        assert ok is True
        assert "editMessageReplyMarkup" in mock_post.call_args[0][0]
        assert mock_post.call_args[1]["json"]["reply_markup"] == {"inline_keyboard": keyboard}

    def test_answer_callback_query_success(self, queue):
        resp = _mock_response({})
        with patch("src.telegram_queue.requests.post", return_value=resp) as mock_post:
            ok = queue.answer_callback_query("cbq-123")

        assert ok is True
        assert "answerCallbackQuery" in mock_post.call_args[0][0]
        assert mock_post.call_args[1]["json"]["callback_query_id"] == "cbq-123"


class TestGetPendingMessagesSkipsCallbacks:
    def test_callback_query_updates_are_not_returned_as_messages(self, queue):
        updates = [
            {"update_id": 10, "callback_query": {"id": "x", "data": "v0", "message": {"message_id": 1}}},
            {
                "update_id": 11,
                "message": {"message_id": 2, "chat": {"id": 123}, "text": "hi", "date": 1},
            },
        ]
        resp = _mock_response(updates)
        with patch("src.telegram_queue.requests.get", return_value=resp):
            messages = queue.get_pending_messages()

        assert len(messages) == 1
        assert messages[0].text == "hi"
        # last_update_id must still advance past the callback_query update, or
        # get_pending_messages would refetch (and re-skip) it forever.
        assert queue.last_update_id == 11


class TestWaitForCallback:
    def test_buffers_non_callback_message_for_next_get_pending_messages(self, queue):
        """The single most subtle requirement: a button tap must not silently
        eat a message the user sent while the manifest was on screen."""
        batch = [
            {
                "update_id": 101,
                "message": {
                    "message_id": 5001,
                    "chat": {"id": 123},
                    "text": "hello while waiting",
                    "date": 1000,
                },
            },
            {
                "update_id": 102,
                "callback_query": {"id": "cbq1", "data": "start", "message": {"message_id": 777}},
            },
        ]
        get_resp = _mock_response(batch)
        post_resp = _mock_response({})

        # Consume like the real caller does: stop at the first terminal tap
        # rather than exhausting the generator (which would poll forever).
        taps = []
        with patch("src.telegram_queue.requests.get", return_value=get_resp), \
             patch("src.telegram_queue.requests.post", return_value=post_resp) as mock_post:
            for tap in queue.wait_for_callback(chat_id=123, message_id=777, timeout=5):
                taps.append(tap)
                if tap in ("start", "cancel"):
                    break

        assert taps == ["start"]
        mock_post.assert_called_once()  # answer_callback_query for the "start" tap
        assert queue.last_update_id == 102  # advanced past the whole batch

        # Telegram will not resend update 101 (offset moved past it) — the
        # buffered copy must be what get_pending_messages() returns instead.
        empty_resp = _mock_response([])
        with patch("src.telegram_queue.requests.get", return_value=empty_resp):
            messages = queue.get_pending_messages()

        assert len(messages) == 1
        assert messages[0].text == "hello while waiting"
        assert messages[0].message_id == 5001

    def test_buffered_message_survives_process_exit(self, queue, tmp_path):
        """run-once/cron exits as soon as the crawl ends. last_update_id is already
        saved past the buffered message, so Telegram will never resend it — the
        buffer MUST outlive the process or the message is lost permanently."""
        batch = [
            {
                "update_id": 201,
                "message": {
                    "message_id": 6001,
                    "chat": {"id": 123},
                    "text": "sent while manifest was open",
                    "date": 1000,
                },
            },
            {
                "update_id": 202,
                "callback_query": {"id": "cbq1", "data": "start", "message": {"message_id": 777}},
            },
        ]
        with patch("src.telegram_queue.requests.get", return_value=_mock_response(batch)), \
             patch("src.telegram_queue.requests.post", return_value=_mock_response({})):
            for tap in queue.wait_for_callback(chat_id=123, message_id=777, timeout=5):
                if tap == "start":
                    break

        # Process dies here. A fresh queue reads the same state file — as the next
        # cron run would.
        reborn = TelegramQueue(
            bot_token="t", allowed_chat_ids=[123], state_file=str(queue.state_file)
        )
        with patch("src.telegram_queue.requests.get", return_value=_mock_response([])):
            messages = reborn.get_pending_messages()

        assert [m.text for m in messages] == ["sent while manifest was open"]

        # And it is not redelivered forever.
        with patch("src.telegram_queue.requests.get", return_value=_mock_response([])):
            assert reborn.get_pending_messages() == []

    def test_stops_iterating_once_terminal_tap_consumed(self, queue):
        """Taps on a different message_id (stale buttons) are answered but not yielded."""
        batch = [
            {
                "update_id": 201,
                "callback_query": {"id": "cbq-stale", "data": "v0", "message": {"message_id": 999}},
            },
            {
                "update_id": 202,
                "callback_query": {"id": "cbq-real", "data": "v0", "message": {"message_id": 777}},
            },
        ]
        get_resp = _mock_response(batch)
        post_resp = _mock_response({})

        taps = []
        with patch("src.telegram_queue.requests.get", return_value=get_resp), \
             patch("src.telegram_queue.requests.post", return_value=post_resp) as mock_post:
            for tap in queue.wait_for_callback(chat_id=123, message_id=777, timeout=5):
                taps.append(tap)
                break  # caller only cares about the first tap here

        assert taps == ["v0"]  # only the tap for our message_id is yielded
        assert mock_post.call_count == 2  # but both taps get answered


class TestRetryFailed:
    def test_failed_message_retried_then_dropped_after_max_attempts(self, queue):
        """A failure must not be marked done: run() requeues it until MAX_ATTEMPTS, then drops it."""
        from src.processor import MAX_ATTEMPTS, ProcessingResult, Processor
        from src.telegram_queue import TelegramMessage

        proc = Processor.__new__(Processor)  # skip __init__: no config/AI needed
        proc.telegram = queue
        proc.process_message = MagicMock(side_effect=RuntimeError("scrape blew up"))
        queue._buffer_message(TelegramMessage(1, 1, 123, "https://x.test/r", 0))

        no_updates = MagicMock(json=lambda: {"result": []})
        with patch("src.telegram_queue.requests.get", return_value=no_updates):
            for _ in range(MAX_ATTEMPTS):
                assert len(proc.run()) == 1
            assert proc.run() == []
