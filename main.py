#!/usr/bin/env python3
"""Obsidian Helper - Automated bookmark processing.

This script polls Telegram for bookmark URLs, scrapes content,
generates AI summaries, and creates Obsidian notes.

Usage:
    python main.py                      # Run once (for cron)
    python main.py --watch              # Run continuously (watch mode)
    python main.py --test URL           # Test with a specific URL
    python main.py --crawl CHANNEL_URL  # Crawl a channel (no Telegram)
    python main.py --backfill instagram:saved   # Backfill Instagram saved posts
    python main.py --backfill instagram:likes   # Backfill Instagram liked posts
    python main.py --backfill PLAYLIST_URL      # Backfill any playlist, e.g. YouTube Liked (?list=LL)
"""
import argparse
import logging
import sys
import time
from datetime import datetime
from pathlib import Path

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent))

from src.config import Config
from src.processor import Processor


def setup_logging(verbose: bool = False) -> None:
    """Configure logging."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler("obsidian_helper.log"),
        ],
    )


def run_once(config: Config) -> int:
    """Run the processor once and exit."""
    processor = Processor(config)
    results = processor.run()

    # Return non-zero if any failures
    failed = sum(1 for r in results if not r.success)
    return 1 if failed > 0 else 0


def run_watch(config: Config, interval: int = 60) -> None:
    """Run the processor continuously."""
    processor = Processor(config)
    logger = logging.getLogger(__name__)

    logger.info(f"Starting watch mode (polling every {interval}s)")
    logger.info("Press Ctrl+C to stop")

    try:
        while True:
            processor.run()
            time.sleep(interval)
    except KeyboardInterrupt:
        logger.info("Stopping watch mode")


def test_url(config: Config, url: str) -> int:
    """Test processing a specific URL."""
    processor = Processor(config)
    result = processor.process_url(url)

    if result.success:
        print(f"Success! Created note: {result.title}")
        print(f"Saved to: {result.folder}")
        return 0
    else:
        print(f"Failed: {result.error}")
        return 1


def _parse_iso_date(raw: str | None):
    if not raw:
        return None
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date()
    except ValueError as e:
        raise SystemExit(f"Invalid date {raw!r}: expected YYYY-MM-DD") from e


def _stdout_progress(done: int, total: int, current: str | None) -> None:
    bar_width = 30
    ratio = (done / total) if total else 0
    filled = int(ratio * bar_width)
    bar = "█" * filled + "░" * (bar_width - filled)
    suffix = f" — {current[:60]}" if current else ""
    # \r to overwrite the line; \n only at completion.
    end = "\n" if done == total else ""
    sys.stdout.write(f"\r[{bar}] {done}/{total}{suffix}{end}")
    sys.stdout.flush()


def run_crawl(
    config: Config,
    url: str,
    crawl_from: str | None,
    crawl_to: str | None,
    crawl_topic: str | None,
    no_resume: bool,
) -> int:
    """CLI-driven channel crawl with stdout progress bar."""
    processor = Processor(config)
    result = processor.run_channel_crawl(
        url=url,
        date_from=_parse_iso_date(crawl_from),
        date_to=_parse_iso_date(crawl_to),
        topic=crawl_topic,
        progress_callback=_stdout_progress,
        resume=not no_resume,
    )
    print()  # newline after the bar
    if result.success:
        print(result.message)
        return 0
    print(f"Failed: {result.error}")
    return 1


def run_backfill(
    config: Config,
    source: str,
    crawl_from: str | None,
    crawl_to: str | None,
    crawl_topic: str | None,
    no_resume: bool,
    max_items: int | None,
) -> int:
    """CLI-driven backfill (Instagram export or a YouTube playlist URL, e.g.
    Liked = ?list=LL) — mirrors run_crawl's idiom, same progress bar."""
    processor = Processor(config)
    result = processor.run_backfill(
        source=source,
        date_from=_parse_iso_date(crawl_from),
        date_to=_parse_iso_date(crawl_to),
        topic=crawl_topic,
        progress_callback=_stdout_progress,
        resume=not no_resume,
        max_items=max_items,
    )
    print()
    if result.success:
        print(result.message)
        return 0
    print(f"Failed: {result.error}")
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Obsidian Helper - Automated bookmark processing"
    )
    parser.add_argument(
        "--config",
        default="config.yaml",
        help="Path to config file (default: config.yaml)",
    )
    parser.add_argument(
        "--watch",
        action="store_true",
        help="Run continuously instead of once",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=60,
        help="Polling interval in seconds for watch mode (default: 60)",
    )
    parser.add_argument(
        "--test",
        metavar="URL",
        help="Test processing a specific URL",
    )
    parser.add_argument(
        "--crawl",
        metavar="CHANNEL_URL",
        help="Crawl a YouTube channel's videos (no Telegram needed)",
    )
    parser.add_argument(
        "--crawl-from",
        metavar="YYYY-MM-DD",
        help="Earliest upload date for --crawl",
    )
    parser.add_argument(
        "--crawl-to",
        metavar="YYYY-MM-DD",
        help="Latest upload date for --crawl",
    )
    parser.add_argument(
        "--crawl-topic",
        metavar="TOPIC",
        help="Topic filter for --crawl/--backfill (LLM filters titles/transcripts/metadata). "
             "--backfill defaults this to the configured `interests` when omitted.",
    )
    parser.add_argument(
        "--backfill",
        metavar="SOURCE",
        help='Backfill a saved/liked history: "instagram:saved", "instagram:likes", '
             "or a playlist URL (e.g. YouTube Liked = ?list=LL)",
    )
    parser.add_argument(
        "--backfill-max-items",
        type=int,
        metavar="N",
        help="Override backfill.max_items for this run",
    )
    parser.add_argument(
        "--no-resume",
        action="store_true",
        help="Ignore any saved crawl state and start fresh",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable verbose logging",
    )

    args = parser.parse_args()
    setup_logging(args.verbose)
    logger = logging.getLogger(__name__)

    # Load config
    try:
        config = Config.load(args.config)
        logger.info(f"Loaded config from {args.config}")
    except Exception as e:
        logger.error(f"Failed to load config: {e}")
        return 1

    # Validate config — Telegram only needed for poll/watch modes.
    needs_telegram = not (args.test or args.crawl or args.backfill)
    if needs_telegram and not config.telegram.bot_token:
        logger.error("TELEGRAM_BOT_TOKEN not set")
        return 1
    if not config.ai.api_key:
        logger.error("GEMINI_API_KEY not set (or ANTHROPIC_API_KEY if using anthropic)")
        return 1

    # Run appropriate mode
    if args.crawl:
        return run_crawl(
            config, args.crawl, args.crawl_from, args.crawl_to,
            args.crawl_topic, args.no_resume,
        )
    if args.backfill:
        return run_backfill(
            config, args.backfill, args.crawl_from, args.crawl_to,
            args.crawl_topic, args.no_resume, args.backfill_max_items,
        )
    if args.test:
        return test_url(config, args.test)
    elif args.watch:
        run_watch(config, args.interval)
        return 0
    else:
        return run_once(config)


if __name__ == "__main__":
    sys.exit(main())
