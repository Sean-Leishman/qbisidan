"""Todo trackers as text: find, tick, move. Pure functions on file contents, no I/O.

Items are identified by a short hash of their text, not a line number, so a button tapped
minutes later still finds its item after other lines have moved.
"""
import calendar
import hashlib
import re
from datetime import date, timedelta

TRACKERS = {
    "i": "08 Trackers/Inbox.md",
    "w": "08 Trackers/This Week.md",
    "d": "08 Trackers/Deadlines.md",
    "s": "08 Trackers/Someday.md",
}
NAMES = {"i": "Inbox", "w": "This Week", "d": "Deadlines", "s": "Someday"}
WEEK_MAX = 10  # the vault guide's cap for This Week

OPEN = re.compile(r"^\s*- \[ \] (.+?)\s*$")
STAMP = re.compile(r"\s*<!--.*?-->\s*$")  # Inbox lines carry "<!-- 2026-11-15 -->"
WEEK = re.compile(r"^## Week of (\d{4}-\d{2}-\d{2})")
DATED = re.compile(r"^\s*- \[[ x]\] (\d{4}-\d{2}-\d{2}) ")


def clean(body: str) -> str:
    return STAMP.sub("", body).strip()


def key(text: str) -> str:
    return hashlib.sha1(clean(text).encode()).hexdigest()[:8]


def open_items(content: str) -> list[str]:
    return [clean(m.group(1)) for line in content.splitlines() if (m := OPEN.match(line))]


def matches(query: str, text: str) -> bool:
    """Every word typed appears somewhere in the item, case-insensitive. "vint" finds "Vinted"."""
    words = query.lower().split()
    return bool(words) and all(w in text.lower() for w in words)


def _find_line(lines, k):
    for i, line in enumerate(lines):
        m = OPEN.match(line)
        if m and key(m.group(1)) == k:
            return i, clean(m.group(1))
    return None, None


def tick(content: str, k: str, today: date) -> tuple[str, str | None]:
    lines = content.splitlines()
    i, text = _find_line(lines, k)
    if i is None:
        return content, None
    lines[i] = lines[i].replace("- [ ] ", "- [x] ", 1).rstrip() + f" ✅ {today.isoformat()}"
    return "\n".join(lines) + "\n", text


def remove(content: str, k: str) -> tuple[str, str | None]:
    lines = content.splitlines()
    i, text = _find_line(lines, k)
    if i is None:
        return content, None
    del lines[i]
    return "\n".join(lines) + "\n", text


# ---------------------------------------------------------------- This Week

def target_monday(today: date) -> date:
    """The week being planned: on Sunday that's tomorrow's week, otherwise this one."""
    return today + timedelta(days=1) if today.weekday() == 6 else today - timedelta(days=today.weekday())


def _week_spans(lines):
    """[(monday iso | None, start, end)] — which lines belong to which "## Week of" section."""
    spans, current, start = [], None, 0
    for i, line in enumerate(lines):
        if m := WEEK.match(line):
            spans.append((current, start, i))
            current, start = m.group(1), i
    spans.append((current, start, len(lines)))
    return spans


def week_items(content: str, monday: date) -> list[str]:
    lines = content.splitlines()
    return [clean(m.group(1)) for wk, a, b in _week_spans(lines) if wk == monday.isoformat()
            for line in lines[a:b] if (m := OPEN.match(line))]


def stale_week_items(content: str, monday: date) -> list[str]:
    """Open items left in earlier weeks' sections: they need re-triage, not silent carry-over."""
    lines = content.splitlines()
    return [clean(m.group(1)) for wk, a, b in _week_spans(lines) if wk and wk != monday.isoformat()
            for line in lines[a:b] if (m := OPEN.match(line))]


def add_to_week(content: str, text: str, monday: date) -> str:
    lines = content.rstrip("\n").splitlines()
    for wk, a, b in _week_spans(lines):
        if wk == monday.isoformat():
            last = max((i for i in range(a, b) if lines[i].lstrip().startswith("- [")), default=a + 1)
            lines.insert(last + 1, f"- [ ] {text}")
            return "\n".join(lines) + "\n"
    return "\n".join(lines) + f"\n\n## Week of {monday.isoformat()}\n\n- [ ] {text}\n"


# ---------------------------------------------------------------- Deadlines and Someday

def add_deadline(content: str, text: str, due: date) -> str:
    """"- [ ] YYYY-MM-DD — text", inserted in date order (the file's own convention)."""
    lines = content.rstrip("\n").splitlines()
    new = f"- [ ] {due.isoformat()} — {text}"
    dated = [i for i, line in enumerate(lines) if DATED.match(line)]
    later = [i for i in dated if DATED.match(lines[i]).group(1) > due.isoformat()]
    at = later[0] if later else (dated[-1] + 1 if dated else len(lines))
    lines.insert(at, new)
    return "\n".join(lines) + "\n"


SOMEDAY_SECTION = "## From triage"


def add_someday(content: str, text: str) -> str:
    """Someday is grouped by life area, which triage can't guess; park under one heading to sort monthly."""
    lines = content.rstrip("\n").splitlines()
    if SOMEDAY_SECTION in lines:
        at = lines.index(SOMEDAY_SECTION) + 1
        while at < len(lines) and lines[at].lstrip().startswith("- ["):
            at += 1
        lines.insert(at, f"- [ ] {text}")
        return "\n".join(lines) + "\n"
    return "\n".join(lines) + f"\n\n{SOMEDAY_SECTION}\n- [ ] {text}\n"


def deadline_choices(today: date) -> list[tuple[str, date]]:
    """The dates a triage button offers: this Friday, next Friday, month end, a month out."""
    fri = today + timedelta(days=(4 - today.weekday()) % 7 or 7)
    end = date(today.year, today.month, calendar.monthrange(today.year, today.month)[1])
    if (end - today).days < 3:
        nxt = end + timedelta(days=1)
        end = date(nxt.year, nxt.month, calendar.monthrange(nxt.year, nxt.month)[1])
    out = [fri, fri + timedelta(days=7), end, today + timedelta(days=30)]
    return [(f"{d:%a} {d.day} {d:%b}", d) for d in dict.fromkeys(out)]
