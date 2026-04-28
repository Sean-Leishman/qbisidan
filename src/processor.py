"""Main processing orchestrator."""
import logging
from dataclasses import dataclass

from .agents.question_agent import QuestionAgent
from .agents.vault_writer import VaultWriter
from .commands import ParsedCommand, parse_message
from .config import Config
from .linker import NoteLinkEngine
from .obsidian import ObsidianNoteGenerator
from .scrapers import WebpageScraper, YouTubeScraper
from .search import VaultSearch, format_search_results
from .summarizer import Summarizer
from .telegram_queue import TelegramMessage, TelegramQueue

logger = logging.getLogger(__name__)


@dataclass
class ProcessingResult:
    url: str | None
    success: bool
    title: str | None = None
    folder: str | None = None
    message: str | None = None
    error: str | None = None


class Processor:
    def __init__(self, config: Config):
        self.config = config

        self.telegram = TelegramQueue(
            bot_token=config.telegram.bot_token,
            allowed_chat_ids=config.telegram.allowed_chat_ids,
        )
        self.scrapers = [
            YouTubeScraper(transcript_languages=config.youtube.transcript_languages),
            WebpageScraper(),
        ]
        self.summarizer = Summarizer(
            provider=config.ai.provider,
            api_key=config.ai.api_key,
            model=config.ai.model,
            max_tokens=config.ai.max_tokens,
            routing_categories=config.get_routing_category_names(),
        )
        self.question_agent = QuestionAgent(
            provider=config.ai.provider,
            api_key=config.ai.api_key,
            model=config.ai.model,
            max_tokens=512,
        )
        self.linker = NoteLinkEngine(vault_path=config.vault_path)
        self.note_generator = ObsidianNoteGenerator(
            vault_path=config.vault_path,
            output_folders={
                "youtube": config.output_folders.youtube,
                "article": config.output_folders.article,
                "default": config.output_folders.default,
            },
        )
        self.vault_writer = VaultWriter(
            vault_path=config.vault_path,
            daily_notes_folder=config.vault_writer.daily_notes_folder,
            ics_folder=config.vault_writer.ics_folder,
        )
        self.search = VaultSearch(vault_path=config.vault_path)

    def _get_scraper(self, url: str):
        for scraper in self.scrapers:
            if scraper.can_handle(url):
                return scraper
        return None

    def _get_folder_for_content(self, scraped, summary) -> str | None:
        if scraped.content_type == "youtube":
            return None
        folder = self.config.get_folder_for_category(summary.category)
        if folder:
            logger.info(f"AI routed to category '{summary.category}' -> {folder}")
            return folder
        return None

    HELP_TEXT = (
        "*Obsidian Helper — Commands*\n\n"
        "*Bookmarks*\n"
        "`<URL>` — scrape + AI summary → vault note\n"
        "`<URL> | my notes` — same, with personal context\n"
        "_(reply to Created note: msg)_ — append question to that note\n\n"
        "*Capture*\n"
        "`/todo <text>` — Inbox\n"
        "`/todo <text> @<date>` — Deadlines\n"
        "`/someday <text>` — Someday\n"
        "`/note <text>` — new Fleeting Note\n"
        "`/note <url>` — raw link, no AI\n\n"
        "*Lists*\n"
        "`/read <title or url>` — To Be Read\n"
        "`/watch <title or url>` — To Be Watched\n\n"
        "*Daily Note*\n"
        "`/daily <text>` — ### Notes\n"
        "`/daily task <text>` — ### Tasks\n\n"
        "*Projects*\n"
        "`/project <name> todo <text>` — Todo.md\n"
        "`/project <name> todo <text> @<date>` — with due date\n"
        "`/project <name> <text>` — Notes.md\n\n"
        "*Calendar*\n"
        "`/event <title> <YYYY-MM-DD> <HH:MM>` — ICS + daily note\n\n"
        "*Date syntax:* `@today` `@tomorrow` `@friday` `@2026-05-01`\n\n"
        "`/search <query>` — search vault notes\n"
        "`/status` — today's capture summary\n"
        "`/help` — show this message"
    )

    # ── Command handlers ───────────────────────────────────────────────────────

    def _process_help(self, _cmd: ParsedCommand, message: TelegramMessage) -> ProcessingResult:
        """/help → send command reference to chat."""
        self.telegram.send_notification(message.chat_id, self.HELP_TEXT)
        return ProcessingResult(url=None, success=True)

    def _process_search(self, cmd: ParsedCommand, message: TelegramMessage) -> ProcessingResult:
        """/search <query> → full-text vault search, reply with ranked results."""
        if not cmd.text:
            self.telegram.send_notification(message.chat_id, "Usage: `/search <query>`")
            return ProcessingResult(url=None, success=True)
        results = self.search.search(cmd.text)
        reply = format_search_results(cmd.text, results)
        self.telegram.send_notification(message.chat_id, reply)
        return ProcessingResult(url=None, success=True)

    def _process_status(self, _cmd: ParsedCommand, message: TelegramMessage) -> ProcessingResult:
        """/status → today's activity summary."""
        from datetime import date
        stats = self.telegram.get_today_stats()
        today_label = date.today().strftime("%B %-d")

        if not stats:
            self.telegram.send_notification(
                message.chat_id, f"*Today ({today_label})* — nothing captured yet."
            )
            return ProcessingResult(url=None, success=True)

        _LABELS = {
            "note": "Notes saved",
            "todo": "Todos",
            "someday": "Someday",
            "read": "To Be Read",
            "watch": "To Be Watched",
            "fleeting": "Fleeting notes",
            "daily": "Daily note entries",
            "project": "Project entries",
            "event": "Events",
            "other": "Other",
        }
        lines = [f"*Today ({today_label})*\n"]
        for key, label in _LABELS.items():
            count = stats.get(key, 0)
            if count:
                lines.append(f"{label}: {count}")
        self.telegram.send_notification(message.chat_id, "\n".join(lines))
        return ProcessingResult(url=None, success=True)

    def _process_url(self, cmd: ParsedCommand, message: TelegramMessage) -> ProcessingResult:
        """Full bookmark pipeline: scrape → summarise → create note."""
        url = cmd.url
        logger.info(f"Processing URL: {url}")

        # Duplicate detection — skip if already in vault
        existing = self.linker.find_note_by_url(url)
        if existing:
            logger.info(f"Duplicate URL detected: {url} → {existing}")
            if self.config.telegram.send_notifications:
                self.telegram.send_notification(
                    message.chat_id,
                    f"Already in vault: *{existing}*",
                )
            return ProcessingResult(url=url, success=True, title=existing)

        scraper = self._get_scraper(url)
        if not scraper:
            return ProcessingResult(url=url, success=False, error="No scraper available for this URL")

        try:
            scraped = scraper.scrape(url)

            summary = self.summarizer.summarize(scraped, user_notes=cmd.user_notes)
            logger.info(f"AI category: {summary.category}, tags: {summary.tags}")

            if scraped.content_type == "youtube":
                try:
                    questions = self.question_agent.run(summary.summary, scraped.title)
                    if questions:
                        summary.review_questions = questions
                        logger.info(f"Generated {len(questions)} review questions")
                except Exception as e:
                    logger.warning(f"Question generation failed: {e}")

            resolved_tags = self.linker.resolve_tags(summary.tags)
            related_links = self.linker.find_related_notes(summary.key_concepts, summary.tags)
            folder_override = self._get_folder_for_content(scraped, summary)

            file_path, folder = self.note_generator.save_note(
                scraped, summary, cmd.user_notes, related_links, folder_override,
                resolved_tags=resolved_tags,
            )
            logger.info(f"Created: {file_path}")
            self.telegram.log_activity("note")
            return ProcessingResult(url=url, success=True, title=scraped.title, folder=folder)

        except Exception as e:
            logger.exception(f"Error processing {url}")
            return ProcessingResult(url=url, success=False, error=str(e))

    def _process_todo_inbox(self, cmd: ParsedCommand, _: TelegramMessage) -> ProcessingResult:
        """/todo (no date) → 08 Trackers/Inbox.md"""
        try:
            self.vault_writer.append_to_inbox(cmd.text)
            self.telegram.log_activity("todo")
            return ProcessingResult(url=None, success=True, message=f"Added to Inbox: {cmd.text}")
        except Exception as e:
            logger.exception("Error processing todo inbox")
            return ProcessingResult(url=None, success=False, error=str(e))

    def _process_todo_deadline(self, cmd: ParsedCommand, _: TelegramMessage) -> ProcessingResult:
        """/todo <text> @<date> → 08 Trackers/Deadlines.md"""
        try:
            self.vault_writer.append_to_deadlines(cmd.text, cmd.due_date)
            self.telegram.log_activity("todo")
            return ProcessingResult(
                url=None, success=True,
                message=f"Added to Deadlines: {cmd.text} ({cmd.due_date})",
            )
        except Exception as e:
            logger.exception("Error processing todo deadline")
            return ProcessingResult(url=None, success=False, error=str(e))

    def _process_someday(self, cmd: ParsedCommand, _: TelegramMessage) -> ProcessingResult:
        """/someday → 08 Trackers/Someday.md"""
        try:
            self.vault_writer.append_to_someday(cmd.text)
            self.telegram.log_activity("someday")
            return ProcessingResult(url=None, success=True, message=f"Added to Someday: {cmd.text}")
        except Exception as e:
            logger.exception("Error processing someday")
            return ProcessingResult(url=None, success=False, error=str(e))

    def _process_read(self, cmd: ParsedCommand, _: TelegramMessage) -> ProcessingResult:
        """/read → 08 Trackers/To Be Read.md"""
        try:
            self.vault_writer.append_to_read(cmd.text)
            self.telegram.log_activity("read")
            return ProcessingResult(url=None, success=True, message=f"Added to To Be Read: {cmd.text}")
        except Exception as e:
            logger.exception("Error processing read")
            return ProcessingResult(url=None, success=False, error=str(e))

    def _process_watch(self, cmd: ParsedCommand, _: TelegramMessage) -> ProcessingResult:
        """/watch → 08 Trackers/To Be Watched.md"""
        try:
            self.vault_writer.append_to_watch(cmd.text)
            self.telegram.log_activity("watch")
            return ProcessingResult(url=None, success=True, message=f"Added to To Be Watched: {cmd.text}")
        except Exception as e:
            logger.exception("Error processing watch")
            return ProcessingResult(url=None, success=False, error=str(e))

    def _process_fleeting_note(self, cmd: ParsedCommand, _: TelegramMessage) -> ProcessingResult:
        """/note → 01 Fleeting Notes/<auto-title>.md (raw if URL, no AI)"""
        try:
            path = self.vault_writer.create_fleeting_note(
                text=cmd.text,
                source_url=cmd.url if cmd.is_raw else None,
            )
            self.telegram.log_activity("fleeting")
            return ProcessingResult(
                url=cmd.url, success=True,
                title=path.stem,
                folder="01 Fleeting Notes",
            )
        except Exception as e:
            logger.exception("Error creating fleeting note")
            return ProcessingResult(url=None, success=False, error=str(e))

    def _process_daily_note(self, cmd: ParsedCommand, _: TelegramMessage) -> ProcessingResult:
        """/daily <text> → daily note ### Notes"""
        try:
            self.vault_writer.append_to_daily_note(cmd.text)
            self.telegram.log_activity("daily")
            return ProcessingResult(url=None, success=True, message=f"Added to daily note: {cmd.text[:60]}")
        except Exception as e:
            logger.exception("Error processing daily note")
            return ProcessingResult(url=None, success=False, error=str(e))

    def _process_daily_task(self, cmd: ParsedCommand, _: TelegramMessage) -> ProcessingResult:
        """/daily task <text> → daily note ### Tasks"""
        try:
            self.vault_writer.append_task_to_daily_note(cmd.text)
            self.telegram.log_activity("daily")
            return ProcessingResult(url=None, success=True, message=f"Added task to daily note: {cmd.text}")
        except Exception as e:
            logger.exception("Error processing daily task")
            return ProcessingResult(url=None, success=False, error=str(e))

    def _process_project_todo(self, cmd: ParsedCommand, _: TelegramMessage) -> ProcessingResult:
        """/project <name> todo → 07 Projects/<name>/Todo.md"""
        try:
            self.vault_writer.append_to_project_todo(cmd.project, cmd.text, due_date=cmd.due_date)
            self.telegram.log_activity("project")
            return ProcessingResult(
                url=None, success=True,
                message=f"Added todo to {cmd.project}: {cmd.text}",
            )
        except Exception as e:
            logger.exception("Error processing project todo")
            return ProcessingResult(url=None, success=False, error=str(e))

    def _process_project_note(self, cmd: ParsedCommand, _: TelegramMessage) -> ProcessingResult:
        """/project <name> → 07 Projects/<name>/Notes.md"""
        try:
            self.vault_writer.append_to_project_note(cmd.project, cmd.text)
            self.telegram.log_activity("project")
            return ProcessingResult(
                url=None, success=True,
                message=f"Added note to {cmd.project}: {cmd.text[:60]}",
            )
        except Exception as e:
            logger.exception("Error processing project note")
            return ProcessingResult(url=None, success=False, error=str(e))

    def _process_event(self, cmd: ParsedCommand, _: TelegramMessage) -> ProcessingResult:
        """/event → ICS file + daily note entry"""
        try:
            if not cmd.event_datetime:
                return ProcessingResult(
                    url=None, success=False,
                    error="Could not parse event date/time. Use: /event Title YYYY-MM-DD HH:MM",
                )
            ics_path = self.vault_writer.create_event(cmd.text, cmd.event_datetime)
            self.vault_writer.add_event_to_daily_note(cmd.text, cmd.event_datetime)
            self.telegram.log_activity("event")
            msg = f"Event: {cmd.text} at {cmd.event_datetime.strftime('%Y-%m-%d %H:%M')}"
            if ics_path:
                msg += f"\nICS: {ics_path}"
            return ProcessingResult(url=None, success=True, message=msg)
        except Exception as e:
            logger.exception("Error processing event")
            return ProcessingResult(url=None, success=False, error=str(e))

    def _process_question(self, cmd: ParsedCommand, _: TelegramMessage) -> ProcessingResult:
        """Reply to bot message → append question to that note"""
        try:
            found = self.vault_writer.append_question_to_note(cmd.reply_to_title, cmd.text)
            if found:
                return ProcessingResult(
                    url=None, success=True,
                    message=f"Question added to: {cmd.reply_to_title}",
                )
            return ProcessingResult(
                url=None, success=False,
                error=f"Could not find note matching '{cmd.reply_to_title}'",
            )
        except Exception as e:
            logger.exception("Error processing question")
            return ProcessingResult(url=None, success=False, error=str(e))

    def _process_inbox(self, cmd: ParsedCommand, _: TelegramMessage) -> ProcessingResult:
        """Plain text default → 08 Trackers/Inbox.md"""
        try:
            self.vault_writer.append_to_inbox(cmd.text)
            return ProcessingResult(url=None, success=True, message=f"Added to Inbox: {cmd.text[:60]}")
        except Exception as e:
            logger.exception("Error processing inbox")
            return ProcessingResult(url=None, success=False, error=str(e))

    # ── Main entry points ──────────────────────────────────────────────────────

    _HANDLERS = {
        "help": "_process_help",
        "search": "_process_search",
        "status": "_process_status",
        "url": "_process_url",
        "todo_inbox": "_process_todo_inbox",
        "todo_deadline": "_process_todo_deadline",
        "someday": "_process_someday",
        "read": "_process_read",
        "watch": "_process_watch",
        "fleeting_note": "_process_fleeting_note",
        "daily_note": "_process_daily_note",
        "daily_task": "_process_daily_task",
        "project_todo": "_process_project_todo",
        "project_note": "_process_project_note",
        "event": "_process_event",
        "question": "_process_question",
        "inbox": "_process_inbox",
    }

    def process_url(self, url: str, user_notes: str | None = None) -> ProcessingResult:
        """Process a single URL directly (for --test mode)."""
        cmd = ParsedCommand(command_type="url", text=url, url=url, user_notes=user_notes)
        stub = TelegramMessage(update_id=0, message_id=0, chat_id=0, text=url, date=0)
        return self._process_url(cmd, stub)

    def process_message(self, message: TelegramMessage) -> ProcessingResult:
        """Route a Telegram message to the correct handler."""
        cmd = parse_message(message.text, message.reply_to_text)
        logger.info(f"Routing '{message.text[:60]}' → {cmd.command_type}")

        if cmd.command_type == "url" and self.config.telegram.send_notifications:
            self.telegram.send_processing_started(message.chat_id, cmd.url)

        handler_name = self._HANDLERS.get(cmd.command_type, "_process_inbox")
        handler = getattr(self, handler_name)
        result = handler(cmd, message)

        if self.config.telegram.send_notifications:
            if result.success:
                if result.title:
                    self.telegram.send_success(message.chat_id, result.title, result.folder or "")
                elif result.message:
                    self.telegram.send_notification(message.chat_id, result.message)
            else:
                self.telegram.send_error(message.chat_id, message.text[:100], result.error or "Unknown error")

        return result

    def run(self) -> list[ProcessingResult]:
        """Fetch pending Telegram messages and process them."""
        logger.info("Fetching pending messages from Telegram")
        messages = self.telegram.get_pending_messages()

        if not messages:
            logger.info("No pending messages to process")
            return []

        logger.info(f"Found {len(messages)} messages to process")
        results = [self.process_message(m) for m in messages]

        successful = sum(1 for r in results if r.success)
        logger.info(f"Processed {len(results)}: {successful} ok, {len(results) - successful} failed")
        return results
