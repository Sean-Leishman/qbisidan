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


class TestErrorRedaction:
    def test_token_never_in_error_text_and_telegram_reason_kept(self, tmp_path):
        q = TelegramQueue(bot_token="123:SECRET", allowed_chat_ids=[1], state_file=str(tmp_path / "s.json"))
        resp = MagicMock(text='{"description":"Bad Request: can\'t parse entities"}')
        e = requests.HTTPError("400 for url: https://api.telegram.org/bot123:SECRET/sendMessage", response=resp)
        msg = q._err(e)
        assert "SECRET" not in msg and "<token>" in msg and "parse entities" in msg


class TestMissedUpdates:
    @staticmethod
    def _fetch(queue, first_id, n=1):
        updates = [{"update_id": first_id + i,
                    "message": {"message_id": i, "chat": {"id": 123}, "text": "hi", "date": 0}}
                   for i in range(n)]
        resp = MagicMock(json=lambda: {"result": updates})
        with patch("src.telegram_queue.requests.get", return_value=resp), \
             patch.object(queue, "send_notification") as notify:
            msgs = queue.get_pending_messages()
        return msgs, notify

    def _seen(self, queue, update_id, ago_s):
        import time
        queue.last_update_id = update_id
        state = queue._load_state()
        state["last_update_at"] = time.time() - ago_s
        queue._save_state(state)

    def test_gap_reports_exact_count_and_still_delivers_new_messages(self, queue):
        self._seen(queue, 100, ago_s=3 * 86400)
        msgs, notify = self._fetch(queue, first_id=104)
        assert len(msgs) == 1
        notify.assert_called_once()
        assert notify.call_args.args[0] == 123 and "Missed 3 messages" in notify.call_args.args[1]

    def test_no_gap_no_report(self, queue):
        self._seen(queue, 100, ago_s=3 * 86400)
        _, notify = self._fetch(queue, first_id=101, n=2)
        notify.assert_not_called()

    def test_after_a_week_ids_are_random_so_no_count(self, queue):
        self._seen(queue, 100, ago_s=8 * 86400)
        _, notify = self._fetch(queue, first_id=5_000_000)
        assert "unknowable" in notify.call_args.args[1]

    def test_first_ever_run_is_not_a_gap(self, queue):
        _, notify = self._fetch(queue, first_id=5_000_000)
        notify.assert_not_called()


def test_second_poller_is_refused_until_the_first_exits(tmp_path):
    import importlib.util, sys
    spec = importlib.util.spec_from_file_location("main_mod", Path(__file__).parent.parent / "main.py")
    main_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(main_mod)
    lock = str(tmp_path / "run.lock")
    first = main_mod.poll_lock(lock)
    assert first is not None and main_mod.poll_lock(lock) is None
    first.close()
    assert main_mod.poll_lock(lock) is not None


class TestRegisterCommands:
    COMMANDS = [("todo", "Add a todo"), ("help", "Full command reference")]

    def test_sends_set_my_commands_once(self, queue):
        ok = MagicMock(status_code=200)
        with patch("src.telegram_queue.requests.post", return_value=ok) as post:
            assert queue.register_commands(self.COMMANDS)
            assert queue.register_commands(self.COMMANDS)
        post.assert_called_once()
        assert post.call_args.args[0].endswith("/setMyCommands")
        assert post.call_args.kwargs["json"]["commands"][0] == {"command": "todo", "description": "Add a todo"}

    def test_resends_when_list_changes(self, queue):
        with patch("src.telegram_queue.requests.post", return_value=MagicMock(status_code=200)) as post:
            queue.register_commands(self.COMMANDS)
            queue.register_commands(self.COMMANDS + [("status", "Today's summary")])
        assert post.call_count == 2

    def test_failure_is_retried_next_run(self, queue):
        bad = MagicMock()
        bad.raise_for_status.side_effect = requests.HTTPError("400")
        with patch("src.telegram_queue.requests.post", return_value=bad):
            assert not queue.register_commands(self.COMMANDS)
        assert queue.get_flag("registered_commands") is None


def test_bot_commands_meet_telegram_limits():
    from src.commands import BOT_COMMANDS, parse_message
    import re
    assert len(BOT_COMMANDS) <= 100
    for name, text in BOT_COMMANDS:
        assert re.fullmatch(r"[a-z0-9_]{1,32}", name)
        assert 1 <= len(text) <= 256
        assert parse_message(f"/{name} a b").command_type != "inbox", name
