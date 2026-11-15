"""Spaced re-drills from the teach skill's records. Pure functions on file contents, no I/O
(except cached_qa, which owns the question cache).

Two sources, the same ones `/teach redrill` reads (~/.claude/skills/teach/SKILL.md):
- `## Gaps` / `## Retired` blocks in 02 Sources/Lessons/*.md. The md-log hook mirrors the chat,
  so these lines usually sit inside a `> ` callout: "> - <miss> — <correct> — due YYYY-MM-DD".
  Lines written before 2026-10-17 carry no due date and count as due.
- The Quiz log table in 07 Projects/Interview Prep/Todo.md, "Misses to re-drill" column:
  "due **2026-10-21**: a · b · c. Retired to +10 (2026-10-30): d · e".

Everything is replayed oldest first. A later block that lists an item again re-dates it; a
later `## Retired` drops it. Results from the phone are written as an ordinary lesson note,
so the skill reads them back with no special case.

An item is identified by its *head*: the miss itself, before the " — correct version" part,
with parentheticals like "(3rd miss)" dropped. The same miss gets reworded between sessions
("(2nd miss)" → "(3rd miss)", "TLB-miss cost" → "Cost of a TLB miss…"), so a later block also
overrides an earlier item when one head's significant words are a subset of the other's.
"""
import hashlib
import json
import logging
import re
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

LESSONS = "02 Sources/Lessons"
QUIZ_LOG = "07 Projects/Interview Prep/Todo.md"
PHONE = "Re-drill (phone)"

MISSED, UNSURE, SURE = "m", "u", "s"
GAP_DAYS = {MISSED: 4, UNSURE: 10}  # SURE → ## Retired. The skill's re-drill close rules.

DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
DUE = re.compile(r"\s+—\s+due\s+(\d{4}-\d{2}-\d{2})\s*\.?\s*$")
QUOTE = re.compile(r"^\s*(?:>\s?)*")
STOP = {"a", "an", "the", "is", "are", "be", "of", "on", "in", "to", "for", "and", "or", "vs",
        "not", "as", "at", "by", "it", "its", "with", "from", "than", "that", "this", "when",
        "what", "why", "how", "only", "no"}


@dataclass
class Item:
    text: str          # the line as written, minus "- " and the due suffix
    due: date | None   # None = undated, which counts as due
    topic: str         # lesson title, or "Quiz log · <section>"
    source: str = ""   # "lesson" or "quiz"
    when: date | None = None  # date of the block that last wrote it

    @property
    def key(self) -> str:
        return key(self.text)


@dataclass
class Block:
    """One `## Gaps` (+ following `## Retired`) block, or one Quiz-log row."""
    when: date
    topic: str
    source: str
    events: list  # [("gap", text, due | None) | ("retired", text, None)], in order


# ── identity ──────────────────────────────────────────────────────────────────

def _strip_parens(text: str) -> str:
    prev = None
    while prev != text:
        prev, text = text, re.sub(r"\([^()]*\)", "", text)
    return text


def head(text: str) -> str:
    """The miss itself, normalised: no due suffix, no " — correct version", no parentheticals,
    no ": explanation" (C++ Foundations style), no markdown, lowercase."""
    t = DUE.sub("", text)
    t = t.split(" — ")[0]
    t = _strip_parens(t)
    t = t.split(": ")[0]
    t = re.sub(r"[*`]", "", t).lower()
    return " ".join(re.sub(r"[^a-z0-9+#]+", " ", t).split())


def key(text: str) -> str:
    return hashlib.sha1(head(text).encode()).hexdigest()[:8]


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", head(text)) if w not in STOP and not w.isdigit()}


def _all_words(text: str) -> set[str]:
    """Significant words of the whole line, explanation included."""
    t = re.sub(r"[*`]", "", DUE.sub("", text)).lower()
    return {w for w in re.findall(r"[a-z0-9]+", t) if len(w) >= 2 and w not in STOP}


def same(a: str, b: str) -> bool:
    """Same miss, possibly reworded: equal heads, or one's significant words ⊆ the other's.
    A one-word head ("`static` ×4", "SSO", "pmr") must be a real word, not a stray letter."""
    if key(a) == key(b):
        return True
    wa, wb = _words(a), _words(b)
    small, big = sorted((wa, wb), key=len)
    if not small:
        return False
    return small == big or (small <= big and (len(small) >= 2 or len(next(iter(small))) >= 3))


# ── lesson notes ──────────────────────────────────────────────────────────────

def lesson_date(name: str, content: str = "") -> date | None:
    m = DATE.match(Path(name).name) or re.search(r"^created:\s*(\d{4}-\d{2}-\d{2})", content, re.M)
    return date.fromisoformat(m.group(1) if m.lastindex else m.group(0)) if m else None


def lesson_topic(name: str) -> str:
    stem = Path(name).name
    while stem.endswith(".md"):
        stem = stem[:-3]
    return re.sub(r"^\d{4}-\d{2}-\d{2}\s*", "", stem).strip() or stem


def lesson_blocks(name: str, content: str) -> list[Block]:
    """Every `## Gaps` block in a lesson note, each with the `## Retired` list that follows it."""
    when = lesson_date(name, content)
    if when is None:
        return []
    topic = lesson_topic(name)
    blocks: list[Block] = []
    kind = None  # "gap" / "retired" while inside a list
    for raw in content.splitlines():
        line = QUOTE.sub("", raw).rstrip()
        if re.fullmatch(r"##\s+Gaps", line):
            blocks.append(Block(when, topic, "lesson", []))
            kind = "gap"
        elif re.fullmatch(r"##\s+Retired", line):
            if not blocks or kind is None:  # a Retired list with no Gaps above it
                blocks.append(Block(when, topic, "lesson", []))
            kind = "retired"
        elif kind and line.startswith("- "):
            body = line[2:].strip()
            m = DUE.search(body)
            due = date.fromisoformat(m.group(1)) if m else None
            text = DUE.sub("", body).strip()
            if text:
                blocks[-1].events.append((kind, text, due if kind == "gap" else None))
        else:
            kind = None  # any other line (blank, ## Next, prose, a fence) ends the list
    return [b for b in blocks if b.events]


# ── Quiz log ──────────────────────────────────────────────────────────────────

MARK = re.compile(r"(?:^|(?<=\.\s))(due|retired|partial)\b([^:.]*?):", re.I)


def _split_items(segment: str) -> list[str]:
    segment = segment.strip().rstrip(".").strip()
    parts = segment.split(" · ")
    if len(parts) == 1:  # older rows list with commas: split only outside (), `` and **
        parts, depth, tick, cur = [], 0, False, ""
        i = 0
        while i < len(segment):
            c = segment[i]
            if c == "`":
                tick = not tick
            elif c == "(" and not tick:
                depth += 1
            elif c == ")" and not tick:
                depth = max(0, depth - 1)
            if segment.startswith(", ", i) and not depth and not tick:
                parts.append(cur)
                cur, i = "", i + 2
                continue
            cur += c
            i += 1
        parts.append(cur)
    return [p.strip().rstrip(".").strip() for p in parts if p.strip(" .")]


def quiz_cell_events(cell: str) -> list[tuple]:
    cell = cell.strip()
    if not cell or cell.lower().startswith("none"):
        return []
    marks = list(MARK.finditer(cell))
    if not marks:  # a first quiz: a bare list of misses, undated, so due
        return [("gap", t, None) for t in _split_items(cell)]
    events = []
    for i, m in enumerate(marks):
        seg = cell[m.end(): marks[i + 1].start() if i + 1 < len(marks) else len(cell)]
        word = m.group(1).lower()
        if word == "retired":
            # "Retired to +10" — the skill's quiz-log wording for a right answer. Treated as
            # retired, not as a +10 due date (see the report: this one's ambiguous).
            events += [("retired", t, None) for t in _split_items(seg)]
        else:
            d = DATE.search(m.group(2)) if word == "due" else None
            due = date.fromisoformat(d.group(0)) if d else None
            events += [("gap", t, due) for t in _split_items(seg)]
    return events


def quiz_blocks(content: str) -> list[Block]:
    """One Block per Quiz-log row, oldest first."""
    return [b for b in quiz_rows(content) if b.events]


def quiz_rows(content: str) -> list[Block]:
    """Every Quiz-log row as a Block (events may be empty: "none"), oldest first."""
    rows, inside = [], False
    for line in content.splitlines():
        if line.startswith("## "):
            inside = line.lower().startswith("## quiz log")
            continue
        if not inside or not line.lstrip().startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 4 or not DATE.fullmatch(cells[0]):
            continue  # header and |---| rows
        section = re.sub(r"[*`]", "", _strip_parens(cells[1])).strip()
        rows.append(Block(date.fromisoformat(cells[0]), f"Quiz log · {section}", "quiz",
                          quiz_cell_events("|".join(cells[3:]))))
    rows.reverse()  # the table is newest first
    return rows


# ── replay ────────────────────────────────────────────────────────────────────

def replay(blocks: list[Block]) -> list[Item]:
    """Live gap items after applying every block in date order (stable within a date).

    Quiz-log rows sort before lesson notes of the same date, so a lesson note — including the
    phone's results note — overrides a Quiz-log item with the same head. That is how phone
    results re-date or retire Quiz-log items without the table ever being edited.
    """
    order = sorted(range(len(blocks)), key=lambda i: (blocks[i].when, blocks[i].source != "quiz", i))
    live: list[Item] = []
    for bi in order:
        b = blocks[bi]
        earlier = {id(it) for it in live}  # fuzzy matching only against earlier blocks, never siblings
        for kind, text, due in b.events:
            gone = [it for it in live if (id(it) in earlier and same(it.text, text)) or it.key == key(text)]
            live = [it for it in live if it not in gone]
            if kind == "gap":
                inherited = next((it.topic for it in gone if b.topic == PHONE), None)
                live.append(Item(text, due, inherited or b.topic, b.source, b.when))
    return live


def _drop_mirrors(quiz: list[Block], lesson: list[Block]) -> None:
    """The skill writes one re-drill's misses twice: as lesson-note Gaps lines and, reworded, as
    that session's Quiz-log row, both with the same due date. A Quiz-log miss sharing its due
    date and at least two significant words with a lesson Gaps line is that line's mirror, so
    it's dropped and the lesson line (the fuller "miss — correct version" record) is drilled."""
    by_due: dict = {}
    for b in lesson:
        for kind, text, due in b.events:
            if kind == "gap" and due:
                by_due.setdefault(due, []).append(_all_words(text))
    for b in quiz:
        b.events = [(kind, text, due) for kind, text, due in b.events
                    if not (kind == "gap" and due and
                            any(len(_all_words(text) & w) >= 2 for w in by_due.get(due, [])))]


def _superseded(item: Item, sessions: list[date]) -> bool:
    """A Quiz-log re-drill row dated R is a re-drill session, and a session drills everything
    due (skill, re-drill step 1), closing with Gaps for what was still missed and Retired for
    what came back right. So an item recorded on or before R and due on or before R that no
    later block re-lists was answered right that day, even when its Retired wording differs
    too much to match. Undated lesson items predate due dates and aren't affected."""
    due = item.due or (item.when if item.source == "quiz" else None)
    return due is not None and any(item.when <= r and due <= r for r in sessions)


def collect(lessons: list[tuple[str, str]], quiz_log: str, today: date) -> list[Item]:
    """Items due today or earlier (or undated), oldest due first. lessons: [(filename, content)]."""
    lesson = [b for name, content in lessons for b in lesson_blocks(name, content)]
    rows = quiz_rows(quiz_log or "")
    sessions = [b.when for b in rows if "re-drill" in b.topic.lower()]
    quiz = [b for b in rows if b.events]
    _drop_mirrors(quiz, lesson)
    live = [it for it in replay(lesson + quiz) if not _superseded(it, sessions)]
    due = [it for it in live if it.due is None or it.due <= today]
    return sorted(due, key=lambda it: it.due or date.min)


# ── results note ──────────────────────────────────────────────────────────────

def note_name(today: date) -> str:
    return f"{today.isoformat()} {PHONE}.md"


def new_due(grade: str, today: date) -> date | None:
    """MISSED → +4, UNSURE → +10, SURE → None (retired)."""
    return today + timedelta(days=GAP_DAYS[grade]) if grade in GAP_DAYS else None


def empty_note(today: date) -> str:
    # The same frontmatter the skill's setup step writes for every lesson note.
    return (f"---\ncreated: {today.isoformat()}\ntags: [lesson]\nsummary:\n---\n#Inbox\n\n"
            "## Gaps\n## Retired\n")


def add_result(content: str, item: Item, grade: str, today: date) -> str:
    """Append one graded item to the phone results note (created if content is empty).

    Gaps lines keep the skill's "- <miss> — <correct> — due YYYY-MM-DD" shape: the item's own
    text (which already carries its correct version) plus a new due date. Retired lines are
    the text alone.
    """
    content = content or empty_note(today)
    lines = content.rstrip("\n").split("\n")
    if "## Gaps" not in lines:
        lines += ["", "## Gaps"]
    if "## Retired" not in lines:
        lines.insert(lines.index("## Gaps") + 1, "## Retired")
    due = new_due(grade, today)
    section = "## Gaps" if due else "## Retired"
    entry = f"- {item.text} — due {due.isoformat()}" if due else f"- {item.text}"
    i = lines.index(section) + 1
    while i < len(lines) and lines[i].startswith("- "):
        i += 1
    lines.insert(i, entry)
    return "\n".join(lines) + "\n"


def tally(content: str, today: date) -> tuple[int, int, int]:
    """(missed, unsure, retired) in today's results note."""
    m = u = r = 0
    for b in lesson_blocks(note_name(today), content or ""):
        for kind, _, due in b.events:
            if kind == "retired":
                r += 1
            elif due == new_due(MISSED, today):
                m += 1
            elif due == new_due(UNSURE, today):
                u += 1
    return m, u, r


# ── question cache ────────────────────────────────────────────────────────────

def fallback_qa(item: Item) -> dict:
    """When the model can't be reached: ask for the correct version of the miss itself."""
    miss = DUE.sub("", item.text).split(" — ")[0]
    return {"question": f"What's the correct version of: {re.sub(r'[*`]', '', miss)}?",
            "answer": re.sub(r"[*`]", "", item.text)}


def cached_qa(item: Item, agent, cache_dir: Path) -> dict:
    """{question, answer} for an item: one model call per item, ever (cached by item key).
    A failed call is not cached, so it's retried next time; the drill still runs on the fallback."""
    cache = Path(cache_dir) / "questions.json"
    known = json.loads(cache.read_text()) if cache.exists() else {}
    if item.key in known:
        return known[item.key]
    qa = None
    try:
        qa = agent.make(item.text) if agent else None
    except Exception as e:
        logger.warning(f"Re-drill question failed for {item.key}: {e}")
    if not qa:
        return fallback_qa(item)
    known[item.key] = qa
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(known, indent=1, ensure_ascii=False))
    return qa
