#!/usr/bin/env python3
"""Obsidian Helper - Automated bookmark processing.

This script polls Telegram for bookmark URLs, scrapes content,
generates AI summaries, and creates Obsidian notes.

Usage:
    python main.py              # Run once (for cron)
    python main.py --watch      # Run continuously (watch mode)
    python main.py --test URL   # Test with a specific URL
"""
import argparse
import logging
import sys
import time
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

    # Validate config
    if not config.telegram.bot_token:
        logger.error("TELEGRAM_BOT_TOKEN not set")
        return 1
    if not config.ai.api_key:
        logger.error("GEMINI_API_KEY not set (or ANTHROPIC_API_KEY if using anthropic)")
        return 1

    # Run appropriate mode
    if args.test:
        return test_url(config, args.test)
    elif args.watch:
        run_watch(config, args.interval)
        return 0
    else:
        return run_once(config)


if __name__ == "__main__":
    sys.exit(main())
