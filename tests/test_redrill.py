"""Re-drills: parsing the teach skill's real formats, skip rules, grading, and the /drill buttons."""
from datetime import date, datetime, timedelta
from types import SimpleNamespace

from src import redrill
from src.agents.redrill_agent import RedrillQuestionAgent
from src.agents.vault_writer import VaultWriter
from src.commands import parse_message
from src.processor import Processor
from src.redrill import Item
from src.telegram_queue import TelegramMessage

# ── fixtures: excerpts copied from the real vault files ─────────────────────────

# 02 Sources/Lessons/2026-10-17 Virtual Memory.md — the block sits in a fence inside a callout.
VM = """---
created: 2026-10-17
tags: [lesson]
summary:
---
#Inbox

> [!abstract] CLAUDE
> You started the session recognising answers without being able to derive them.
>
> ```
> ## Gaps
> - MMU/hardware never reads VMAs (missed 3×, first right at conf 3 at the end) — HW walks only the page table via CR3 and checks PTE bits; kernel reads VMAs only after a fault — due 2026-10-19
> - Minor vs major fault — major = waits on I/O; minor = resolved from RAM, however much work (zeroing, copying) — due 2026-10-19
> - Cost of a TLB miss beyond "a walk" — up to 4 dependent reads, each of which can itself miss cache; huge pages shorten the walk and shrink tables — due 2026-10-19
> ## Next
> - Write the Base Note in your own words, then `file this`.
> ```
"""

# 02 Sources/Lessons/2026-10-19 Re-drill — OS and C++.md — two Gaps/Retired blocks in one note.
REDRILL = """---
created: 2026-10-19
tags: [lesson]
summary:
---
#Inbox

> [!abstract] CLAUDE
> ## Gaps
> - SSO limit in libstdc++ (said 64 again) — 15 chars inline: sizeof(std::string) = 32 = pointer 8 + size 8 + 16-byte buffer (15 + '\\0') — due 2026-10-19
> - std::map as a hash-flooding defence (2nd miss) — a tree compares keys and never hashes them, so the worst case is O(log n) whatever the keys — due 2026-10-19
> - Three distinct UB kinds — lifetime (read before construct, double destroy), out of bounds, signed overflow, misalignment — not three variants of one kind — due 2026-10-19
> ## Retired
> - vector insert direction (p before the insert point valid, q after it invalid)
> ## Next
> - Write the Base Note in your own words, then `file this`.


> [!abstract] CLAUDE
> - **C6 ✓, retired.** Out of bounds, use after lifetime, and signed overflow are three distinct kinds.
>
> ## Gaps
> - deque is a linked list (committed) — deque = array of pointers to fixed blocks; push_back invalidates iterators (pointer array may reallocate) but not references (elements never move) — due 2026-10-21
> - std::map as a hash-flooding defence (3rd miss) — compares and never hashes, so O(log n) for any keys — due 2026-10-21
> - TLB-miss cost — up to 4 dependent reads, each of which can miss cache (~100 ns each) — due 2026-10-21
> ## Retired
> - Three distinct UB kinds
> - Anonymous first-touch fault path: VMA check, zeroed frame, PTE, re-execute; minor because there's no I/O
> ## Next
> - Write the Base Note in your own words, then `file this`.
"""

# 02 Sources/Lessons/2026-10-13 C++ Foundations.md — undated, "miss: correction" lines.
CPP = """---
created: 2026-10-13
tags: [lesson]
summary:
---
Inbox

> [!abstract] CLAUDE
> ## Gaps
> - Coroutine locals live on the stack while suspended (missed at confidence 4): suspending *returns* to the caller and pops the stack frame.
> - `-lutil main.o` links: archives are scanned once, left to right, and only fill symbols that are *already* needed. List each library after whatever uses it.
>
> ## Next
> - Write the Base Note in your own words, then `file this`.
"""

# 07 Projects/Interview Prep/Todo.md — Quiz log rows (newest first), cells verbatim.
QUIZ = """## Done

- [x] something

## Quiz log (C++ bank + topic quizzes; re-drill misses next session, then +4, +10 days)

| Date | Section | Score | Misses to re-drill |
|---|---|---|---|
| 2026-10-20 | **C++ §3 drill: smart pointers + virtual dispatch** (first time; 2 sets + 3 recall) | smart ptrs **4/4** (conf 2) · virtual dtor **0/4** (conf 2) · recall ~2/3 | due **2026-10-21**: virtual-dtor effects (UB via Base*, dtor suppresses moves, vptr → not trivially copyable, sizeof grows) · `shared_ptr(this)` = second control block → double delete · virtual cost = no inlining (`final` devirtualises). Retired to +10: `make_shared` one alloc · `weak_ptr` keeps block not object · by-value `shared_ptr` = atomic inc/dec · `unique_ptr` size · virtual call in ctor → Base |
| 2026-10-19 | **OS + C++ re-drill** (VM gaps from 10-17 + isolation; C++ items due 10-19; 10 Qs) | **OS ~60%** (fix-ups 2/2) · **C++ ~70%** | due **2026-10-21**: **`deque` is not a linked list** (map of fixed blocks: `push_back` invalidates **iterators**, not references) · **`std::map` as a flooding defence** (3rd miss) · TLB-miss cost = up to 4 *dependent* reads, each can miss cache. Retired to +10 (2026-10-29): **SSO 15 chars, derived (conf 4)** · 3 distinct UB kinds · minor = no I/O |
| 2026-10-14 | **Q3/STL re-drill #1** (6 Qs, reworded) | **~39%** (7/18) | due **+1 (2026-10-15)**: **`vector` invalidation** (0/3: thought `push_back` at capacity, `erase` before `p`, and `reserve` all keep `p`) · `emplace_back(10)` = size 10 · **SSO** definition + 15 chars. Due +4 (2026-10-18): hash-flooding defences (keyed hash, `std::map`) · `list` splice / non-movable types · DFS O(depth) vs BFS O(frontier) |
| 2026-10-13 | **DB indexing re-drill #2: covering indexes** (3 scenario Qs) | **9/9 (100%)**, blank on 10-07 and 10-09 | none. Retired to +10 (**2026-10-23**). Strong: `INCLUDE` blocks HOT updates. Next DB topic: **partitioning/sharding** (after 20 Oct) |
| 2026-10-13 | **Q3 short-answer + C++ §4 STL** (7 Qs, explicit asks) | **~38%** (8/21) | due +1 (**2026-10-14**), then +4: `list` when (stable iterators, splice; = Q2) · **SSO** (15 chars libstdc++; moving short strings copies). Partial: DFS O(depth) memory; BFS shortest only if unweighted |
| 2026-10-08 | **C++ §1 re-drill** (the 10 misses, cold, reordered) | **~60%** (4 right · 4 partial · 2 blank) | due +4 (2026-10-12): `decltype((x))` · slicing needs "by value". Retired to +10: `static`, `noexcept` moves, `constexpr`/`consteval`, `move` is a cast |
| 2026-10-07 | C++ bank §1 language core (16 Qs) | ~47% (5 right · 5 partial · 6 blank) | `static` ×4 · `noexcept` moves · slicing · `decltype((x))` |

## System design log (one mock a week)

| Date | Prompt | Weakest step | Fix next time |
|---|---|---|---|
"""

LESSONS = [("2026-10-13 C++ Foundations.md", CPP), ("2026-10-17 Virtual Memory.md", VM),
           ("2026-10-19 Re-drill — OS and C++.md", REDRILL)]
TODAY = date(2026, 11, 15)


def heads(items):
    return [redrill.head(it.text) for it in items]


# ── parsing ─────────────────────────────────────────────────────────────────────

class TestLessonParsing:
    def test_quoted_fenced_block(self):
        [b] = redrill.lesson_blocks("2026-10-17 Virtual Memory.md", VM)
        assert b.when == date(2026, 10, 17) and b.topic == "Virtual Memory"
        assert [k for k, _, _ in b.events] == ["gap"] * 3
        kind, text, due = b.events[0]
        assert due == date(2026, 10, 19)
        assert text.startswith("MMU/hardware never reads VMAs") and not text.endswith("2026-10-19")

    def test_two_blocks_with_retired(self):
        first, second = redrill.lesson_blocks("2026-10-19 Re-drill — OS and C++.md", REDRILL)
        assert first.events[-1] == ("retired", "vector insert direction (p before the insert point valid, "
                                    "q after it invalid)", None)
        assert [e[1] for e in second.events if e[0] == "retired"] == [
            "Three distinct UB kinds",
            "Anonymous first-touch fault path: VMA check, zeroed frame, PTE, re-execute; minor because there's no I/O"]

    def test_undated_colon_lines_and_blank_line_ends_block(self):
        [b] = redrill.lesson_blocks("2026-10-13 C++ Foundations.md", CPP)
        assert len(b.events) == 2 and all(due is None for _, _, due in b.events)
        assert redrill.head(b.events[0][1]) == "coroutine locals live on the stack while suspended"

    def test_double_md_extension_title(self):
        assert redrill.lesson_topic("2026-10-10 Pattern — Arrays & Hashing.md.md") == "Pattern — Arrays & Hashing"

    def test_head_ignores_parentheticals_and_correction(self):
        a = "std::map as a hash-flooding defence (2nd miss) — a tree compares keys — due 2026-10-19"
        b = "std::map as a hash-flooding defence (3rd miss) — compares and never hashes"
        assert redrill.key(a) == redrill.key(b)


class TestQuizParsing:
    def test_modern_cell(self):
        cell = ("due **2026-10-21**: virtual-dtor effects (UB via Base*, dtor suppresses moves) · "
                "`shared_ptr(this)` = second control block → double delete. "
                "Retired to +10: `make_shared` one alloc · `unique_ptr` size")
        ev = redrill.quiz_cell_events(cell)
        assert ev == [("gap", "virtual-dtor effects (UB via Base*, dtor suppresses moves)", date(2026, 10, 21)),
                      ("gap", "`shared_ptr(this)` = second control block → double delete", date(2026, 10, 21)),
                      ("retired", "`make_shared` one alloc", None), ("retired", "`unique_ptr` size", None)]

    def test_rows_oldest_first_and_older_formats(self):
        rows = redrill.quiz_rows(QUIZ)
        assert [r.when for r in rows] == sorted(r.when for r in rows)
        assert rows[0].events == [("gap", t, None) for t in
                                  ("`static` ×4", "`noexcept` moves", "slicing", "`decltype((x))`")]
        by_date = {}
        for r in rows:
            by_date.setdefault(r.when, []).append(r)
        stl = by_date[date(2026, 10, 14)][0].events
        assert stl[0] == ("gap", "**`vector` invalidation** (0/3: thought `push_back` at capacity, "
                          "`erase` before `p`, and `reserve` all keep `p`)", date(2026, 10, 15))
        assert ("gap", "`list` splice / non-movable types", date(2026, 10, 18)) in stl
        assert [r.events for r in by_date[date(2026, 10, 13)] if "DB" in r.topic] == [[]]  # "none."
        short = next(r for r in by_date[date(2026, 10, 13)] if "STL" in r.topic).events
        assert short[-1] == ("gap", "DFS O(depth) memory; BFS shortest only if unweighted", None)  # Partial:
        old = by_date[date(2026, 10, 8)][0].events
        assert ("retired", "`constexpr`/`consteval`", None) in old  # comma-separated list

    def test_stops_at_next_section(self):
        assert all(r.topic.startswith("Quiz log") for r in redrill.quiz_rows(QUIZ))
        assert len(redrill.quiz_rows(QUIZ)) == 7


# ── skip / override rules ───────────────────────────────────────────────────────

class TestSkipRules:
    def test_due_set_from_real_formats(self):
        due = redrill.collect(LESSONS, QUIZ, TODAY)
        h = heads(due)
        # Undated lines count as due, and sort first.
        assert h[:2] == ["coroutine locals live on the stack while suspended", "lutil main o links"]
        # Re-dated by the later block: one copy, with the later date.
        maps = [it for it in due if "flooding" in it.text]
        assert len(maps) == 1 and maps[0].due == date(2026, 10, 21) and "3rd miss" in maps[0].text
        # Retired by a later block.
        assert not any("three distinct ub kinds" in x for x in h)
        # "TLB-miss cost" re-dates the reworded "Cost of a TLB miss beyond a walk".
        assert sum("tlb" in x for x in h) == 1
        # The Quiz log's mirrors of the lesson lines are dropped (same due date, shared words) …
        assert not any("deque is not a linked list" in x for x in h)
        assert "deque is a linked list" in h
        # … but a Quiz-only miss stays.
        assert "virtual dtor effects" in h

    def test_quiz_redrill_session_supersedes_earlier_due_items(self):
        h = heads(redrill.collect(LESSONS, QUIZ, TODAY))
        # Due 10-12/10-15/10-18/10-19, all before the 10-19 re-drill session: gone.
        for stale in ("decltype", "vector invalidation", "list splice non movable types",
                      "sso limit in libstdc++", "mmu hardware never reads vmas", "minor vs major fault"):
            assert not any(stale in x for x in h), stale
        # Undated lesson lines are not touched by that rule.
        assert "coroutine locals live on the stack while suspended" in h

    def test_future_items_are_not_due(self):
        before = redrill.collect(LESSONS, QUIZ, date(2026, 10, 20))
        assert before and all(it.due is None or it.due <= date(2026, 10, 20) for it in before)
        assert len(redrill.collect(LESSONS, QUIZ, date(2026, 10, 21))) > len(before)

    def test_later_lesson_overrides_quiz_item_with_same_text_same_day(self):
        quiz = ("## Quiz log\n\n| Date | Section | Score | Misses |\n|---|---|---|---|\n"
                "| 2026-11-01 | **x drill** | 1/2 | due **2026-11-02**: `pmr` is one type · other thing |\n")
        later = empty = redrill.empty_note(date(2026, 11, 1))
        item = Item("`pmr` is one type", date(2026, 11, 2), "Quiz log · x drill", "quiz")
        later = redrill.add_result(empty, item, redrill.SURE, date(2026, 11, 1))
        due = redrill.collect([(redrill.note_name(date(2026, 11, 1)), later)], quiz, TODAY)
        assert heads(due) == ["other thing"]

    def test_same_is_not_too_loose(self):
        assert redrill.same("TLB-miss cost — x", "Cost of a TLB miss beyond \"a walk\" — y")
        assert redrill.same("`static` ×4", "`static`")
        assert not redrill.same("operator[] pitfalls — x", "operator[] on a missing key — y")
        assert not redrill.same("a — x", "b — x")


# ── grading and the results note ────────────────────────────────────────────────

class TestResults:
    def test_grade_arithmetic(self):
        assert redrill.new_due(redrill.MISSED, TODAY) == date(2026, 11, 19)
        assert redrill.new_due(redrill.UNSURE, TODAY) == date(2026, 11, 25)
        assert redrill.new_due(redrill.SURE, TODAY) is None

    def test_note_matches_lesson_format(self):
        item = Item("deque is a linked list (committed) — deque = array of pointers to fixed blocks",
                    date(2026, 10, 21), "Re-drill — OS and C++", "lesson")
        note = redrill.add_result("", item, redrill.MISSED, TODAY)
        note = redrill.add_result(note, Item("Three distinct UB kinds", None, "t"), redrill.SURE, TODAY)
        assert note == ("---\ncreated: 2026-11-15\ntags: [lesson]\nsummary:\n---\n#Inbox\n\n"
                        "## Gaps\n"
                        "- deque is a linked list (committed) — deque = array of pointers to fixed blocks"
                        " — due 2026-11-19\n"
                        "## Retired\n- Three distinct UB kinds\n")
        assert redrill.note_name(TODAY) == "2026-11-15 Re-drill (phone).md"
        assert redrill.tally(note, TODAY) == (1, 0, 1)

    def round_trip(self, grade):
        due = redrill.collect(LESSONS, QUIZ, TODAY)
        item = next(it for it in due if "deque" in it.text)
        note = redrill.add_result("", item, grade, TODAY)
        lessons = LESSONS + [(redrill.note_name(TODAY), note)]
        return item, lessons, len(due)

    def test_missed_round_trip(self):
        item, lessons, n = self.round_trip(redrill.MISSED)
        now = redrill.collect(lessons, QUIZ, TODAY)
        assert len(now) == n - 1 and item.key not in {it.key for it in now}
        later = [it for it in redrill.collect(lessons, QUIZ, date(2026, 11, 19)) if it.key == item.key]
        assert [it.due for it in later] == [date(2026, 11, 19)]
        assert later[0].topic == "Re-drill — OS and C++"  # topic carried over from the original

    def test_unsure_round_trip(self):
        item, lessons, _ = self.round_trip(redrill.UNSURE)
        assert item.key not in {it.key for it in redrill.collect(lessons, QUIZ, date(2026, 11, 24))}
        assert item.key in {it.key for it in redrill.collect(lessons, QUIZ, date(2026, 11, 25))}

    def test_sure_round_trip(self):
        item, lessons, _ = self.round_trip(redrill.SURE)
        assert item.key not in {it.key for it in redrill.collect(lessons, QUIZ, date(2027, 1, 1))}

    def test_quiz_item_round_trip(self):
        due = redrill.collect(LESSONS, QUIZ, TODAY)
        item = next(it for it in due if it.source == "quiz")
        note = redrill.add_result("", item, redrill.MISSED, TODAY)
        lessons = LESSONS + [(redrill.note_name(TODAY), note)]
        assert item.key not in {it.key for it in redrill.collect(lessons, QUIZ, TODAY)}
        assert item.key in {it.key for it in redrill.collect(lessons, QUIZ, date(2026, 11, 19))}


# ── questions ───────────────────────────────────────────────────────────────────

class FakeAgent:
    def __init__(self, fail=False):
        self.calls, self.fail = [], fail

    def make(self, text):
        self.calls.append(text)
        if self.fail:
            raise RuntimeError("offline")
        return {"question": f"Q about {text[:10]}?", "answer": f"A: {text[:10]}"}


class TestQuestions:
    def test_one_call_per_item_ever(self, tmp_path):
        agent, item = FakeAgent(), Item("deque is a linked list — blocks", None, "t")
        first = redrill.cached_qa(item, agent, tmp_path)
        assert redrill.cached_qa(item, agent, tmp_path) == first
        assert len(agent.calls) == 1 and (tmp_path / "questions.json").exists()

    def test_failure_falls_back_and_is_not_cached(self, tmp_path):
        agent, item = FakeAgent(fail=True), Item("**SSO** limit — 15 chars", None, "t")
        qa = redrill.cached_qa(item, agent, tmp_path)
        assert qa == {"question": "What's the correct version of: SSO limit?", "answer": "SSO limit — 15 chars"}
        redrill.cached_qa(item, agent, tmp_path)
        assert len(agent.calls) == 2 and not (tmp_path / "questions.json").exists()

    def test_agent_parses_json_and_grounds_prompt(self):
        agent = RedrillQuestionAgent.__new__(RedrillQuestionAgent)
        seen = []
        agent._call = lambda prompt: seen.append(prompt) or '```json\n{"question": "Why?", "answer": "Because."}\n```'
        assert agent.make("miss — fix") == {"question": "Why?", "answer": "Because."}
        assert "miss — fix" in seen[0] and "ONLY" in seen[0]
        agent._call = lambda prompt: "no json here"
        assert agent.make("x") is None


# ── Telegram flow ───────────────────────────────────────────────────────────────

class FakeTelegram:
    """Records cards; wait_for_callback replays the taps queued in .taps."""

    def __init__(self):
        self.sent, self.edits, self.notes, self.taps, self.flags = [], [], [], [], {}

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

    def get_flag(self, name):
        return self.flags.get(name)

    def set_flag(self, name, value):
        self.flags[name] = value


def make(tmp_path, lessons=LESSONS, quiz=QUIZ):
    vault = tmp_path / "vault"
    for name, body in lessons:
        (vault / redrill.LESSONS).mkdir(parents=True, exist_ok=True)
        (vault / redrill.LESSONS / name).write_text(body)
    (vault / redrill.QUIZ_LOG).parent.mkdir(parents=True, exist_ok=True)
    (vault / redrill.QUIZ_LOG).write_text(quiz)
    p = Processor.__new__(Processor)  # skip __init__: no config/AI needed
    p.vault_writer = VaultWriter(vault_path=vault)
    p.telegram = FakeTelegram()
    p.redrill_agent = FakeAgent()
    p.REDRILL_CACHE = tmp_path / "cache"
    p.config = SimpleNamespace(telegram=SimpleNamespace(allowed_chat_ids=[1], send_notifications=False))
    return p


def tap(card, label):
    return next(b["callback_data"] for row in card[1] for b in row if label in b["text"])


# Two undated items: small enough to drill to the end.
SMALL = [("2026-10-13 C++ Foundations.md", CPP)]


class TestDrillFlow:
    def msg(self, text="/drill", data=None):
        return TelegramMessage(1, 99, 1, text, 0, callback_data=data)

    def results(self, p):
        return p.vault_writer.read_redrill_results(date.today())

    def test_full_flow(self, tmp_path):
        p = make(tmp_path, SMALL, "")
        tg = p.telegram
        assert parse_message("/drill").command_type == "drill"
        p.process_message(self.msg())
        card = tg.sent[0]
        assert card[0].startswith("*Re-drill* 1/2 · C++ Foundations") and "Q about Coroutine" in card[0]
        assert [[b["text"] for b in row] for row in card[1]] == [["Show answer"]]
        assert all(len(b["callback_data"].encode()) <= 64 for row in card[1] for b in row)

        tg.taps = [tap(card, "Show answer")]
        p._wait_for_taps(1, 99)
        card = tg.edits[-1]
        assert "*Answer:* A: Coroutine" in card[0]
        assert [b["text"] for row in card[1] for b in row] == ["✗ Missed", "✓ Unsure (1–2)", "✓ Sure (3–4)"]

        tg.taps = [tap(card, "Missed")]
        p._wait_for_taps(1, 99)
        note = self.results(p)
        back = date.today() + timedelta(days=4)
        assert f"- Coroutine locals live on the stack while suspended (missed at confidence 4): suspending " \
               f"*returns* to the caller and pops the stack frame. — due {back.isoformat()}" in note
        card = tg.edits[-1]
        assert "*Re-drill* 2/2" in card[0] and "Q about \\`-lutil ma" in card[0] and "_Missed, back" in card[0]

        # The wait has ended. A late tap comes in with the next run, as a callback message.
        p.process_message(self.msg(data=tap(card, "Show answer")))
        card = tg.edits[-1]
        assert "*Answer:*" in card[0]
        p.process_message(self.msg(data=tap(card, "Sure")))
        assert tg.edits[-1] == ("_Retired_\n\nRe-drill done: 1 missed, 0 unsure, 1 retired.", [])
        assert "## Retired\n- `-lutil main.o` links" in self.results(p)
        assert p.redrill_agent.calls and len(p.redrill_agent.calls) == 2  # one call per item

        # Nothing left: /drill says so, and a stale tap changes nothing.
        before = self.results(p)
        p._handle_tap(1, 99, tap(card, "Sure"))
        assert self.results(p) == before
        p.process_message(self.msg())
        assert tg.notes[-1] == "Re-drill done: 1 missed, 0 unsure, 1 retired."

    def test_unsure_goes_to_gaps_plus_10(self, tmp_path):
        p = make(tmp_path, SMALL, "")
        p.process_message(self.msg())
        k = redrill.collect(SMALL, "", date.today())[0].key
        p._handle_tap(1, 99, f"rd:g:{k}:u")
        assert f"— due {(date.today() + timedelta(days=10)).isoformat()}" in self.results(p)

    def test_todo_taps_still_route_to_todos(self, tmp_path, monkeypatch):
        p = make(tmp_path, SMALL, "")
        seen = []
        monkeypatch.setattr(p, "_handle_todo_tap", lambda c, m, d: seen.append(d) or True)
        p.process_message(self.msg(data="tg:k:0:iabc"))
        p.process_message(self.msg(data="dn:iabc"))
        assert seen == ["tg:k:0:iabc", "dn:iabc"] and not p.telegram.edits

    def test_nothing_due(self, tmp_path):
        p = make(tmp_path, [], "")
        p.process_message(self.msg())
        assert p.telegram.notes == ["Nothing due to re-drill."] and not p.telegram.sent

    def test_help_lists_drill(self):
        assert "/drill" in Processor.HELP_TEXT


class TestNudge:
    def test_once_a_day_from_19(self, tmp_path):
        p = make(tmp_path, SMALL, "")
        tg = p.telegram
        p._maybe_redrill_nudge(datetime(2026, 11, 15, 18, 59))
        assert tg.notes == []
        p._maybe_redrill_nudge(datetime(2026, 11, 15, 19, 0))
        assert tg.notes == ["2 re-drills due — /drill"] and tg.flags["redrill_nudge"] == "2026-11-15"
        p._maybe_redrill_nudge(datetime(2026, 11, 15, 21, 0))
        assert len(tg.notes) == 1
        p._maybe_redrill_nudge(datetime(2026, 11, 16, 19, 5))
        assert len(tg.notes) == 2

    def test_silent_when_nothing_due(self, tmp_path):
        p = make(tmp_path, [], "")
        p._maybe_redrill_nudge(datetime(2026, 11, 15, 20, 0))
        assert p.telegram.notes == [] and "redrill_nudge" not in p.telegram.flags
