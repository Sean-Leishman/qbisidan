"""Todo workflow: matching, moving between trackers, and the triage / /done button flows."""
from datetime import date
from types import SimpleNamespace

from src import todos
from src.agents.vault_writer import VaultWriter
from src.commands import ParsedCommand
from src.processor import Processor
from src.telegram_queue import TelegramMessage

SUN = date(2026, 11, 15)
MON = date(2026, 11, 16)

INBOX = """---
title: Inbox
---
Unsorted TODOs.

---

- [ ] Sell items on Vinted  <!-- 2026-11-15 -->
- [ ] Book dental check-up and hygienist appointment
- [ ] Find dentist
"""
WEEK = """---
title: This Week
---
## Week of 2026-11-09

- [ ] Old unfinished thing
- [x] Old finished thing

*a footer note*
"""
DEADLINES = """---
title: Deadlines
---

- [ ] 2026-04-07 — Mum's birthday
- [ ] 2026-11-27 — Claire's birthday
- [ ] 2028-10-28 — Passport renewal
"""


class TestPure:
    def test_matches_needs_every_word_and_takes_partial_words(self):
        assert todos.matches("vint", "Sell items on Vinted")
        assert todos.matches("dental check", "Book dental check-up and hygienist appointment")
        assert not todos.matches("dental insurance", "Book dental check-up")
        assert not todos.matches("   ", "anything")

    def test_key_ignores_inbox_date_stamp(self):
        assert todos.key("Sell items on Vinted  <!-- 2026-11-15 -->") == todos.key("Sell items on Vinted")

    def test_tick_marks_in_place_with_date(self):
        content, text = todos.tick(INBOX, todos.key("Find dentist"), SUN)
        assert text == "Find dentist" and "- [x] Find dentist ✅ 2026-11-15" in content
        assert "Find dentist" not in todos.open_items(content)

    def test_deadline_inserted_in_date_order_with_file_convention(self):
        out = todos.add_deadline(DEADLINES, "Sell items on Vinted", date(2026, 11, 20))
        lines = [l for l in out.splitlines() if l.startswith("- [")]
        assert lines[1] == "- [ ] 2026-11-20 — Sell items on Vinted"

    def test_sunday_plans_next_week_and_old_week_items_are_stale(self):
        assert todos.target_monday(SUN) == MON and todos.target_monday(MON) == MON
        assert todos.stale_week_items(WEEK, MON) == ["Old unfinished thing"]
        out = todos.add_to_week(WEEK, "New thing", MON)
        assert todos.week_items(out, MON) == ["New thing"]
        out = todos.add_to_week(out, "Second", MON)
        assert todos.week_items(out, MON) == ["New thing", "Second"]

    def test_someday_parks_under_one_heading(self):
        out = todos.add_someday(todos.add_someday("## Work\n- [ ] a\n", "x"), "y")
        assert out.count(todos.SOMEDAY_SECTION) == 1
        assert out.split(todos.SOMEDAY_SECTION)[1].split() == ["-", "[", "]", "x", "-", "[", "]", "y"]


class FakeTelegram:
    """Records cards; wait_for_callback replays the taps queued in .taps."""

    def __init__(self):
        self.sent, self.edits, self.notes, self.taps = [], [], [], []

    def send_buttons(self, chat_id, text, keyboard):
        self.sent.append((text, keyboard))
        return 99

    def edit_message(self, chat_id, message_id, text, keyboard=None):
        self.edits.append((text, keyboard))
        return True

    def send_notification(self, chat_id, text):
        self.notes.append(text)
        return True

    def wait_for_callback(self, chat_id, message_id, timeout):
        while self.taps:
            yield self.taps.pop(0)


def make(tmp_path, monkeypatch):
    for rel, body in (("08 Trackers/Inbox.md", INBOX), ("08 Trackers/This Week.md", WEEK),
                      ("08 Trackers/Deadlines.md", DEADLINES)):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(body)
    monkeypatch.setattr(todos, "target_monday", lambda today: MON)
    p = Processor.__new__(Processor)  # skip __init__: no config/AI needed
    p.vault_writer = VaultWriter(vault_path=tmp_path)
    p.telegram = FakeTelegram()
    return p


def tap(card, label):
    return next(b["callback_data"] for row in card[1] for b in row if label in b["text"])


class TestTriageFlow:
    def test_each_button_moves_the_item_and_shows_the_next(self, tmp_path, monkeypatch):
        p = make(tmp_path, monkeypatch)
        tg = p.telegram
        # Queue: last week's leftover, then the 3 Inbox items.
        first = p._triage_card(0)
        assert "Old unfinished thing" in first[0] and "1/4" in first[0]
        tg.taps = [tap(first, "This week")]
        p._send_card_and_wait(1, *first)
        card = tg.edits[-1]
        assert "Sell items on Vinted" in card[0]  # carried-over item left the queue
        tg.taps = [tap(card, "Deadline")]
        p._handle_todo_tap(1, 99, tg.taps.pop())
        menu = tg.edits[-1]
        p._handle_todo_tap(1, 99, menu[1][0][0]["callback_data"])  # first date offered
        card = tg.edits[-1]
        assert "Book dental" in card[0]
        p._handle_todo_tap(1, 99, tap(card, "Skip"))
        card = tg.edits[-1]
        assert "Find dentist" in card[0]
        finished = p._handle_todo_tap(1, 99, tap(card, "Done"))
        assert finished and "Triage done" in tg.edits[-1][0]

        week = p.vault_writer.read_tracker("w")
        assert todos.week_items(week, MON) == ["Old unfinished thing"]
        assert todos.stale_week_items(week, MON) == []
        assert "— Sell items on Vinted" in p.vault_writer.read_tracker("d")
        inbox = p.vault_writer.read_tracker("i")
        assert todos.open_items(inbox) == ["Book dental check-up and hygienist appointment"]  # skipped stays
        assert "- [x] Find dentist" in inbox

    def test_stale_tap_does_not_touch_files(self, tmp_path, monkeypatch):
        p = make(tmp_path, monkeypatch)
        before = p.vault_writer.read_tracker("i")
        p._handle_todo_tap(1, 99, "tg:r:0:ideadbeef")
        assert p.vault_writer.read_tracker("i") == before


class TestDone:
    def msg(self):
        return TelegramMessage(1, 1, 1, "", 0)

    def test_single_match_ticks(self, tmp_path, monkeypatch):
        p = make(tmp_path, monkeypatch)
        r = p._process_done(ParsedCommand("done", "vint"), self.msg())
        assert "Sell items on Vinted" in r.message and "Inbox" in r.message
        assert "- [x] Sell items on Vinted" in p.vault_writer.read_tracker("i")

    def test_several_matches_ask_then_tick_the_tapped_one(self, tmp_path, monkeypatch):
        p = make(tmp_path, monkeypatch)
        p.telegram.taps = ["dn:i" + todos.key("Find dentist")]
        p._process_done(ParsedCommand("done", "dent"), self.msg())
        assert len(p.telegram.sent[0][1]) == 2
        assert "Done: Find dentist" in p.telegram.edits[-1][0]
        assert todos.open_items(p.vault_writer.read_tracker("i")) == [
            "Sell items on Vinted", "Book dental check-up and hygienist appointment"]

    def test_no_match_changes_nothing(self, tmp_path, monkeypatch):
        p = make(tmp_path, monkeypatch)
        r = p._process_done(ParsedCommand("done", "zebra"), self.msg())
        assert "No open todo" in r.message
