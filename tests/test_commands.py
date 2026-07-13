"""Tests for command parser — parse_message()."""
from datetime import date, timedelta

import pytest

from src.commands import ParsedCommand, parse_message


class TestUrlCommands:
    def test_bare_url(self):
        cmd = parse_message("https://example.com/article")
        assert cmd.command_type == "url"
        assert cmd.url == "https://example.com/article"
        assert cmd.user_notes is None

    def test_url_with_user_notes_pipe(self):
        cmd = parse_message("https://example.com | focus on caching")
        assert cmd.command_type == "url"
        assert cmd.url == "https://example.com"
        assert cmd.user_notes == "focus on caching"

    def test_url_with_notes_no_leading_space(self):
        cmd = parse_message("https://example.com |notes here")
        assert cmd.command_type == "url"
        assert cmd.user_notes == "notes here"

    def test_youtube_url(self):
        cmd = parse_message("https://www.youtube.com/watch?v=abc123")
        assert cmd.command_type == "url"
        assert "youtube.com" in cmd.url


class TestHelpCommand:
    def test_help(self):
        assert parse_message("/help").command_type == "help"

    def test_help_uppercase(self):
        assert parse_message("/HELP").command_type == "help"

    def test_help_with_trailing_text(self):
        # /help anything → still help
        assert parse_message("/help me").command_type == "help"


class TestSearchCommand:
    def test_search_basic(self):
        cmd = parse_message("/search attention mechanism")
        assert cmd.command_type == "search"
        assert cmd.text == "attention mechanism"

    def test_search_single_word(self):
        cmd = parse_message("/search python")
        assert cmd.command_type == "search"
        assert cmd.text == "python"

    def test_search_empty(self):
        cmd = parse_message("/search")
        assert cmd.command_type == "search"
        assert cmd.text == ""


class TestStatusCommand:
    def test_status(self):
        assert parse_message("/status").command_type == "status"

    def test_status_uppercase(self):
        assert parse_message("/STATUS").command_type == "status"


class TestTodoCommand:
    def test_todo_no_date_goes_to_inbox(self):
        cmd = parse_message("/todo Buy groceries")
        assert cmd.command_type == "todo_inbox"
        assert cmd.text == "Buy groceries"
        assert cmd.due_date is None

    def test_todo_with_at_date_goes_to_deadline(self):
        cmd = parse_message("/todo Submit paper @tomorrow")
        assert cmd.command_type == "todo_deadline"
        assert cmd.due_date == date.today() + timedelta(days=1)

    def test_todo_with_iso_date(self):
        cmd = parse_message("/todo Tax return @2026-04-30")
        assert cmd.command_type == "todo_deadline"
        assert cmd.due_date == date(2026, 4, 30)

    def test_todo_with_weekday(self):
        cmd = parse_message("/todo Meeting @monday")
        assert cmd.command_type == "todo_deadline"
        assert cmd.due_date is not None
        assert cmd.due_date.weekday() == 0  # Monday

    def test_todo_text_stripped_of_date(self):
        cmd = parse_message("/todo Submit report @friday")
        assert cmd.text == "Submit report"


class TestSomedayCommand:
    def test_someday(self):
        cmd = parse_message("/someday Learn Rust")
        assert cmd.command_type == "someday"
        assert cmd.text == "Learn Rust"


class TestReadCommand:
    def test_read_title(self):
        cmd = parse_message("/read The Buried Giant")
        assert cmd.command_type == "read"
        assert cmd.text == "The Buried Giant"
        assert cmd.url is None

    def test_read_url(self):
        cmd = parse_message("/read https://example.com/article")
        assert cmd.command_type == "read"
        assert cmd.url == "https://example.com/article"


class TestWatchCommand:
    def test_watch_title(self):
        cmd = parse_message("/watch Inception")
        assert cmd.command_type == "watch"
        assert cmd.text == "Inception"

    def test_watch_url(self):
        cmd = parse_message("/watch https://youtube.com/watch?v=xyz")
        assert cmd.command_type == "watch"
        assert cmd.url is not None


class TestNoteCommand:
    def test_note_text(self):
        cmd = parse_message("/note Attention is a soft dictionary lookup")
        assert cmd.command_type == "fleeting_note"
        assert cmd.text == "Attention is a soft dictionary lookup"
        assert cmd.is_raw is False

    def test_note_url_is_raw(self):
        cmd = parse_message("/note https://arxiv.org/abs/2501.12948")
        assert cmd.command_type == "fleeting_note"
        assert cmd.is_raw is True
        assert cmd.url == "https://arxiv.org/abs/2501.12948"


class TestDailyCommand:
    def test_daily_note(self):
        cmd = parse_message("/daily Interesting conversation")
        assert cmd.command_type == "daily_note"
        assert cmd.text == "Interesting conversation"

    def test_daily_task(self):
        cmd = parse_message("/daily task Review PR")
        assert cmd.command_type == "daily_task"
        assert cmd.text == "Review PR"

    def test_daily_task_case_insensitive(self):
        cmd = parse_message("/daily Task Fix bug")
        assert cmd.command_type == "daily_task"


class TestProjectCommand:
    def test_project_note(self):
        cmd = parse_message("/project obsidian-helper Consider async polling")
        assert cmd.command_type == "project_note"
        assert cmd.project == "obsidian-helper"
        assert cmd.text == "Consider async polling"

    def test_project_todo(self):
        cmd = parse_message("/project myapp todo Add tests")
        assert cmd.command_type == "project_todo"
        assert cmd.project == "myapp"
        assert cmd.text == "Add tests"

    def test_project_todo_with_date(self):
        cmd = parse_message("/project myapp todo Fix auth @2026-05-01")
        assert cmd.command_type == "project_todo"
        assert cmd.due_date == date(2026, 5, 1)
        assert cmd.text == "Fix auth"


class TestEventCommand:
    def test_event_with_iso_datetime(self):
        cmd = parse_message("/event Dentist 2026-04-10 14:00")
        assert cmd.command_type == "event"
        assert cmd.text == "Dentist"
        assert cmd.event_datetime is not None
        assert cmd.event_datetime.year == 2026
        assert cmd.event_datetime.hour == 14

    def test_event_no_datetime(self):
        cmd = parse_message("/event Something vague")
        assert cmd.command_type == "event"
        # datetime may or may not parse; just check type
        assert cmd.text is not None


class TestQuestionCommand:
    def test_reply_to_bot_message_becomes_question(self):
        reply_text = "Created note: *Why Transformers Work*\nSaved to: `02 Sources/Videos`"
        cmd = parse_message("Why did attention replace recurrence?", reply_to_text=reply_text)
        assert cmd.command_type == "question"
        assert cmd.reply_to_title == "Why Transformers Work"
        assert cmd.text == "Why did attention replace recurrence?"

    def test_reply_to_non_bot_message_is_inbox(self):
        cmd = parse_message("some reply", reply_to_text="just a normal message")
        assert cmd.command_type == "inbox"


class TestCrawlCommand:
    def test_crawl_url_only(self):
        cmd = parse_message("/crawl https://youtube.com/@lex")
        assert cmd.command_type == "channel_crawl"
        assert cmd.url == "https://youtube.com/@lex"
        assert cmd.crawl_from is None
        assert cmd.crawl_to is None
        assert cmd.crawl_topic is None

    def test_crawl_with_date_range(self):
        cmd = parse_message(
            "/crawl https://youtube.com/@lex from:2026-01-01 to:2026-05-01"
        )
        assert cmd.command_type == "channel_crawl"
        assert cmd.crawl_from == date(2026, 1, 1)
        assert cmd.crawl_to == date(2026, 5, 1)

    def test_crawl_with_quoted_topic(self):
        cmd = parse_message('/crawl https://youtube.com/@lex topic:"AI safety"')
        assert cmd.command_type == "channel_crawl"
        assert cmd.crawl_topic == "AI safety"

    def test_crawl_with_bare_topic(self):
        cmd = parse_message("/crawl https://youtube.com/@lex topic:rust")
        assert cmd.crawl_topic == "rust"

    def test_crawl_all_args(self):
        cmd = parse_message(
            '/crawl https://youtube.com/@lex from:2026-01-01 to:2026-05-01 topic:"AI safety"'
        )
        assert cmd.url == "https://youtube.com/@lex"
        assert cmd.crawl_from == date(2026, 1, 1)
        assert cmd.crawl_to == date(2026, 5, 1)
        assert cmd.crawl_topic == "AI safety"

    def test_crawl_missing_url(self):
        cmd = parse_message("/crawl topic:rust")
        assert cmd.command_type == "channel_crawl"
        assert cmd.url is None


class TestBackfillCommand:
    def test_instagram_saved(self):
        cmd = parse_message("/backfill instagram saved")
        assert cmd.command_type == "backfill"
        assert cmd.backfill_kind == "saved"
        assert cmd.url is None

    def test_instagram_likes(self):
        cmd = parse_message("/backfill instagram likes")
        assert cmd.command_type == "backfill"
        assert cmd.backfill_kind == "likes"

    def test_instagram_saved_is_case_insensitive(self):
        cmd = parse_message("/backfill Instagram Saved")
        assert cmd.backfill_kind == "saved"

    def test_instagram_with_date_range(self):
        cmd = parse_message("/backfill instagram likes from:2026-01-01 to:2026-05-01")
        assert cmd.backfill_kind == "likes"
        assert cmd.crawl_from == date(2026, 1, 1)
        assert cmd.crawl_to == date(2026, 5, 1)

    def test_playlist_url(self):
        cmd = parse_message("/backfill https://youtube.com/playlist?list=LL")
        assert cmd.command_type == "backfill"
        assert cmd.backfill_kind is None
        assert cmd.url == "https://youtube.com/playlist?list=LL"

    def test_playlist_url_with_topic(self):
        cmd = parse_message('/backfill https://youtube.com/playlist?list=LL topic:"cooking"')
        assert cmd.url == "https://youtube.com/playlist?list=LL"
        assert cmd.crawl_topic == "cooking"

    def test_bare_backfill_has_no_kind_or_url(self):
        cmd = parse_message("/backfill")
        assert cmd.command_type == "backfill"
        assert cmd.backfill_kind is None
        assert cmd.url is None


class TestInboxFallback:
    def test_plain_text_goes_to_inbox(self):
        cmd = parse_message("Just a random thought")
        assert cmd.command_type == "inbox"
        assert cmd.text == "Just a random thought"

    def test_empty_command_prefix_goes_to_inbox(self):
        cmd = parse_message("random text without commands")
        assert cmd.command_type == "inbox"
