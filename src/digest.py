"""Weekly and monthly digests: the vault's "Personal Tracking" layer (Nordorn CLAUDE.md).

Pure where possible: this module reads the vault and builds strings. It never writes. The
LLM call lives in agents/digest_agent.py and the write in VaultWriter.write_digest.

Weeks are ISO 8601 weeks (Monday to Sunday, numbered with date.isocalendar(), so the week
of 2026-12-31 can belong to 2027). They are labelled "2026-W46". The vault spec says
`<YYYY-WW>`, but a bare "2026-11" for ISO week 11 would collide with the November monthly
digest "2026-11" in the same folder, so the label keeps the ISO "W".

Grounding: the model may say only what it can cite. Every item must carry a [[wikilink]] to
a note it was shown, and items that cite nothing they were shown are dropped in
filter_cited(). An empty week makes no LLM call at all.
"""
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

import yaml

from . import redrill

DIGESTS_FOLDER = "04 Indexes/Digests"
SOURCES_FOLDER = "02 Sources"
BASE_NOTES_FOLDER = "05 Base Notes"

# JSON key -> (template heading, italic guidance line from 99 Templates/Digest.md)
SECTIONS = [
    ("patterns", "Patterns",
     "Recurring themes across the period. 3+ mentions or strong single signal."),
    ("anomalies", "Anomalies",
     "Things that broke the pattern — outliers worth a second look."),
    ("commitments", "Commitments made",
     '"I will / I want to / I\'ll try" statements from Daily Notes. Track follow-through next digest.'),
    ("contradictions", "Contradictions with prior personal facts",
     "Where this period's signal disagrees with `tag: personal` Base Notes. Trust the present; flag the fact for update."),
    ("candidate_base_notes", "Candidate Base Notes",
     "Topics that have accumulated enough material to promote. Format: title — what to collate — Curate or Draft mode suggestion."),
    ("open_threads", "Open threads",
     "Questions raised but unanswered, sources flagged but unread, ideas dropped mid-thought."),
]

_DAILY_CLIP = 4000
_SUMMARY_CLIP = 400
_WEEKLY_CLIP = 6000


@dataclass
class Note:
    name: str     # file stem: what a [[wikilink]] to it says
    text: str     # what the model is shown: a cleaned daily note, or title + summary


@dataclass
class DigestInputs:
    label: str                       # "2026-W46" or "2026-11"
    period: str                      # "weekly" or "monthly"
    start: date
    end: date
    daily: list[Note] = field(default_factory=list)
    sources: list[Note] = field(default_factory=list)
    weeklies: list[Note] = field(default_factory=list)   # monthly only
    facts: list[Note] = field(default_factory=list)      # tag: personal Base Notes
    learning: list[str] = field(default_factory=list)    # counted from lesson notes, never drafted

    def is_empty(self) -> bool:
        """Nothing to digest. Personal facts alone are not evidence of anything this period."""
        return not (self.daily or self.sources or self.weeklies)

    def citable(self) -> set[str]:
        names = {n.name for n in self.daily + self.sources + self.weeklies + self.facts}
        for w in self.weeklies:  # a monthly may cite the daily notes its weeklies cited
            names.update(wikilinks(w.text))
        return {n.lower() for n in names}


# ── Weeks and months ───────────────────────────────────────────────────────────

def week_monday(d: date) -> date:
    """Monday of d's ISO week."""
    return d - timedelta(days=d.weekday())


def week_label(monday: date) -> str:
    """ISO 8601 week label, e.g. 2026-W46. isocalendar() year, not calendar year."""
    iso = monday.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def scheduled_week(now: datetime) -> date | None:
    """Monday of the week the scheduled digest covers, or None outside the window.

    Same window as the weekly triage: Sunday from 18:00 digests the week ending today;
    Monday (laptop was off on Sunday) digests the week that ended yesterday.
    """
    if now.weekday() == 6 and now.hour >= 18:
        return week_monday(now.date())
    if now.weekday() == 0:
        return now.date() - timedelta(days=7)
    return None


def prior_month(today: date) -> tuple[int, int]:
    first = today.replace(day=1) - timedelta(days=1)
    return first.year, first.month


def month_label(year: int, month: int) -> str:
    return f"{year}-{month:02d}"


def weeks_of_month(year: int, month: int) -> list[date]:
    """Mondays of the ISO weeks that END (Sunday) in this month.

    Ending, not mostly-in: every such week's Sunday-18:00 digest is written by the last day of
    the month, so a monthly run on the 1st never misses its last week.
    """
    d = date(year, month, 1)
    sunday = d + timedelta(days=(6 - d.weekday()))
    out = []
    while sunday.month == month:
        out.append(sunday - timedelta(days=6))
        sunday += timedelta(days=7)
    return out


# ── Reading the vault ──────────────────────────────────────────────────────────

def split_frontmatter(text: str) -> tuple[dict, str]:
    """(frontmatter dict, body). Unparseable YAML gives {} rather than an exception."""
    if not text.startswith("---"):
        return {}, text
    parts = text.split("\n---", 1)
    if len(parts) < 2:
        return {}, text
    head, body = parts[0][3:], parts[1].split("\n", 1)[1] if "\n" in parts[1] else ""
    try:
        meta = yaml.safe_load(head) or {}
    except yaml.YAMLError:
        meta = {}
        m = re.search(r"^created:\s*(\S+)", head, re.MULTILINE)
        if m:
            meta["created"] = m.group(1)
    return (meta if isinstance(meta, dict) else {}), body


def created_date(meta: dict) -> date | None:
    """`created:` in any of the vault's shapes: 2026-07-30, 2026-05-07:00-28, 2026-05-07 12:30, a date."""
    value = meta.get("created")
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    m = re.match(r"(\d{4}-\d{2}-\d{2})", str(value or ""))
    if not m:
        return None
    try:
        return date.fromisoformat(m.group(1))
    except ValueError:
        return None


def clean_daily(body: str) -> str:
    """A daily note's body without blank lines and separators. "" when only template headings remain."""
    lines = [ln.rstrip() for ln in body.splitlines()]
    lines = [ln for ln in lines if ln.strip() and ln.strip() not in ("***", "---")]
    if not any(not ln.lstrip().startswith("#") for ln in lines):
        return ""
    return "\n".join(lines)


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _summary(meta: dict, body: str) -> str:
    summary = meta.get("summary")
    if isinstance(summary, str) and summary.strip():
        return _clip(" ".join(summary.split()), _SUMMARY_CLIP)
    # No summary: the first prose lines, past the Properties callout.
    prose = [ln.strip() for ln in body.splitlines()
             if ln.strip() and not ln.lstrip().startswith((">", "#", "---", "***", "!["))]
    return _clip(" ".join(" ".join(prose[:3]).split()), _SUMMARY_CLIP)


def gather_daily(vault: Path, start: date, end: date, folder: str = "06 Daily Notes") -> list[Note]:
    out = []
    d = start
    while d <= end:
        path = vault / folder / f"{d.isoformat()}.md"
        if path.exists():
            _, body = split_frontmatter(path.read_text(encoding="utf-8"))
            cleaned = clean_daily(body)
            if cleaned:
                out.append(Note(d.isoformat(), _clip(cleaned, _DAILY_CLIP)))
        d += timedelta(days=1)
    return out


def gather_sources(vault: Path, start: date, end: date) -> list[Note]:
    """Source notes whose frontmatter `created:` falls in [start, end]. Title + summary, not bodies."""
    out = []
    for path in sorted((vault / SOURCES_FOLDER).rglob("*.md")):
        try:
            meta, body = split_frontmatter(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError):
            continue
        created = created_date(meta)
        if created is None or not start <= created <= end:
            continue
        title = str(meta.get("title") or path.stem)
        out.append(Note(path.stem, f"{title}: {_summary(meta, body)}".rstrip(": ")))
    return out


def gather_personal_facts(vault: Path) -> list[Note]:
    """`tag: personal` Base Notes, title + summary (+ confidence) only, to keep the prompt small."""
    out = []
    for path in sorted((vault / BASE_NOTES_FOLDER).glob("*.md")):
        try:
            meta, body = split_frontmatter(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError):
            continue
        tags = meta.get("tags") or []
        tags = [tags] if isinstance(tags, str) else tags
        if "personal" not in {str(t).strip().lstrip("#").lower() for t in tags}:
            continue
        conf = f" (confidence: {meta['confidence']})" if meta.get("confidence") else ""
        out.append(Note(path.stem, f"{_summary(meta, body)}{conf}"))
    return out


def _section_lines(content: str, heading: str) -> list[str]:
    """`- ` items under `## <heading>` in a lesson note, through the md-log hook's `> ` quoting."""
    out, inside = [], False
    for raw in content.splitlines():
        line = re.sub(r"^\s*(?:>\s?)*", "", raw).rstrip()
        if re.fullmatch(rf"##\s+{heading}", line):
            inside = True
        elif inside and line.startswith("- "):
            out.append(line[2:].strip())
        else:
            inside = False
    return out


def gather_learning(vault: Path, start: date, end: date) -> list[str]:
    """The period's learning, counted rather than drafted (no LLM): each lesson's `## Session`
    line, each phone re-drill's tally, and the summed `## Calibration` fractions (teach skill,
    Close). Lesson notes are dated by filename or `created:`."""
    out, hi, lo = [], [0, 0], [0, 0]
    folder = Path(vault) / redrill.LESSONS
    for path in sorted(folder.glob("*.md")) if folder.is_dir() else []:
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        when = redrill.lesson_date(path.name, content)
        if when is None or not start <= when <= end:
            continue
        link = f"[[{path.stem}]]"
        if redrill.PHONE in path.name:
            m, u, r = redrill.tally(content, when)
            if m + u + r:
                out.append(f"Phone re-drill {when.isoformat()}: {m} missed · {u} unsure · {r} retired {link}")
            continue
        out += [f"{line} {link}" for line in _section_lines(content, "Session")]
        for line in _section_lines(content, "Calibration"):
            fr = re.findall(r"(\d+)/(\d+)", line)[:2]
            for band, (a, b) in zip((hi, lo), fr):
                band[0] += int(a)
                band[1] += int(b)
    if hi[1] or lo[1]:
        text = f"Calibration: conf 3–4 right {hi[0]}/{hi[1]} · conf 1–2 right {lo[0]}/{lo[1]}"
        if hi[1] - hi[0]:
            text += f" → {hi[1] - hi[0]} confident {'miss' if hi[1] - hi[0] == 1 else 'misses'}"
        out.append(text)
    return out


def gather_week(vault: Path, monday: date, daily_folder: str = "06 Daily Notes",
                until: date | None = None) -> DigestInputs:
    """Inputs for the ISO week starting monday, optionally cut off at `until` (an on-demand mid-week run)."""
    end = monday + timedelta(days=6)
    if until is not None:
        end = min(end, until)
    vault = Path(vault)
    inputs = DigestInputs(week_label(monday), "weekly", monday, end,
                          daily=gather_daily(vault, monday, end, daily_folder),
                          sources=gather_sources(vault, monday, end),
                          learning=gather_learning(vault, monday, end))
    if not inputs.is_empty():  # facts only matter when there is something to compare them to
        inputs.facts = gather_personal_facts(vault)
    return inputs


def gather_month(vault: Path, year: int, month: int) -> DigestInputs:
    """The month's weekly digests (weeks ending in it). Empty when none were written."""
    vault = Path(vault)
    mondays = weeks_of_month(year, month)
    weeklies = []
    for monday in mondays:
        path = vault / DIGESTS_FOLDER / f"{week_label(monday)}.md"
        if path.exists():
            _, body = split_frontmatter(path.read_text(encoding="utf-8"))
            weeklies.append(Note(path.stem, _clip(body.strip(), _WEEKLY_CLIP)))
    start = mondays[0] if mondays else date(year, month, 1)
    end = mondays[-1] + timedelta(days=6) if mondays else start
    inputs = DigestInputs(month_label(year, month), "monthly", start, end, weeklies=weeklies,
                          learning=gather_learning(vault, start, end))
    if not inputs.is_empty():
        inputs.facts = gather_personal_facts(vault)
    return inputs


# ── Prompt ─────────────────────────────────────────────────────────────────────

GROUNDING_RULES = """Grounding rules (these override everything else):
- Use ONLY the notes below. Do not add facts, interpretations or advice that the notes do not support.
- Every item MUST cite the note it came from as an Obsidian wikilink using the exact name shown
  in its header, e.g. [[2026-11-15]]. An item with no citation will be deleted.
- If a section has no evidence, return an empty list for it. An empty list is a correct answer;
  an invented item is a wrong one. Do not pad sections to make the digest look complete.
- Quote the user's own words where you can, in "quotes"."""


def build_prompt(inputs: DigestInputs) -> str:
    unit = "week" if inputs.period == "weekly" else "month"
    blocks = []
    if inputs.weeklies:
        blocks.append("## Weekly digests this month\n" + "\n\n".join(
            f"### [[{n.name}]]\n{n.text}" for n in inputs.weeklies))
    if inputs.daily:
        blocks.append("## Daily notes (raw captures, timestamped lines)\n" + "\n\n".join(
            f"### [[{n.name}]]\n{n.text}" for n in inputs.daily))
    if inputs.sources:
        blocks.append("## Sources saved this week (title: summary)\n" + "\n".join(
            f"- [[{n.name}]] {n.text}" for n in inputs.sources))
    if inputs.facts:
        blocks.append("## Prior personal facts (tag: personal Base Notes, title: summary)\n" + "\n".join(
            f"- [[{n.name}]] {n.text}" for n in inputs.facts))
    else:
        blocks.append("## Prior personal facts\n(none recorded yet, so `contradictions` must be empty)")

    task = (
        "Synthesise these weekly digests into one monthly digest: longer horizon, same format. "
        "Patterns that recur across weeks matter most; carry forward commitments and say whether "
        "the later weeks show follow-through."
        if inputs.period == "monthly" else
        "Write a weekly digest of this person's own notes."
    )
    return f"""You are drafting a {inputs.period} personal digest for {inputs.label} \
({inputs.start.isoformat()} to {inputs.end.isoformat()}) in the user's Obsidian vault. \
The user reads and corrects it, so be useful and terse. {task}

{GROUNDING_RULES}

Sections:
- patterns: recurring themes this {unit} (3+ mentions, or one strong signal).
- anomalies: things that broke the pattern.
- commitments: "I will / I want to / I'll try" statements, and plain todo-like lines the user wrote.
- contradictions: where this {unit}'s notes disagree with a prior personal fact. Cite both the fact
  and the note. Also list any personal fact marked "confidence: decaying" as a candidate for refresh.
- candidate_base_notes: topics with enough material to promote, as
  "title — what to collate — Curate or Draft mode", citing the notes.
- open_threads: questions raised but unanswered, ideas dropped mid-thought.

{chr(10).join(blocks)}

Answer with ONLY a JSON object, each value a list of strings:
{{"patterns": [], "anomalies": [], "commitments": [], "contradictions": [],
  "candidate_base_notes": [], "open_threads": []}}"""


# ── Response handling and rendering ────────────────────────────────────────────

def wikilinks(text: str) -> list[str]:
    return [m.strip() for m in re.findall(r"\[\[([^\]|#]+)", text or "")]


def filter_cited(draft: dict, citable: set[str]) -> tuple[dict, int]:
    """Keep items citing at least one note the model was shown. Returns (sections, dropped count)."""
    out, dropped = {}, 0
    for key, _, _ in SECTIONS:
        items = draft.get(key) or []
        kept = []
        for item in items if isinstance(items, list) else []:
            if not isinstance(item, str) or not item.strip():
                continue
            if any(link.lower() in citable for link in wikilinks(item)):
                kept.append(" ".join(item.split()))
            else:
                dropped += 1
        out[key] = kept
    return out, dropped


def highlights(sections: dict, n: int = 3) -> list[str]:
    """The first cited item of each non-empty section, in template order. Taken from the
    filtered sections rather than asked for separately, so a Telegram highlight is never
    an uncited claim."""
    return [items[0] for key, _, _ in SECTIONS if (items := sections.get(key))][:n]


def render(inputs: DigestInputs, sections: dict, dropped: int = 0, prior: str | None = None,
           now: datetime | None = None) -> str:
    """Markdown matching 99 Templates/Digest.md."""
    now = now or datetime.now()
    unit = "week" if inputs.period == "weekly" else "month"
    scanned = (["Weekly digests"] if inputs.weeklies else []) + ["Daily Notes", "new Sources"]
    body = []
    for key, heading, guide in SECTIONS:
        items = sections.get(key) or []
        lines = "\n".join(f"- {i}" for i in items) if items else f"- none this {unit}"
        body.append(f"## {heading}\n*{guide}*\n\n{lines}\n")
    learn = "\n".join(f"- {i}" for i in inputs.learning) if inputs.learning else f"- none this {unit}"
    body.append(f"## Learning\n*Lessons, re-drills and calibration this {unit}. Counted from the lesson notes, not drafted.*\n\n{learn}\n")
    links = lambda notes: ", ".join(f"[[{n.name}]]" for n in notes)
    log = []
    if inputs.weeklies:
        log.append(f"- Weekly digests: {len(inputs.weeklies)} — {links(inputs.weeklies)}")
    log += [f"- Sources counted: {len(inputs.sources)}" + (f" — {links(inputs.sources)}" if inputs.sources else ""),
            f"- Daily Notes scanned: {len(inputs.daily)}" + (f" — {links(inputs.daily)}" if inputs.daily else ""),
            f"- Prior digest: [[{prior}]]" if prior else "- Prior digest: none"]
    if dropped:
        log.append(f"- Uncited items dropped: {dropped}")
    return f"""---
title: {inputs.label}
created: {now:%Y-%m-%d:%H-%M}
type: digest
period: {inputs.period}
visibility: private
tags:
  - digest
  - {inputs.period}
cssclasses:
  - base-notes
---
> [!note]+ **Digest — {inputs.label} ({inputs.period})**
> **Range:** {inputs.start.isoformat()} → {inputs.end.isoformat()}
> **Sources scanned:** {", ".join(scanned)}
> **Author:** agent draft (user reviews)

---
{chr(10).join(body)}
## Log
{chr(10).join(log)}
"""


def prior_label(inputs: DigestInputs) -> str:
    if inputs.period == "weekly":
        return week_label(inputs.start - timedelta(days=7))
    year, month = map(int, inputs.label.split("-"))
    return month_label(*prior_month(date(year, month, 1)))


def plain(text: str, limit: int = 120) -> str:
    """One Telegram line: wikilink brackets dropped, clipped."""
    text = re.sub(r"\[\[([^\]|]+)(?:\|([^\]]+))?\]\]", lambda m: m.group(2) or m.group(1), text)
    return _clip(" ".join(text.split()), limit)
