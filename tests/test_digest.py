"""Weekly / monthly digest: gathering, grounding, no-overwrite, once-per-period triggers."""
from datetime import date, datetime
from types import SimpleNamespace

import pytest

from src import digest
from src.agents.digest_agent import DigestAgent, _parse_object
from src.agents.vault_writer import VaultWriter
from src.commands import parse_message
from src.processor import Processor

MON = date(2026, 11, 9)  # ISO week 2026-W46 runs Mon 9 to Sun 15 Nov

DAILY_TEMPLATE_ONLY = """---
date: 2026-11-10T09:00
tags:
  - Daily
---
# DAILY NOTE
## Tuesday, November 10th, 2026
***
### Journal

***
### Notes

***
"""

DAILY_WITH_LINES = """---
date: 2026-11-15T17:14
tags:
  - Daily
---
# DAILY NOTE
## Sunday, November 15th, 2026
***
### Notes
- 16:05 Track things to shop for
- 16:10 I will sell items on vinted
"""


def write(vault, rel, body):
    path = vault / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


def source(created, title="S", summary="a summary"):
    return f"---\ntitle: {title}\ncreated: {created}\nsummary: {summary}\n---\n> [!note]+ Props\n\nBody text that must not be sent.\n"


@pytest.fixture
def vault(tmp_path):
    write(tmp_path, "06 Daily Notes/2026-11-08.md", DAILY_WITH_LINES)   # Sunday before: out of window
    write(tmp_path, "06 Daily Notes/2026-11-10.md", DAILY_TEMPLATE_ONLY)  # in window but empty
    write(tmp_path, "06 Daily Notes/2026-11-15.md", DAILY_WITH_LINES)   # in window
    write(tmp_path, "06 Daily Notes/2026-11-16.md", DAILY_WITH_LINES)   # Monday after: out
    write(tmp_path, "02 Sources/Articles/In Week.md", source("2026-11-11", "In Week"))
    write(tmp_path, "02 Sources/Videos/Odd Format.md", source("2026-11-15:21-04", "Odd Format"))
    write(tmp_path, "02 Sources/Articles/Before.md", source("2026-11-08", "Before"))
    write(tmp_path, "02 Sources/Articles/No Date.md", "---\ntitle: No Date\n---\nbody\n")
    write(tmp_path, "05 Base Notes/Sleep Baseline.md",
          "---\ntitle: Sleep Baseline\nsummary: I sleep best at 11pm.\nconfidence: decaying\ntags:\n  - personal\n---\nlong body\n")
    write(tmp_path, "05 Base Notes/Raft.md", "---\ntitle: Raft\nsummary: consensus\ntags:\n  - distributed-systems\n---\n")
    return tmp_path


LESSON = """---
created: 2026-11-12
tags: [lesson]
summary:
---
#Inbox

> [!abstract] CLAUDE
> ## Session
> - Virtual memory · lesson · nodes 5/6 · misses 3 · 48 min
> ## Calibration
> - conf 3–4: right 7/9 · conf 1–2: right 3/4 · don't know: 1
> ## Gaps
> - PTE vs page (structural) — a PTE is a pointer — due 2026-11-16
"""

PHONE_NOTE = """---
created: 2026-11-14
tags: [lesson]
summary:
---
#Inbox

## Gaps
- SSO limit — 15 chars — due 2026-11-18
- operator[] — inserts — due 2026-11-18
## Retired
- UB kinds — back 2026-11-24
"""


# ── Gathering ──────────────────────────────────────────────────────────────────

class TestLearning:
    def test_counts_sessions_phone_tallies_and_calibration(self, vault):
        write(vault, "02 Sources/Lessons/2026-11-12 Virtual Memory.md", LESSON)
        write(vault, "02 Sources/Lessons/2026-11-14 Re-drill (phone).md", PHONE_NOTE)
        write(vault, "02 Sources/Lessons/2026-11-05 Old.md", LESSON.replace("2026-11-12", "2026-11-05"))
        learning = digest.gather_week(vault, MON).learning
        assert learning == [
            "Virtual memory · lesson · nodes 5/6 · misses 3 · 48 min [[2026-11-12 Virtual Memory]]",
            "Phone re-drill 2026-11-14: 2 missed · 0 unsure · 1 retired [[2026-11-14 Re-drill (phone)]]",
            "Calibration: conf 3–4 right 7/9 · conf 1–2 right 3/4 → 2 confident misses",
        ]
        md = digest.render(digest.gather_week(vault, MON), {})
        assert "## Learning" in md and "- Phone re-drill 2026-11-14" in md

    def test_no_lessons_folder(self, vault):
        assert digest.gather_week(vault, MON).learning == []


class TestGather:
    def test_week_window_and_empty_template_notes(self, vault):
        inputs = digest.gather_week(vault, MON)
        assert inputs.label == "2026-W46"
        assert [n.name for n in inputs.daily] == ["2026-11-15"]  # 08/16 outside, 10 template-only
        assert "vinted" in inputs.daily[0].text
        assert "tags:" not in inputs.daily[0].text  # frontmatter stripped

    def test_sources_filtered_by_created_any_format(self, vault):
        names = {n.name for n in digest.gather_week(vault, MON).sources}
        assert names == {"In Week", "Odd Format"}

    def test_sources_carry_summary_not_body(self, vault):
        s = next(n for n in digest.gather_week(vault, MON).sources if n.name == "In Week")
        assert "a summary" in s.text and "must not be sent" not in s.text

    def test_only_personal_facts_loaded(self, vault):
        facts = digest.gather_week(vault, MON).facts
        assert [f.name for f in facts] == ["Sleep Baseline"]
        assert "11pm" in facts[0].text and "long body" not in facts[0].text
        assert "decaying" in facts[0].text

    def test_until_cuts_a_midweek_run(self, vault):
        inputs = digest.gather_week(vault, MON, until=date(2026, 11, 12))
        assert inputs.daily == [] and [s.name for s in inputs.sources] == ["In Week"]

    def test_empty_week(self, tmp_path):
        inputs = digest.gather_week(tmp_path, MON)
        assert inputs.is_empty() and inputs.facts == []

    def test_iso_week_label_uses_iso_year(self):
        assert digest.week_label(date(2026, 12, 28)) == "2026-W53"
        assert digest.week_label(date(2024, 12, 30)) == "2025-W01"

    def test_scheduled_window(self):
        assert digest.scheduled_week(datetime(2026, 11, 15, 17, 59)) is None
        assert digest.scheduled_week(datetime(2026, 11, 15, 18, 0)) == MON
        assert digest.scheduled_week(datetime(2026, 11, 16, 9, 0)) == MON  # Monday catch-up: last week
        assert digest.scheduled_week(datetime(2026, 11, 17, 9, 0)) is None

    def test_weeks_of_month_are_weeks_ending_in_it(self):
        assert digest.weeks_of_month(2026, 11) == [date(2026, 10, 26), date(2026, 11, 2), date(2026, 11, 9),
                                                   date(2026, 11, 16), date(2026, 11, 23)]


# ── Prompt and grounding ───────────────────────────────────────────────────────

class TestPrompt:
    def test_grounding_instructions_present(self, vault):
        prompt = digest.build_prompt(digest.gather_week(vault, MON))
        assert digest.GROUNDING_RULES in prompt
        assert "MUST cite" in prompt and "empty list" in prompt
        assert "[[2026-11-15]]" in prompt and "[[In Week]]" in prompt and "[[Sleep Baseline]]" in prompt
        assert "must not be sent" not in prompt

    def test_uncited_and_wrongly_cited_items_dropped(self, vault):
        inputs = digest.gather_week(vault, MON)
        draft = {"patterns": ["shopping on the mind [[2026-11-15]]", "invented trend", "fake [[2026-01-01]]"],
                 "commitments": ["sell on vinted [[2026-11-15]]"], "anomalies": "not a list"}
        sections, dropped = digest.filter_cited(draft, inputs.citable())
        assert sections["patterns"] == ["shopping on the mind [[2026-11-15]]"]
        assert sections["anomalies"] == [] and dropped == 2
        assert digest.highlights(sections) == ["shopping on the mind [[2026-11-15]]", "sell on vinted [[2026-11-15]]"]

    def test_render_matches_template_and_says_none(self, vault):
        inputs = digest.gather_week(vault, MON)
        md = digest.render(inputs, {"patterns": ["x [[2026-11-15]]"]}, dropped=1, prior="2026-W45",
                           now=datetime(2026, 11, 15, 18, 5))
        for heading in ("## Patterns", "## Anomalies", "## Commitments made",
                        "## Contradictions with prior personal facts", "## Candidate Base Notes",
                        "## Open threads", "## Log"):
            assert heading in md
        assert "type: digest" in md and "period: weekly" in md and "visibility: private" in md
        assert "created: 2026-11-15:18-05" in md
        assert "**Range:** 2026-11-09 → 2026-11-15" in md
        assert md.count("- none this week") == 6 and "## Learning" in md
        assert "- Daily Notes scanned: 1 — [[2026-11-15]]" in md
        assert "- Prior digest: [[2026-W45]]" in md and "Uncited items dropped: 1" in md

    def test_parse_object_fenced_and_bare(self):
        assert _parse_object('```json\n{"patterns": []}\n```') == {"patterns": []}
        assert _parse_object('Sure: {"a": 1} done') == {"a": 1}
        assert _parse_object("no json") is None


# ── Writing ────────────────────────────────────────────────────────────────────

def test_write_digest_never_overwrites(tmp_path):
    w = VaultWriter(vault_path=tmp_path)
    assert w.write_digest("2026-W46", "first") == tmp_path / "04 Indexes/Digests/2026-W46.md"
    assert w.write_digest("2026-W46", "second") is None
    assert (tmp_path / "04 Indexes/Digests/2026-W46.md").read_text() == "first"


# ── Processor: triggers, LLM skipping, /digest ────────────────────────────────

class FakeTelegram:
    def __init__(self):
        self.flags, self.notes = {}, []

    def get_flag(self, name):
        return self.flags.get(name)

    def set_flag(self, name, value):
        self.flags[name] = value

    def send_notification(self, chat_id, text):
        self.notes.append(text)
        return True


class FakeAgent:
    def __init__(self, response):
        self.response, self.prompts = response, []

    def draft(self, prompt):
        self.prompts.append(prompt)
        return self.response


def make(vault, response=None):
    p = Processor.__new__(Processor)  # skip __init__: no real config, AI or Telegram
    p.config = SimpleNamespace(vault_path=vault, telegram=SimpleNamespace(allowed_chat_ids=[1]),
                               vault_writer=SimpleNamespace(daily_notes_folder="06 Daily Notes"))
    p.vault_writer = VaultWriter(vault_path=vault)
    p.telegram = FakeTelegram()
    p.agent = FakeAgent(response if response is not None else {
        "patterns": ["shopping [[2026-11-15]]"], "commitments": ["sell on vinted [[2026-11-15]]"]})
    p._digest_agent = lambda: p.agent
    return p


def at(monkeypatch, when):
    class FakeDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return when

    class FakeDate(date):
        @classmethod
        def today(cls):
            return when.date()

    monkeypatch.setattr("datetime.datetime", FakeDatetime)
    monkeypatch.setattr("datetime.date", FakeDate)


class TestWeeklyTrigger:
    def test_writes_once_per_week_and_notifies(self, vault, monkeypatch):
        p = make(vault)
        at(monkeypatch, datetime(2026, 11, 15, 18, 30))
        p._maybe_weekly_digest()
        path = vault / "04 Indexes/Digests/2026-W46.md"
        assert path.exists() and "shopping [[2026-11-15]]" in path.read_text()
        assert p.telegram.flags["digest_week"] == "2026-W46"
        assert p.telegram.notes[0].startswith("Weekly digest written: 2026-W46.md")
        assert "• shopping 2026-11-15" in p.telegram.notes[0]
        # Monday catch-up for the same week: flag already set, nothing happens.
        at(monkeypatch, datetime(2026, 11, 16, 8, 0))
        p._maybe_weekly_digest()
        assert len(p.agent.prompts) == 1 and len(p.telegram.notes) == 1

    def test_flag_set_before_acting(self, vault, monkeypatch):
        p = make(vault)
        at(monkeypatch, datetime(2026, 11, 15, 19, 0))

        def boom():
            raise RuntimeError("provider down")
        p._digest_agent = boom
        with pytest.raises(RuntimeError):
            p._maybe_weekly_digest()
        assert p.telegram.flags["digest_week"] == "2026-W46"  # a crash must not retry every 5 minutes

    def test_outside_window_does_nothing(self, vault, monkeypatch):
        p = make(vault)
        at(monkeypatch, datetime(2026, 11, 15, 12, 0))
        p._maybe_weekly_digest()
        assert p.telegram.flags == {} and p.agent.prompts == []

    def test_empty_week_skips_llm_and_stays_quiet(self, tmp_path, monkeypatch):
        p = make(tmp_path)
        at(monkeypatch, datetime(2026, 11, 15, 18, 30))
        p._maybe_weekly_digest()
        assert p.agent.prompts == [] and p.telegram.notes == []
        assert not (tmp_path / "04 Indexes/Digests").exists()

    def test_existing_digest_skips_llm_and_is_kept(self, vault, monkeypatch):
        p = make(vault)
        write(vault, "04 Indexes/Digests/2026-W46.md", "user-corrected draft")
        at(monkeypatch, datetime(2026, 11, 15, 18, 30))
        p._maybe_weekly_digest()
        assert p.agent.prompts == []
        assert (vault / "04 Indexes/Digests/2026-W46.md").read_text() == "user-corrected draft"

    def test_daily_notes_untouched(self, vault, monkeypatch):
        before = (vault / "06 Daily Notes/2026-11-15.md").read_text()
        p = make(vault)
        at(monkeypatch, datetime(2026, 11, 15, 18, 30))
        p._maybe_weekly_digest()
        assert (vault / "06 Daily Notes/2026-11-15.md").read_text() == before


class TestMonthly:
    def test_zero_weeklies_skips_silently(self, vault, monkeypatch):
        p = make(vault)
        at(monkeypatch, datetime(2026, 12, 1, 9, 0))
        p._maybe_monthly_digest()
        assert p.telegram.flags["digest_month"] == "2026-11"
        assert p.agent.prompts == [] and p.telegram.notes == []
        assert not (vault / "04 Indexes/Digests/2026-11.md").exists()

    def test_synthesises_prior_months_weeklies_once(self, vault, monkeypatch):
        write(vault, "04 Indexes/Digests/2026-W46.md", "---\ntitle: 2026-W46\n---\n## Patterns\n- shopping [[2026-11-15]]\n")
        write(vault, "04 Indexes/Digests/2026-W49.md", "---\ntitle: 2026-W49\n---\nDecember week, not November\n")
        p = make(vault, {"patterns": ["shopping recurs [[2026-W46]]"], "commitments": ["vinted [[2026-11-15]]"]})
        at(monkeypatch, datetime(2026, 12, 3, 9, 0))  # first run after the 1st
        p._maybe_monthly_digest()
        md = (vault / "04 Indexes/Digests/2026-11.md").read_text()
        assert "period: monthly" in md and "shopping recurs [[2026-W46]]" in md
        assert "vinted [[2026-11-15]]" in md  # daily notes cited by a weekly are citable
        assert "- none this month" in md
        assert "December week" not in p.agent.prompts[0]
        assert "Monthly digest written: 2026-11.md" in p.telegram.notes[0]
        p._maybe_monthly_digest()
        assert len(p.agent.prompts) == 1


class TestDigestCommand:
    def test_parse(self):
        assert parse_message("/digest").command_type == "digest"
        assert parse_message("/Digest now").command_type == "digest"

    def test_on_demand_writes_this_week_so_far(self, vault, monkeypatch):
        p = make(vault)
        at(monkeypatch, datetime(2026, 11, 15, 10, 0))
        result = p._process_digest(None, None)
        assert result.success and result.message.startswith("Weekly digest written: 2026-W46.md")
        again = p._process_digest(None, None)
        assert "already exists" in again.message and len(p.agent.prompts) == 1

    def test_mid_week_is_a_preview_so_sundays_full_digest_still_gets_written(self, vault, monkeypatch):
        p = make(vault)
        at(monkeypatch, datetime(2026, 11, 11, 10, 0))  # Wednesday
        result = p._process_digest(None, None)
        assert result.success and "preview, not saved" in result.message
        assert not p.vault_writer.digest_path("2026-W46").exists()

    def test_on_demand_empty_week(self, tmp_path, monkeypatch):
        p = make(tmp_path)
        at(monkeypatch, datetime(2026, 11, 15, 10, 0))
        result = p._process_digest(None, None)
        assert result.success and "Nothing to digest" in result.message and p.agent.prompts == []

    def test_model_failure_is_an_error(self, vault, monkeypatch):
        p = make(vault)
        p.agent.response = None
        p._digest_agent = lambda: SimpleNamespace(draft=lambda prompt: None)
        at(monkeypatch, datetime(2026, 11, 15, 10, 0))
        assert not p._process_digest(None, None).success

    def test_in_help(self):
        assert "/digest" in Processor.HELP_TEXT


def test_digest_agent_is_a_base_agent():
    from src.agents.base import BaseAgent
    assert issubclass(DigestAgent, BaseAgent)


def test_run_does_digests_before_triage(monkeypatch):
    p = Processor.__new__(Processor)
    order = []
    p.telegram = SimpleNamespace(get_pending_messages=lambda: [])
    p._maybe_weekly_digest = lambda: order.append("weekly")
    p._maybe_monthly_digest = lambda: (_ for _ in ()).throw(RuntimeError("x"))  # a failure must not stop triage
    p._maybe_weekly_triage = lambda: order.append("triage")
    assert p.run() == []
    assert order == ["weekly", "triage"]
