"""Main processing orchestrator - coordinates scraping, summarization, and note creation."""
import logging
from dataclasses import dataclass

from .config import Config
from .linker import NoteLinkEngine
from .obsidian import ObsidianNoteGenerator
from .scrapers import WebpageScraper, YouTubeScraper
from .summarizer import Summarizer
from .telegram_queue import TelegramMessage, TelegramQueue

logger = logging.getLogger(__name__)


@dataclass
class ProcessingResult:
    """Result of processing a single URL."""

    url: str
    success: bool
    title: str | None = None
    folder: str | None = None
    error: str | None = None


class Processor:
    """Orchestrates the bookmark processing pipeline."""

    def __init__(self, config: Config):
        self.config = config

        # Initialize components
        self.telegram = TelegramQueue(
            bot_token=config.telegram.bot_token,
            allowed_chat_ids=config.telegram.allowed_chat_ids,
        )

        self.scrapers = [
            YouTubeScraper(transcript_languages=config.youtube.transcript_languages),
            WebpageScraper(),  # Fallback scraper - handles any URL
        ]

        self.summarizer = Summarizer(
            provider=config.ai.provider,
            api_key=config.ai.api_key,
            model=config.ai.model,
            max_tokens=config.ai.max_tokens,
            routing_categories=config.get_routing_category_names(),
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

    def _get_scraper(self, url: str):
        """Find appropriate scraper for URL."""
        for scraper in self.scrapers:
            if scraper.can_handle(url):
                return scraper
        return None

    def _get_folder_for_content(self, scraped, summary) -> str | None:
        """Determine output folder based on AI-chosen category.

        For articles, use AI routing. For YouTube, use default video folder.
        Returns None to use default content-type folder.
        """
        # Only apply AI routing for articles
        if scraped.content_type == "youtube":
            return None

        # Look up folder for AI-chosen category
        folder = self.config.get_folder_for_category(summary.category)
        if folder:
            logger.info(f"AI routed to category '{summary.category}' -> {folder}")
            return folder

        return None

    def process_url(self, url: str, user_notes: str | None = None) -> ProcessingResult:
        """Process a single URL through the pipeline."""
        logger.info(f"Processing: {url}")
        if user_notes:
            logger.info(f"User notes: {user_notes}")

        # Find scraper
        scraper = self._get_scraper(url)
        if not scraper:
            return ProcessingResult(
                url=url, success=False, error="No scraper available for this URL"
            )

        try:
            # 1. Scrape content
            logger.info(f"Scraping with {scraper.__class__.__name__}")
            scraped = scraper.scrape(url)

            # 2. Generate structured summary
            logger.info("Generating summary")
            summary = self.summarizer.summarize(scraped, user_notes=user_notes)
            logger.info(f"AI category: {summary.category}, tags: {summary.tags}")

            # 3. Find related notes in vault
            logger.info("Finding related notes")
            related_links = self.linker.find_related_notes(
                summary.key_concepts, summary.tags
            )
            if related_links:
                logger.info(f"Found {len(related_links)} related notes")

            # 4. Determine output folder via routing rules
            folder_override = self._get_folder_for_content(scraped, summary)

            # 5. Create and save note with all sections
            logger.info("Saving note")
            file_path, folder = self.note_generator.save_note(
                scraped,
                summary,
                user_notes,
                related_links,
                folder_override,
            )

            logger.info(f"Created: {file_path}")
            return ProcessingResult(
                url=url, success=True, title=scraped.title, folder=folder
            )

        except Exception as e:
            logger.exception(f"Error processing {url}")
            return ProcessingResult(url=url, success=False, error=str(e))

    def process_message(self, message: TelegramMessage) -> ProcessingResult:
        """Process a Telegram message and send notifications."""
        url = message.url

        # Send "processing" notification if enabled
        if self.config.telegram.send_notifications:
            self.telegram.send_processing_started(message.chat_id, url)

        # Process the URL with optional user notes
        result = self.process_url(url, user_notes=message.user_notes)

        # Send result notification if enabled
        if self.config.telegram.send_notifications:
            if result.success:
                self.telegram.send_success(
                    message.chat_id, result.title or "Untitled", result.folder or ""
                )
            else:
                self.telegram.send_error(message.chat_id, url, result.error or "Unknown error")

        return result

    def run(self) -> list[ProcessingResult]:
        """Main entry point - fetch pending URLs and process them."""
        logger.info("Fetching pending URLs from Telegram")
        messages = self.telegram.get_pending_urls()

        if not messages:
            logger.info("No pending URLs to process")
            return []

        logger.info(f"Found {len(messages)} URLs to process")
        results = []

        for message in messages:
            result = self.process_message(message)
            results.append(result)

        # Log summary
        successful = sum(1 for r in results if r.success)
        failed = len(results) - successful
        logger.info(f"Processed {len(results)} URLs: {successful} successful, {failed} failed")

        return results
