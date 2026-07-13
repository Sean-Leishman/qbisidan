"""Vault file writer — daily notes, trackers, fleeting notes, events, questions."""
import logging
import re
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

_ORDINAL_SUFFIXES = {1: "st", 2: "nd", 3: "rd", 21: "st", 22: "nd", 23: "rd", 31: "st"}


def _ordinal(n: int) -> str:
    return f"{n}{_ORDINAL_SUFFIXES.get(n, 'th')}"


def _format_daily_date(d: date) -> str:
    return f"{d.strftime('%A')}, {d.strftime('%B')} {_ordinal(d.day)}, {d.year}"


def _sanitize_filename(text: str, max_len: int = 80) -> str:
    """Convert text to a safe filename."""
    name = re.sub(r'[<>:"/\\|?*\n\r\t]', "", text)
    name = re.sub(r"\s+", " ", name).strip()
    return name[:max_len]


def _auto_title(text: str) -> str:
    """Generate a title from the first 6-8 words of text."""
    words = text.split()
    return " ".join(words[:7]) if len(words) > 7 else text


class VaultWriter:
    """Handles all vault file mutations outside the main note pipeline."""

    def __init__(
        self,
        vault_path: Path,
        daily_notes_folder: str = "06 Daily Notes",
        ics_folder: str = "",
        books_folder: str = "02 Sources/Books",
    ):
        self.vault_path = Path(vault_path)
        self.daily_notes_folder = daily_notes_folder
        self.ics_folder = Path(ics_folder) if ics_folder else None
        self.books_folder = books_folder

    # ── Tracker helpers ────────────────────────────────────────────────────────

    def _append_to_tracker(self, relative_path: str, line: str) -> None:
        """Append a line to a tracker file. Creates the file with minimal header if missing."""
        path = self.vault_path / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)

        if not path.exists():
            title = path.stem
            header = f"""---
title: {title}
created: {date.today().isoformat()}
tags:
  - tracker
cssclasses:
  - center-images
  - status-tag
  - base-notes
---
> [!note]+ **Properties**
> **Created:** {date.today().isoformat()}
> **Status:** #Inbox

---

"""
            path.write_text(header + line + "\n", encoding="utf-8")
            logger.info(f"Created tracker: {path}")
            return

        content = path.read_text(encoding="utf-8")
        content = content.rstrip() + "\n" + line + "\n"
        path.write_text(content, encoding="utf-8")
        logger.info(f"Appended to tracker {relative_path}")

    # ── Todo / Inbox trackers ──────────────────────────────────────────────────

    def append_to_inbox(self, text: str) -> None:
        """Append to 08 Trackers/Inbox.md — unsorted, to be triaged."""
        date_stamp = date.today().isoformat()
        self._append_to_tracker(
            "08 Trackers/Inbox.md",
            f"- [ ] {text}  <!-- {date_stamp} -->",
        )

    def append_to_deadlines(self, text: str, due_date: date) -> None:
        """Append to 08 Trackers/Deadlines.md — tasks with a specific due date."""
        self._append_to_tracker(
            "08 Trackers/Deadlines.md",
            f"- [ ] {text} | {due_date.isoformat()}",
        )

    def append_to_someday(self, text: str) -> None:
        """Append to 08 Trackers/Someday.md — no-urgency parking lot."""
        self._append_to_tracker("08 Trackers/Someday.md", f"- [ ] {text}")

    # ── Media trackers ─────────────────────────────────────────────────────────

    def append_to_read(self, text: str) -> None:
        """Append to 08 Trackers/To Be Read.md."""
        self._append_to_tracker("08 Trackers/To Be Read.md", f"- [ ] {text}")

    def append_to_watch(self, text: str) -> None:
        """Append to 08 Trackers/To Be Watched.md."""
        self._append_to_tracker("08 Trackers/To Be Watched.md", f"- [ ] {text}")

    # ── Fleeting notes ─────────────────────────────────────────────────────────

    def create_fleeting_note(self, text: str, source_url: str | None = None) -> Path:
        """Create a new fleeting note in 01 Fleeting Notes/.

        If source_url is provided the note stores it as a raw link (no AI processing).
        Returns the path to the created file.
        """
        now = datetime.now()
        title = _auto_title(text) if not source_url else _auto_title(text)
        filename = _sanitize_filename(title) + ".md"
        path = self.vault_path / "01 Fleeting Notes" / filename

        # Handle filename collision
        counter = 1
        while path.exists():
            path = self.vault_path / "01 Fleeting Notes" / f"{_sanitize_filename(title)} ({counter}).md"
            counter += 1

        source_line = f"source: {source_url}" if source_url else "source:"
        created_str = now.strftime("%Y-%m-%dT%H-%M")

        content = f"""---
title: {title}
created: {created_str}
links:
tags:
{source_line}
cssclasses:
  - center-images
  - status-tag
  - base-notes
---
> [!note]+ **Properties**
> **Created:** {created_str}
> **Origin:**
> **Status:** #Inbox
> **Tags:**
> **Sources:** {"[[" + source_url + "]]" if source_url else ""}

---

{text if not source_url else f"[{text}]({source_url})" if text != source_url else source_url}
"""
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        logger.info(f"Created fleeting note: {path}")
        return path

    # ── Daily note ─────────────────────────────────────────────────────────────

    def _daily_note_path(self, target_date: date | None = None) -> Path:
        d = target_date or date.today()
        return self.vault_path / self.daily_notes_folder / f"{d.isoformat()}.md"

    def _create_daily_note_content(self, target_date: date) -> str:
        now = datetime.now()
        weekday = target_date.strftime("%A").lower()
        time_str = now.strftime("%H:%M") if target_date == date.today() else "00:00"
        return f"""---
date: {target_date.isoformat()}T{time_str}
tags:
  - Daily
cssclasses:
  - daily
  - {weekday}
---
# DAILY NOTE
## {_format_daily_date(target_date)}
***
### Journal

***
### Tasks

***
### Notes

***
### Future Thoughts

### Task List
"""

    def _append_to_daily_section(
        self,
        text: str,
        section: str,
        as_todo: bool = False,
        target_date: date | None = None,
    ) -> None:
        d = target_date or date.today()
        path = self._daily_note_path(d)
        line = f"- [ ] {text}" if as_todo else f"- {text}"

        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            content = self._create_daily_note_content(d)
            content = content.replace(f"### {section}\n", f"### {section}\n{line}\n")
            path.write_text(content, encoding="utf-8")
            logger.info(f"Created daily note: {path}")
            return

        content = path.read_text(encoding="utf-8")
        match = re.search(rf"(### {re.escape(section)}\n)", content)
        if match:
            pos = match.end()
            content = content[:pos] + line + "\n" + content[pos:]
        else:
            content = content.rstrip() + f"\n\n### {section}\n{line}\n"
        path.write_text(content, encoding="utf-8")
        logger.info(f"Appended to daily note ({section}): {path}")

    def append_to_daily_note(self, text: str, target_date: date | None = None) -> None:
        """Append to the ### Notes section of a daily note."""
        self._append_to_daily_section(text, "Notes", target_date=target_date)

    def append_task_to_daily_note(self, text: str, target_date: date | None = None) -> None:
        """Append a checkbox to the ### Tasks section of a daily note."""
        self._append_to_daily_section(text, "Tasks", as_todo=True, target_date=target_date)

    # ── Project files ──────────────────────────────────────────────────────────

    def append_to_project_todo(self, project: str, text: str, due_date: date | None = None) -> None:
        """Append a todo to 07 Projects/<project>/Todo.md."""
        project_dir = self.vault_path / "07 Projects" / project
        todo_path = project_dir / "Todo.md"
        due_str = f" @{due_date.isoformat()}" if due_date else ""
        line = f"- [ ] {text}{due_str}\n"

        if not todo_path.exists():
            project_dir.mkdir(parents=True, exist_ok=True)
            todo_path.write_text(
                f'---\ntitle: "{project} Todo"\ncreated: {date.today().isoformat()}\ntags:\n  - project\n  - todo\n---\n\n# {project} — Todo\n\n{line}',
                encoding="utf-8",
            )
            logger.info(f"Created project todo: {todo_path}")
            return

        content = todo_path.read_text(encoding="utf-8")
        todo_path.write_text(content.rstrip() + "\n" + line, encoding="utf-8")
        logger.info(f"Appended to project todo: {todo_path}")

    def append_to_project_note(self, project: str, text: str) -> None:
        """Append a note to 07 Projects/<project>/Notes.md."""
        project_dir = self.vault_path / "07 Projects" / project
        notes_path = project_dir / "Notes.md"
        date_stamp = date.today().isoformat()
        line = f"- {text}  <!-- {date_stamp} -->\n"

        if not notes_path.exists():
            project_dir.mkdir(parents=True, exist_ok=True)
            notes_path.write_text(
                f'---\ntitle: "{project} Notes"\ncreated: {date.today().isoformat()}\ntags:\n  - project\n---\n\n# {project} — Notes\n\n{line}',
                encoding="utf-8",
            )
            logger.info(f"Created project notes: {notes_path}")
            return

        content = notes_path.read_text(encoding="utf-8")
        notes_path.write_text(content.rstrip() + "\n" + line, encoding="utf-8")
        logger.info(f"Appended to project notes: {notes_path}")

    # ── Questions ──────────────────────────────────────────────────────────────

    def append_question_to_note(self, note_title: str, question: str) -> bool:
        """Find a note by title and append a question under ## Questions."""
        search_dirs = [
            self.vault_path / "02 Sources",
            self.vault_path / "05 Base Notes",
            self.vault_path / "Clippings",
        ]
        sanitized = re.sub(r'[<>:"/\\|?*]', "", note_title)
        target = None
        for search_dir in search_dirs:
            if not search_dir.exists():
                continue
            for md_file in search_dir.rglob("*.md"):
                if sanitized.lower() in md_file.stem.lower():
                    target = md_file
                    break
            if target:
                break

        if not target:
            logger.warning(f"Could not find note matching '{note_title}'")
            return False

        content = target.read_text(encoding="utf-8")
        line = f"- {question}\n"
        match = re.search(r"(## Questions\n)", content)
        if match:
            content = content[: match.end()] + line + content[match.end():]
        else:
            final_sep = content.rfind("\n---\n")
            if final_sep != -1:
                content = content[:final_sep] + f"\n## Questions\n{line}" + content[final_sep:]
            else:
                content = content.rstrip() + f"\n\n## Questions\n{line}"
        target.write_text(content, encoding="utf-8")
        logger.info(f"Appended question to: {target}")
        return True

    # ── Source entities (books, magazines, papers, podcasts) ───────────────────

    def ensure_source_note(
        self,
        title: str,
        source_type: str,
        author: str | None = None,
        referenced_by: str | None = None,
    ) -> Path:
        """Create the entity note for a mentioned book/magazine/paper/podcast if
        it doesn't already exist, and append a backlink to the referencing note
        under '## Referenced by'. Idempotent: calling twice with the same
        (title, referenced_by) does not duplicate the backlink line.

        # ponytail: exact title match, one canonical file per title — no fuzzy
        # matching ("Sapiens" vs "Sapiens: A Brief History..." make two notes
        # until that earns itself; see PLAN-ingest.md Phase 5).
        """
        path = self.vault_path / self.books_folder / (_sanitize_filename(title) + ".md")

        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            lines = ["---", f'title: "{title}"', f"type: {source_type}"]
            if author:
                lines.append(f'author: "{author}"')
            lines += [
                f"created: {date.today().isoformat()}",
                "tags:",
                "  - source-entity",
                "---",
                "",
                f"# {title}",
                "",
                "## Referenced by",
                "",
            ]
            path.write_text("\n".join(lines), encoding="utf-8")
            logger.info(f"Created source note: {path}")

        if referenced_by:
            content = path.read_text(encoding="utf-8")
            link_line = f"- [[{referenced_by}]]"
            if link_line not in content.splitlines():
                content = content.rstrip("\n") + f"\n{link_line}\n"
                path.write_text(content, encoding="utf-8")
                logger.info(f"Linked {referenced_by!r} -> {path}")

        return path

    # ── Channel crawls ─────────────────────────────────────────────────────────

    def write_channel_index(
        self,
        channel_name: str,
        date_from: date | None,
        date_to: date | None,
        topic: str | None,
        note_titles: list[str],
        skipped: list[str] | None = None,
    ) -> Path:
        """Write an index page listing every note created by a /crawl run."""
        sanitized_channel = _sanitize_filename(channel_name) or "channel"
        from_str = date_from.isoformat() if date_from else "all"
        to_str = date_to.isoformat() if date_to else "all"
        filename = f"{sanitized_channel}-{from_str}-{to_str}.md"
        path = self.vault_path / "04 Indexes" / "Channels" / filename
        path.parent.mkdir(parents=True, exist_ok=True)

        lines = [
            "---",
            f"title: {channel_name} crawl ({from_str} → {to_str})",
            f"created: {date.today().isoformat()}",
            "tags:",
            "  - channel-index",
            "---",
            "",
            f"# {channel_name}",
            "",
            f"- Range: {from_str} → {to_str}",
        ]
        if topic:
            lines.append(f"- Topic filter: {topic}")
        lines.append(f"- Videos saved: {len(note_titles)}")
        lines.append("")
        lines.append("## Notes")
        lines.extend(f"- [[{t}]]" for t in note_titles)
        if skipped:
            lines.append("")
            lines.append("## Skipped (already in vault)")
            lines.extend(f"- [[{t}]]" for t in skipped)
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        logger.info(f"Wrote channel index: {path}")
        return path

    # ── Calendar events ────────────────────────────────────────────────────────

    def create_event(self, title: str, dt: datetime, duration_minutes: int = 60) -> Path | None:
        """Create an ICS calendar event file. Returns path or None if unconfigured."""
        if not self.ics_folder:
            logger.warning("ICS folder not configured, skipping event creation")
            return None

        self.ics_folder.mkdir(parents=True, exist_ok=True)
        end_dt = dt + timedelta(minutes=duration_minutes)

        def _fmt(d: datetime) -> str:
            return d.strftime("%Y%m%dT%H%M%S")

        ics = (
            f"BEGIN:VCALENDAR\nVERSION:2.0\nPRODID:-//ObsidianHelper//EN\n"
            f"BEGIN:VEVENT\nUID:{uuid.uuid4()}\nDTSTAMP:{_fmt(datetime.utcnow())}\n"
            f"DTSTART:{_fmt(dt)}\nDTEND:{_fmt(end_dt)}\nSUMMARY:{title}\n"
            f"END:VEVENT\nEND:VCALENDAR\n"
        )
        filename = _sanitize_filename(title) + f"_{dt.strftime('%Y%m%d')}.ics"
        path = self.ics_folder / filename
        path.write_text(ics, encoding="utf-8")
        logger.info(f"Created ICS event: {path}")
        return path

    def add_event_to_daily_note(self, title: str, dt: datetime) -> None:
        """Add an event entry to the daily note for the event's date."""
        self._append_to_daily_section(
            f"**{dt.strftime('%H:%M')}** — {title}",
            "Notes",
            target_date=dt.date(),
        )
