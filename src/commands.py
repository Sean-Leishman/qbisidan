"""Telegram message parser — routes raw text into structured commands."""
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from dateutil import parser as dateutil_parser


@dataclass
class ParsedCommand:
    """A parsed Telegram message ready for routing."""

    command_type: str  # see parse_message docstring for all types
    text: str  # content after command prefix
    url: str | None = None
    user_notes: str | None = None
    project: str | None = None
    due_date: date | None = None
    event_datetime: datetime | None = None
    reply_to_title: str | None = None
    is_raw: bool = False  # True for /note <url> — skip AI processing
    crawl_from: date | None = None
    crawl_to: date | None = None
    crawl_topic: str | None = None
    backfill_kind: str | None = None  # "saved" | "likes" for /backfill instagram ...


_URL_PATTERN = re.compile(r"https?://[^\s<>\"{}|\\^`\[\]]+")

_WEEKDAY_NAMES = {
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
    "friday": 4, "saturday": 5, "sunday": 6,
}

_RELATIVE_DAYS = {"today": 0, "tomorrow": 1}


def _next_weekday(name: str, reference: date | None = None) -> date:
    ref = reference or date.today()
    target = _WEEKDAY_NAMES[name.lower()]
    days_ahead = target - ref.weekday()
    if days_ahead <= 0:
        days_ahead += 7
    return ref + timedelta(days=days_ahead)


def _parse_at_date(text: str) -> tuple[str, date | None]:
    """Extract a due date from @<date> syntax, e.g. 'Buy milk @friday'.

    Returns (cleaned_text, due_date). Removes the @... token from text.
    """
    at_match = re.search(
        r"\s*@(today|tomorrow|monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b",
        text,
        re.IGNORECASE,
    )
    if at_match:
        word = at_match.group(1).lower()
        cleaned = (text[: at_match.start()] + text[at_match.end():]).strip()
        if word in _RELATIVE_DAYS:
            return cleaned, date.today() + timedelta(days=_RELATIVE_DAYS[word])
        return cleaned, _next_weekday(word)

    # @YYYY-MM-DD or @Apr10 etc.
    at_date_match = re.search(r"\s*@(\S+)", text)
    if at_date_match:
        try:
            parsed = dateutil_parser.parse(at_date_match.group(1), fuzzy=True)
            cleaned = (text[: at_date_match.start()] + text[at_date_match.end():]).strip()
            return cleaned, parsed.date()
        except (ValueError, OverflowError):
            pass

    return text, None


def _parse_event_datetime(text: str) -> tuple[str, datetime | None]:
    """Extract event title and datetime from text like 'Dentist 2026-04-10 14:00'."""
    datetime_match = re.search(
        r"^(.+?)\s+(\d{4}-\d{2}-\d{2})\s+(\d{1,2}:\d{2})\s*$", text
    )
    if datetime_match:
        try:
            dt = datetime.strptime(
                f"{datetime_match.group(2)} {datetime_match.group(3)}", "%Y-%m-%d %H:%M"
            )
            return datetime_match.group(1).strip(), dt
        except ValueError:
            pass

    relative_match = re.search(
        r"^(.+?)\s+(today|tomorrow)\s+(\d{1,2}(?::\d{2})?\s*(?:am|pm)?)\s*$",
        text, re.IGNORECASE,
    )
    if relative_match:
        try:
            t = dateutil_parser.parse(relative_match.group(3))
            day_word = relative_match.group(2).lower()
            base = date.today() + timedelta(days=_RELATIVE_DAYS.get(day_word, 0))
            return relative_match.group(1).strip(), datetime.combine(base, t.time())
        except (ValueError, OverflowError):
            pass

    try:
        parsed_dt, tokens = dateutil_parser.parse(text, fuzzy_with_tokens=True)
        title = " ".join(t.strip() for t in tokens if t.strip())
        if title and parsed_dt:
            return title, parsed_dt
    except (ValueError, OverflowError, TypeError):
        pass

    return text, None


_CRAWL_DATE_RE = re.compile(r"\b(from|to):(\d{4}-\d{2}-\d{2})\b", re.IGNORECASE)
_CRAWL_TOPIC_QUOTED_RE = re.compile(r'\btopic:"([^"]+)"', re.IGNORECASE)
_CRAWL_TOPIC_BARE_RE = re.compile(r"\btopic:(\S+)", re.IGNORECASE)


def _parse_crawl_args(body: str) -> tuple[str | None, date | None, date | None, str | None]:
    """Extract url + from/to/topic kwargs from /crawl body. Returns (url, from, to, topic)."""
    url_match = _URL_PATTERN.search(body)
    url = url_match.group(0) if url_match else None
    if url:
        body = (body[: url_match.start()] + body[url_match.end():]).strip()

    date_from: date | None = None
    date_to: date | None = None
    for match in _CRAWL_DATE_RE.finditer(body):
        try:
            parsed = datetime.strptime(match.group(2), "%Y-%m-%d").date()
        except ValueError:
            continue
        if match.group(1).lower() == "from":
            date_from = parsed
        else:
            date_to = parsed
    body = _CRAWL_DATE_RE.sub("", body).strip()

    topic: str | None = None
    quoted = _CRAWL_TOPIC_QUOTED_RE.search(body)
    if quoted:
        topic = quoted.group(1).strip()
        body = _CRAWL_TOPIC_QUOTED_RE.sub("", body).strip()
    else:
        bare = _CRAWL_TOPIC_BARE_RE.search(body)
        if bare:
            topic = bare.group(1).strip()
            body = _CRAWL_TOPIC_BARE_RE.sub("", body).strip()

    return url, date_from, date_to, topic


def _extract_reply_title(reply_text: str | None) -> str | None:
    """Extract note title from a bot success message like 'Created note: *Title*'."""
    if not reply_text:
        return None
    match = re.search(r"Created note:\s*\*(.+?)\*", reply_text)
    return match.group(1) if match else None


def parse_message(text: str, reply_to_text: str | None = None) -> ParsedCommand:
    """Parse a raw Telegram message into a structured command.

    Command types:
        url             — bare URL → full bookmark pipeline (scrape + AI)
        question        — reply to bot success message → append to note
        help            — /help → send command reference
        todo_inbox      — /todo without @date → 08 Trackers/Inbox.md
        todo_deadline   — /todo with @date   → 08 Trackers/Deadlines.md
        someday         — /someday           → 08 Trackers/Someday.md
        read            — /read              → 08 Trackers/To Be Read.md
        watch           — /watch             → 08 Trackers/To Be Watched.md
        fleeting_note   — /note              → 01 Fleeting Notes/<auto-title>.md
        daily_note      — /daily             → daily note ### Notes
        daily_task      — /daily task        → daily note ### Tasks
        project_todo    — /project X todo    → 07 Projects/X/Todo.md
        project_note    — /project X         → 07 Projects/X/Notes.md
        event           — /event             → ICS + daily note
        channel_crawl   — /crawl <url> ...   → batch-bookmark a channel's videos
        backfill        — /backfill ...      → batch-ingest a saved history, filtered by interests
        inbox           — plain text default → 08 Trackers/Inbox.md
    """
    text = text.strip()

    # /help
    if text.lower().startswith("/help"):
        return ParsedCommand(command_type="help", text="")

    # /search <query>
    if text.lower().startswith("/search"):
        query = text[7:].strip()
        return ParsedCommand(command_type="search", text=query)

    # /status
    if text.lower().startswith("/status"):
        return ParsedCommand(command_type="status", text="")

    # Reply to bot message → question about that content
    reply_title = _extract_reply_title(reply_to_text)
    if reply_title:
        return ParsedCommand(command_type="question", text=text, reply_to_title=reply_title)

    # /todo — inbox or deadline depending on @date
    if text.lower().startswith("/todo"):
        body = text[5:].strip()
        cleaned, due = _parse_at_date(body)
        if due:
            return ParsedCommand(command_type="todo_deadline", text=cleaned, due_date=due)
        return ParsedCommand(command_type="todo_inbox", text=cleaned)

    # /someday
    if text.lower().startswith("/someday"):
        body = text[8:].strip()
        return ParsedCommand(command_type="someday", text=body)

    # /read
    if text.lower().startswith("/read"):
        body = text[5:].strip()
        url_match = _URL_PATTERN.search(body)
        return ParsedCommand(
            command_type="read",
            text=body,
            url=url_match.group(0) if url_match else None,
        )

    # /watch
    if text.lower().startswith("/watch"):
        body = text[6:].strip()
        url_match = _URL_PATTERN.search(body)
        return ParsedCommand(
            command_type="watch",
            text=body,
            url=url_match.group(0) if url_match else None,
        )

    # /note — creates a fleeting note file; if body is a URL, mark as raw (no AI)
    if text.lower().startswith("/note"):
        body = text[5:].strip()
        url_match = _URL_PATTERN.search(body)
        is_url = bool(url_match)
        return ParsedCommand(
            command_type="fleeting_note",
            text=body,
            url=url_match.group(0) if is_url else None,
            is_raw=is_url,  # URL passed to /note → save raw, skip pipeline
        )

    # /daily — daily note (note or task)
    if text.lower().startswith("/daily"):
        body = text[6:].strip()
        if body.lower().startswith("task"):
            task_text = body[4:].strip()
            return ParsedCommand(command_type="daily_task", text=task_text)
        return ParsedCommand(command_type="daily_note", text=body)

    # /project <name> todo|<text>
    if text.lower().startswith("/project"):
        body = text[8:].strip()
        project_match = re.match(r"(\S+)\s+(.+)", body, re.DOTALL)
        if project_match:
            project = project_match.group(1)
            remainder = project_match.group(2).strip()
            if remainder.lower().startswith("todo"):
                todo_text = remainder[4:].strip()
                cleaned, due = _parse_at_date(todo_text)
                return ParsedCommand(
                    command_type="project_todo", text=cleaned,
                    project=project, due_date=due,
                )
            return ParsedCommand(command_type="project_note", text=remainder, project=project)
        return ParsedCommand(command_type="inbox", text=text)

    # /crawl <channel_url> [from:YYYY-MM-DD] [to:YYYY-MM-DD] [topic:"..."]
    if text.lower().startswith("/crawl"):
        body = text[6:].strip()
        url, date_from, date_to, topic = _parse_crawl_args(body)
        return ParsedCommand(
            command_type="channel_crawl",
            text=body,
            url=url,
            crawl_from=date_from,
            crawl_to=date_to,
            crawl_topic=topic,
        )

    # /backfill instagram [saved|likes] [from:...] [to:...] [topic:"..."]
    # /backfill <playlist-url> [from:...] [to:...] [topic:"..."]
    if text.lower().startswith("/backfill"):
        body = text[9:].strip()
        url, date_from, date_to, topic = _parse_crawl_args(body)
        kind_match = re.match(r"instagram\s+(saved|likes)\b", body, re.IGNORECASE)
        return ParsedCommand(
            command_type="backfill",
            text=body,
            url=url,
            backfill_kind=kind_match.group(1).lower() if kind_match else None,
            crawl_from=date_from,
            crawl_to=date_to,
            crawl_topic=topic,
        )

    # /event
    if text.lower().startswith("/event"):
        body = text[6:].strip()
        title, dt = _parse_event_datetime(body)
        return ParsedCommand(command_type="event", text=title, event_datetime=dt)

    # Bare URL → full bookmark pipeline
    url_match = _URL_PATTERN.search(text)
    if url_match:
        url = url_match.group(0)
        remaining = (text[: url_match.start()] + text[url_match.end():]).strip()
        if remaining.startswith("|"):
            remaining = remaining[1:].strip()
        return ParsedCommand(
            command_type="url", text=text, url=url,
            user_notes=remaining if remaining else None,
        )

    # Default: plain text → inbox
    return ParsedCommand(command_type="inbox", text=text)
