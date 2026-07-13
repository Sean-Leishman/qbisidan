"""Main processing orchestrator."""
import logging
import re
import time
from dataclasses import dataclass

from .agents.question_agent import QuestionAgent
from .agents.topic_filter import TopicFilterAgent
from .agents.vault_writer import VaultWriter
from .commands import ParsedCommand, parse_message
from .config import Config
from .crawl_state import CrawlRunState, CrawlStateStore
from .linker import NoteLinkEngine
from .obsidian import ObsidianNoteGenerator
from .scrapers import ChannelEnumerator, InstagramScraper, WebpageScraper, YouTubeScraper
from .scrapers.channel import StaleCookiesError
from .scrapers.instagram_export import ExportNotFoundError, load_instagram_export
from .search import VaultSearch, format_search_results
from .summarizer import Summarizer
from .telegram_queue import TelegramMessage, TelegramQueue

logger = logging.getLogger(__name__)

# Model-choice manifest: cycling order for a single video's override button,
# and for the playlist-wide default-model button (no skip/default there).
_MODEL_CYCLE = ["default", "gemini", "groq", "claude", "skip"]
_DEFAULT_CYCLE = ["gemini", "groq", "claude"]
_MODEL_LABELS = {"default": "default", "gemini": "Gemini", "groq": "Groq", "claude": "Claude", "skip": "skip"}
# Telegram caps inline keyboards at 100 buttons. Reserve one for the default-model
# row and two for Start/Cancel.
_MANIFEST_MAX_VIDEOS = 97


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
            YouTubeScraper(
                transcript_languages=config.youtube.transcript_languages,
                cookies_from_browser=config.youtube.cookies_from_browser,
                cookies_file=config.youtube.cookies_file,
            ),
            InstagramScraper(
                gemini_api_key=config.ai.gemini_api_key,
                video_model=config.instagram.video_model,
                cookies_file=config.instagram.cookies_file,
                max_duration_seconds=config.instagram.max_duration_seconds,
            ),
            # WebpageScraper.can_handle() is a catch-all for any http(s) URL —
            # it MUST stay last, or it would "handle" youtube/instagram URLs
            # itself and produce garbage.
            WebpageScraper(),
        ]
        self.summarizer = Summarizer(
            provider=config.ai.provider,
            api_key=config.ai.api_key,
            model=config.ai.model,
            max_tokens=config.ai.max_tokens,
            routing_categories=config.get_routing_category_names(),
        )
        # Per-provider summarizers, for the crawl manifest's per-video model
        # overrides. Seeded with the one built above so the config-default
        # provider never gets built twice.
        self._summarizers: dict[str, Summarizer] = {config.ai.provider: self.summarizer}
        self.question_agent = QuestionAgent(
            provider=config.ai.provider,
            api_key=config.ai.api_key,
            model=config.ai.model,
            max_tokens=512,
        )
        self.topic_filter = TopicFilterAgent(
            provider=config.ai.provider,
            api_key=config.ai.api_key,
            model=config.ai.model,
            max_tokens=512,
        )
        self.channel_enumerator = ChannelEnumerator(
            cookies_from_browser=config.youtube.cookies_from_browser,
            cookies_file=config.youtube.cookies_file,
        )
        self.crawl_state = CrawlStateStore(config.channel_crawl.state_file)
        self.linker = NoteLinkEngine(vault_path=config.vault_path)
        self.note_generator = ObsidianNoteGenerator(
            vault_path=config.vault_path,
            output_folders={
                "youtube": config.output_folders.youtube,
                "article": config.output_folders.article,
                "instagram": config.output_folders.instagram,
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
        # youtube and instagram both have a fixed content-type folder
        # (output_folders.youtube / .instagram) rather than an AI-routed
        # category subfolder — returning None here means save_note falls back
        # to ObsidianNoteGenerator._get_output_folder(content_type), which is
        # exactly that fixed folder. Reels get the same treatment as videos:
        # "what kind of source is this" beats "what category does the AI think
        # this is" for a reel same as it does for a YouTube video.
        if scraped.content_type in ("youtube", "instagram"):
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
        "*Channel crawl*\n"
        "`/crawl <channel_url> [from:YYYY-MM-DD] [to:YYYY-MM-DD] [topic:\"...\"]`\n"
        "_Shows a manifest to pick a default model and override a few videos —_\n"
        "_tap Start to run, Cancel to abort, or leave it — it proceeds on the default model after a timeout._\n\n"
        "*Backfill*\n"
        "`/backfill instagram saved [from:YYYY-MM-DD] [to:YYYY-MM-DD]`\n"
        "`/backfill instagram likes [from:YYYY-MM-DD] [to:YYYY-MM-DD]`\n"
        "`/backfill <playlist_url>` — e.g. YouTube Liked = `?list=LL`\n"
        "_Filters down to your configured `interests` by default; runs in chunks_\n"
        "_(`backfill.max_items` per run) since Instagram rate-limits hard — just re-run to continue._\n\n"
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

    def _summarizer_for(self, provider: str) -> Summarizer:
        """Cached per-provider Summarizer, so the crawl manifest's model overrides
        don't rebuild an AI client for every video."""
        cached = self._summarizers.get(provider)
        if cached is None:
            cached = Summarizer(
                provider=provider,
                api_key=self.config.ai.key_for(provider),
                model=self.config.ai.model_for(provider),
                max_tokens=self.config.ai.max_tokens,
                routing_categories=self.config.get_routing_category_names(),
            )
            self._summarizers[provider] = cached
        return cached

    def _save_scraped(
        self, scraped, user_notes: str | None, provider: str | None = None
    ) -> ProcessingResult:
        """Summarise pre-scraped content and write the vault note. Shared by URL + crawl paths.

        `provider` overrides the configured AI provider for this one item (crawl
        manifest per-video choice); None uses the config default.
        """
        url = scraped.url
        try:
            summarizer = self._summarizer_for(provider) if provider else self.summarizer
            summary = summarizer.summarize(scraped, user_notes=user_notes)
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
                scraped, summary, user_notes, related_links, folder_override,
                resolved_tags=resolved_tags,
            )
            logger.info(f"Created: {file_path}")
            self.telegram.log_activity("note")
            return ProcessingResult(url=url, success=True, title=scraped.title, folder=folder)
        except Exception as e:
            logger.exception(f"Error saving scraped content for {url}")
            return ProcessingResult(url=url, success=False, error=str(e))

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
        except Exception as e:
            logger.exception(f"Error scraping {url}")
            return ProcessingResult(url=url, success=False, error=str(e))

        return self._save_scraped(scraped, cmd.user_notes)

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

    # ── Channel crawl ──────────────────────────────────────────────────────────

    def _channel_name_from_url(self, url: str) -> str:
        match = re.search(r"youtube\.com/(@[\w.-]+|c/[\w.-]+|channel/[\w.-]+|user/[\w.-]+)", url)
        if match:
            return match.group(1).lstrip("@").replace("c/", "").replace("channel/", "").replace("user/", "")
        return "channel"

    def _render_progress_bar(self, channel: str, done: int, total: int, current: str | None) -> str:
        bar_width = 20
        ratio = (done / total) if total else 0
        filled = int(ratio * bar_width)
        bar = "█" * filled + "░" * (bar_width - filled)
        line = f"*Crawling {channel}*\n`[{bar}]` {done}/{total}"
        if current:
            line += f"\nCurrent: {current[:60]}"
        return line

    # ── Model-choice manifest ────────────────────────────────────────────────

    def _build_manifest_keyboard(
        self, stubs: list, default_provider: str, choices: dict[str, str]
    ) -> list[list[dict]]:
        rows = [[{
            "text": f"Default model: [{_MODEL_LABELS[default_provider]} ▾]",
            "callback_data": "d",
        }]]
        for idx, stub in enumerate(stubs):
            choice = choices.get(stub.video_id, "default")
            # callback_data is capped at 64 bytes by Telegram — index only, never the title.
            rows.append([{
                "text": f"{idx + 1}. {stub.title[:40]}  [{_MODEL_LABELS[choice]}]",
                "callback_data": f"v{idx}",
            }])
        rows.append([
            {"text": "Start", "callback_data": "start"},
            {"text": "Cancel", "callback_data": "cancel"},
        ])
        return rows

    def _run_model_manifest(
        self, chat_id: int, display_name: str, stubs: list, timeout: float
    ) -> tuple[str, dict[str, str]] | None:
        """Show one editable manifest message; block until Start, Cancel, or timeout.

        Returns (default_provider, {video_id: choice}) after Start — choice is one
        of gemini/groq/claude/skip for videos the user touched, "default" videos are
        simply absent. Returns None if the user cancelled (crawl must abort, write
        nothing). On timeout, returns (config default provider, {}) — untapped taps
        before a timeout are discarded; only Start commits them.
        """
        config_default = self.config.ai.provider if self.config.ai.provider in _DEFAULT_CYCLE else "gemini"

        manifest_stubs = stubs
        if len(stubs) > _MANIFEST_MAX_VIDEOS:
            logger.warning(
                f"Manifest truncated to {_MANIFEST_MAX_VIDEOS} of {len(stubs)} videos "
                "(Telegram's 100-button inline keyboard cap); the rest run on the default model"
            )
            manifest_stubs = stubs[:_MANIFEST_MAX_VIDEOS]

        default_provider = config_default
        choices: dict[str, str] = {}
        text = f'*{display_name}* — {len(stubs)} videos\nTap a video to override its model, then *Start*.'
        keyboard = self._build_manifest_keyboard(manifest_stubs, default_provider, choices)
        message_id = self.telegram.send_buttons(chat_id, text, keyboard)
        if message_id is None:
            logger.warning("Could not show crawl manifest; proceeding on config default")
            return config_default, {}

        started = False
        for data in self.telegram.wait_for_callback(chat_id, message_id, timeout):
            if data == "start":
                started = True
                break
            if data == "cancel":
                return None
            if data == "d":
                i = _DEFAULT_CYCLE.index(default_provider)
                default_provider = _DEFAULT_CYCLE[(i + 1) % len(_DEFAULT_CYCLE)]
            elif data.startswith("v"):
                try:
                    stub = manifest_stubs[int(data[1:])]
                except (ValueError, IndexError):
                    continue
                current = choices.get(stub.video_id, "default")
                nxt = _MODEL_CYCLE[(_MODEL_CYCLE.index(current) + 1) % len(_MODEL_CYCLE)]
                if nxt == "default":
                    choices.pop(stub.video_id, None)
                else:
                    choices[stub.video_id] = nxt
            else:
                continue
            keyboard = self._build_manifest_keyboard(manifest_stubs, default_provider, choices)
            self.telegram.edit_message_reply_markup(chat_id, message_id, keyboard)

        if not started:
            logger.info("Crawl manifest timed out with no Start tap; proceeding on config default")
            return config_default, {}
        return default_provider, choices

    def run_crawl(
        self,
        stubs: list,
        state_key: str,
        display_name: str,
        topic: str | None = None,
        chat_id: int | None = None,
        progress_callback=None,
        resume: bool = True,
        date_from=None,
        date_to=None,
        max_items: int | None = None,
    ) -> ProcessingResult:
        """Generic crawl engine: topic-filter -> dedupe vs vault -> scrape ->
        summarise -> write note -> resumable state -> index note.

        Shared by every batch-ingest entry point (`/crawl` channels and
        playlists, `/backfill` Instagram exports and YouTube Liked/Watch
        Later) — the caller's only job is to hand it a pre-built list of
        stubs (VideoStub: video_id/url/title/upload_date) plus a stable
        `state_key` for resume and a human-readable `display_name` for the
        manifest/index/summary text. This method has no idea where the stubs
        came from.

        date_from/date_to are only used for the index note's metadata line —
        stubs are assumed to already be date-filtered by whoever built them.

        progress_callback(done, total, current_title) is called before each
        item. If None, no progress is reported.

        chat_id, when set, shows the Telegram model-choice manifest before
        running and blocks for it — only the Telegram entry points pass this.
        The CLI paths leave it None and always run on the configured default
        model.
        """
        cfg = self.config.channel_crawl
        if not stubs:
            return ProcessingResult(url=None, success=False, error=f"No items found for {display_name}")

        state = self.crawl_state.load(state_key) if resume else CrawlRunState()
        processed = set(state.processed_ids)
        filtered_out = set(state.filtered_out_ids)

        # Stage 1: bulk title filter — only meaningful when stubs carry real
        # titles (YouTube titles). Enumerators whose scraper offers a cheap
        # per-item metadata path (Instagram) skip this: their stub titles are
        # placeholders (no caption at enumeration time — see instagram_export),
        # so there's nothing here for the LLM to vote on. The per-item
        # cheap-metadata filter below is the equivalent stage for those.
        scraper_for_stubs = self._get_scraper(stubs[0].url)
        has_cheap_metadata = bool(scraper_for_stubs and hasattr(scraper_for_stubs, "get_metadata"))
        if topic and not has_cheap_metadata and len(stubs) > cfg.title_filter_threshold:
            titles = [s.title for s in stubs]
            keep_ids = self.topic_filter.filter_titles(titles, topic)
            stubs = [stubs[i] for i in keep_ids]
            logger.info(f"Title filter retained {len(stubs)} of {len(titles)} items")

        cap = max_items if max_items is not None else cfg.max_videos
        capped_count = 0
        if len(stubs) > cap:
            capped_count = len(stubs) - cap
            # No silent truncation: logged here AND surfaced in the final
            # summary message below, so a capped backfill visibly says so
            # rather than quietly looking "done".
            logger.warning(f"Capping crawl at {cap} of {len(stubs)} items — {capped_count} not attempted this run")
            stubs = stubs[:cap]

        default_provider = None
        overrides: dict[str, str] = {}
        if chat_id is not None:
            manifest = self._run_model_manifest(chat_id, display_name, stubs, cfg.manifest_timeout_seconds)
            if manifest is None:
                logger.info(f"Crawl cancelled via manifest for {display_name}")
                return ProcessingResult(
                    url=None, success=True,
                    message=f"Crawl cancelled for {display_name}. Nothing written.",
                )
            default_provider, overrides = manifest

        total = len(stubs)
        for idx, stub in enumerate(stubs, start=1):
            if progress_callback:
                try:
                    progress_callback(idx - 1, total, stub.title)
                except Exception:
                    logger.exception("Progress callback raised; continuing crawl")

            if stub.video_id in processed or stub.video_id in filtered_out:
                logger.info(f"Resume: already handled {stub.video_id} ({stub.title})")
                continue

            existing = self.linker.find_note_by_url(stub.url)
            if existing:
                state.skipped_titles.append(existing)
                state.processed_ids.append(stub.video_id)
                self.crawl_state.save(state_key, state)
                continue

            choice = overrides.get(stub.video_id, "default")
            if choice == "skip":
                logger.info(f"Manifest skip: {stub.title}")
                state.filtered_out_ids.append(stub.video_id)
                self.crawl_state.save(state_key, state)
                continue
            chosen_provider = default_provider if choice == "default" else choice

            scraper = self._get_scraper(stub.url)
            if not scraper:
                state.errors.append(f"{stub.title}: no scraper")
                state.processed_ids.append(stub.video_id)
                self.crawl_state.save(state_key, state)
                continue

            # Stage 2, cheap path: when the scraper offers get_metadata (a
            # metadata-only fetch — no download, no Gemini call), filter on
            # THAT before ever calling scraper.scrape(). This is the ordering
            # that matters for Instagram: scrape() there means download +
            # Gemini video understanding, so an item the interest filter would
            # reject must never reach it. YouTube's scraper has no
            # get_metadata, so it falls through to the stage-2b post-scrape
            # filter unchanged.
            used_cheap_filter = False
            if topic and hasattr(scraper, "get_metadata"):
                try:
                    metadata = scraper.get_metadata(stub.url)
                except Exception as e:
                    logger.warning(
                        f"Cheap metadata fetch failed for {stub.url}, falling back to post-scrape filter: {e}"
                    )
                    metadata = None
                if metadata is not None:
                    used_cheap_filter = True
                    cheap_title = metadata.get("title") or stub.title
                    excerpt = (metadata.get("caption") or metadata.get("description") or "")[
                        : cfg.transcript_filter_chars
                    ]
                    if not self.topic_filter.is_relevant(cheap_title, excerpt, topic):
                        logger.info(f"Cheap-metadata filter dropped: {cheap_title}")
                        state.filtered_out_ids.append(stub.video_id)
                        self.crawl_state.save(state_key, state)
                        if cfg.sleep_seconds:
                            time.sleep(cfg.sleep_seconds)
                        continue

            try:
                scraped = scraper.scrape(stub.url)
            except Exception as e:
                logger.exception(f"Scrape failed for {stub.url}")
                state.errors.append(f"{stub.title}: scrape failed: {e}")
                # Do NOT mark as processed — resume should retry transient failures
                # (bot detection, network blips). Persistent failures (deleted video)
                # will retry too; use --no-resume to start fresh.
                self.crawl_state.save(state_key, state)
                if cfg.sleep_seconds:
                    time.sleep(cfg.sleep_seconds)
                continue

            # Stage 2b: post-scrape transcript-level filter — only for
            # scrapers that had no cheap path (already filtered above if so).
            if topic and not used_cheap_filter:
                excerpt_parts = [scraped.description or "", scraped.content or ""]
                excerpt = "\n".join(p for p in excerpt_parts if p)[: cfg.transcript_filter_chars]
                if not self.topic_filter.is_relevant(scraped.title, excerpt, topic):
                    logger.info(f"Transcript filter dropped: {scraped.title}")
                    state.filtered_out_ids.append(stub.video_id)
                    self.crawl_state.save(state_key, state)
                    if cfg.sleep_seconds:
                        time.sleep(cfg.sleep_seconds)
                    continue

            result = self._save_scraped(scraped, user_notes=None, provider=chosen_provider)
            if result.success and result.title:
                state.created_titles.append(result.title)
            elif not result.success:
                state.errors.append(f"{stub.title}: {result.error}")
                logger.warning(f"Crawl item failed ({stub.url}): {result.error}")
            state.processed_ids.append(stub.video_id)
            self.crawl_state.save(state_key, state)

            if cfg.sleep_seconds:
                time.sleep(cfg.sleep_seconds)

        if progress_callback:
            try:
                progress_callback(total, total, None)
            except Exception:
                logger.exception("Progress callback raised at completion")

        index_path = self.vault_writer.write_channel_index(
            channel_name=display_name,
            date_from=date_from,
            date_to=date_to,
            topic=topic,
            note_titles=state.created_titles,
            skipped=state.skipped_titles,
        )
        summary = (
            f"Crawl done for {display_name}: {len(state.created_titles)} new, "
            f"{len(state.skipped_titles)} skipped, "
            f"{len(state.filtered_out_ids)} filtered out, "
            f"{len(state.errors)} failed.\nIndex: `{index_path.name}`"
        )
        if capped_count:
            summary += f"\nCapped at {cap} items this run — {capped_count} more remain; resume to continue."
        # Clear state once the run completed end-to-end.
        self.crawl_state.clear(state_key)
        return ProcessingResult(url=None, success=True, message=summary)

    def run_channel_crawl(
        self,
        url: str,
        date_from=None,
        date_to=None,
        topic: str | None = None,
        progress_callback=None,
        resume: bool = True,
        chat_id: int | None = None,
    ) -> ProcessingResult:
        """/crawl <channel_url> ... — thin wrapper over run_crawl. Enumerates a
        YouTube channel or playlist via ChannelEnumerator and hands the stubs
        to the generic engine. Kept as its own method, with this exact
        signature, because both main.py's --crawl CLI flag and Telegram's
        /crawl command call it directly.
        """
        if not url:
            return ProcessingResult(
                url=None, success=False,
                error="Usage: /crawl <channel_url> [from:YYYY-MM-DD] [to:YYYY-MM-DD] [topic:\"...\"]",
            )

        channel = self._channel_name_from_url(url)
        state_key = CrawlStateStore.make_key(
            channel,
            date_from.isoformat() if date_from else "all",
            date_to.isoformat() if date_to else "all",
            topic,
        )

        try:
            stubs = self.channel_enumerator.list_videos(url, date_from=date_from, date_to=date_to)
        except StaleCookiesError as e:
            return ProcessingResult(url=url, success=False, error=str(e))
        if not stubs:
            return ProcessingResult(
                url=url, success=False,
                error=f"No regular videos found on {url} in given range",
            )

        display_name = self.channel_enumerator.last_title or channel
        result = self.run_crawl(
            stubs=stubs,
            state_key=state_key,
            display_name=display_name,
            topic=topic,
            chat_id=chat_id,
            progress_callback=progress_callback,
            resume=resume,
            date_from=date_from,
            date_to=date_to,
        )
        if result.url is None:
            result.url = url
        return result

    def _process_channel_crawl(self, cmd: ParsedCommand, message: TelegramMessage) -> ProcessingResult:
        """/crawl <url> ... → batch-bookmark a channel (Telegram entry point)."""
        channel = self._channel_name_from_url(cmd.url) if cmd.url else "channel"
        notify = self.config.telegram.send_notifications and bool(cmd.url)
        progress_id: int | None = None

        def callback(done: int, total: int, current: str | None) -> None:
            nonlocal progress_id
            if not notify:
                return
            text = self._render_progress_bar(channel, done, total, current)
            if progress_id is None:
                progress_id = self.telegram.send_message_get_id(message.chat_id, text)
            else:
                self.telegram.edit_message(message.chat_id, progress_id, text)

        return self.run_channel_crawl(
            url=cmd.url,
            date_from=cmd.crawl_from,
            date_to=cmd.crawl_to,
            topic=cmd.crawl_topic,
            progress_callback=callback if notify else None,
            chat_id=message.chat_id if notify else None,
        )

    # ── Backfill ────────────────────────────────────────────────────────────────

    def run_backfill(
        self,
        source: str,
        date_from=None,
        date_to=None,
        topic: str | None = None,
        progress_callback=None,
        resume: bool = True,
        chat_id: int | None = None,
        max_items: int | None = None,
    ) -> ProcessingResult:
        """Backfill a large saved/liked history through the same run_crawl
        engine /crawl uses.

        `source` is either "instagram:saved", "instagram:likes", or any URL
        ChannelEnumerator can enumerate (a playlist works exactly like /crawl's
        channel URLs — YouTube Liked is `?list=LL`, Watch Later is `?list=WL`).

        `topic` defaults to the rendered `interests` config when not given, so
        a plain backfill with no explicit topic still filters down to what the
        user actually cares about.
        """
        topic = topic or self.config.interests.render()

        if source.startswith("instagram:"):
            kind = source.split(":", 1)[1]
            if kind not in ("saved", "likes"):
                return ProcessingResult(
                    url=None, success=False,
                    error=f"Unknown Instagram backfill kind {kind!r}; use 'saved' or 'likes'",
                )
            export_dir = self.config.instagram.export_dir
            if not export_dir:
                return ProcessingResult(
                    url=None, success=False,
                    error="instagram.export_dir not configured in config.yaml",
                )
            try:
                stubs = load_instagram_export(export_dir, kind, date_from, date_to)
            except ExportNotFoundError as e:
                return ProcessingResult(url=None, success=False, error=str(e))
            display_name = f"Instagram {kind}"
            state_key = CrawlStateStore.make_key(
                f"instagram-{kind}",
                date_from.isoformat() if date_from else "all",
                date_to.isoformat() if date_to else "all",
                topic,
            )
        else:
            try:
                stubs = self.channel_enumerator.list_videos(source, date_from=date_from, date_to=date_to)
            except StaleCookiesError as e:
                return ProcessingResult(url=source, success=False, error=str(e))
            display_name = self.channel_enumerator.last_title or self._channel_name_from_url(source)
            state_key = CrawlStateStore.make_key(
                self._channel_name_from_url(source),
                date_from.isoformat() if date_from else "all",
                date_to.isoformat() if date_to else "all",
                topic,
            )

        if not stubs:
            return ProcessingResult(url=None, success=False, error=f"No items found for {display_name}")

        result = self.run_crawl(
            stubs=stubs,
            state_key=state_key,
            display_name=display_name,
            topic=topic,
            chat_id=chat_id,
            progress_callback=progress_callback,
            resume=resume,
            date_from=date_from,
            date_to=date_to,
            max_items=max_items if max_items is not None else self.config.backfill.max_items,
        )
        if result.url is None:
            result.url = source
        return result

    def _process_backfill(self, cmd: ParsedCommand, message: TelegramMessage) -> ProcessingResult:
        """/backfill instagram [saved|likes] [from:...] [to:...] [topic:"..."]
        /backfill <playlist-url> [from:...] [to:...] [topic:"..."]
        (Telegram entry point)."""
        source = f"instagram:{cmd.backfill_kind}" if cmd.backfill_kind else cmd.url
        if not source:
            return ProcessingResult(
                url=None, success=False,
                error='Usage: /backfill instagram [saved|likes] [from:YYYY-MM-DD] [to:YYYY-MM-DD]  '
                      "or  /backfill <playlist-url>",
            )

        label = f"Instagram {cmd.backfill_kind}" if cmd.backfill_kind else self._channel_name_from_url(cmd.url)
        notify = self.config.telegram.send_notifications
        progress_id: int | None = None

        def callback(done: int, total: int, current: str | None) -> None:
            nonlocal progress_id
            if not notify:
                return
            text = self._render_progress_bar(label, done, total, current)
            if progress_id is None:
                progress_id = self.telegram.send_message_get_id(message.chat_id, text)
            else:
                self.telegram.edit_message(message.chat_id, progress_id, text)

        return self.run_backfill(
            source=source,
            date_from=cmd.crawl_from,
            date_to=cmd.crawl_to,
            topic=cmd.crawl_topic,
            progress_callback=callback if notify else None,
            chat_id=message.chat_id if notify else None,
        )

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
        "channel_crawl": "_process_channel_crawl",
        "backfill": "_process_backfill",
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
