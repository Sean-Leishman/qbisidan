"""Configuration loading and management."""
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class TelegramConfig:
    bot_token: str
    allowed_chat_ids: list[int]
    send_notifications: bool = True


@dataclass
class AIConfig:
    provider: str  # "groq", "gemini", or "anthropic"
    groq_api_key: str
    groq_model: str
    gemini_api_key: str
    gemini_model: str
    anthropic_api_key: str
    anthropic_model: str
    max_tokens: int = 1024

    def key_for(self, provider: str) -> str:
        if provider == "groq":
            return self.groq_api_key
        if provider == "gemini":
            return self.gemini_api_key
        return self.anthropic_api_key

    def model_for(self, provider: str) -> str:
        if provider == "groq":
            return self.groq_model
        if provider == "gemini":
            return self.gemini_model
        return self.anthropic_model

    @property
    def api_key(self) -> str:
        return self.key_for(self.provider)

    @property
    def model(self) -> str:
        return self.model_for(self.provider)


@dataclass
class OutputFoldersConfig:
    youtube: str = "02 Sources/Videos"
    article: str = "02 Sources/Articles"
    default: str = "Clippings"


@dataclass
class YouTubeConfig:
    fetch_transcript: bool = True
    transcript_languages: list[str] = field(
        default_factory=lambda: ["en", "en-US", "en-GB"]
    )
    # Pass a browser name (e.g. "chrome", "firefox", "edge") to yt-dlp so it can
    # reuse your logged-in cookies. Avoids the "Sign in to confirm you're not a bot"
    # error that hits during heavy crawls. Only works when the browser profile is
    # readable from this machine — in WSL pointing at Windows Chrome won't decrypt
    # cookies; use `cookies_file` instead.
    cookies_from_browser: str | None = None
    # Path to a Netscape-format cookies.txt file (export with a "Get cookies.txt"
    # browser extension while logged into YouTube). Takes precedence over
    # cookies_from_browser when both are set.
    cookies_file: str | None = None


@dataclass
class RoutingCategory:
    """A category for AI-powered content routing."""

    name: str
    folder: str


@dataclass
class VaultWriterConfig:
    """Configuration for vault file writing (daily notes, events)."""

    daily_notes_folder: str = "06 Daily Notes"
    ics_folder: str = ""


@dataclass
class ChannelCrawlConfig:
    """Configuration for /crawl <channel_url> command."""

    max_videos: int = 25  # cap on videos actually processed per crawl
    title_filter_threshold: int = 25  # invoke LLM title filter only over this many
    sleep_seconds: float = 2.0  # between videos, to be polite to AI provider
    transcript_filter_chars: int = 1500  # chars of description+transcript used in stage-2 filter
    state_file: str = "data/crawl_state.json"
    manifest_timeout_seconds: float = 120.0  # how long the model-choice manifest waits for Start/Cancel


@dataclass
class Config:
    vault_path: Path
    telegram: TelegramConfig
    ai: AIConfig
    output_folders: OutputFoldersConfig
    youtube: YouTubeConfig
    vault_writer: VaultWriterConfig = field(default_factory=VaultWriterConfig)
    channel_crawl: ChannelCrawlConfig = field(default_factory=ChannelCrawlConfig)
    routing_categories: list[RoutingCategory] = field(default_factory=list)

    def get_routing_category_names(self) -> list[str]:
        """Get list of category names for AI prompt."""
        return [cat.name for cat in self.routing_categories]

    def get_folder_for_category(self, category_name: str) -> str | None:
        """Look up folder for a category name."""
        for cat in self.routing_categories:
            if cat.name.lower() == category_name.lower():
                return cat.folder
        return None

    @classmethod
    def load(cls, config_path: str = "config.yaml") -> "Config":
        """Load configuration from YAML file, substituting environment variables."""
        with open(config_path, "r") as f:
            raw = f.read()

        # Substitute ${VAR} patterns with environment variables
        def replace_env(match: re.Match) -> str:
            var_name = match.group(1)
            value = os.environ.get(var_name, "")
            if not value:
                print(f"Warning: Environment variable {var_name} not set")
            return value

        raw = re.sub(r"\$\{(\w+)\}", replace_env, raw)
        data = yaml.safe_load(raw)

        return cls(
            vault_path=Path(data["vault_path"]),
            telegram=TelegramConfig(
                bot_token=data["telegram"]["bot_token"],
                allowed_chat_ids=data["telegram"].get("allowed_chat_ids", []),
                send_notifications=data["telegram"].get("send_notifications", True),
            ),
            ai=AIConfig(
                provider=data["ai"].get("provider", "groq"),
                groq_api_key=data["ai"].get("groq_api_key", ""),
                groq_model=data["ai"].get("groq_model", "llama-3.3-70b-versatile"),
                gemini_api_key=data["ai"].get("gemini_api_key", ""),
                gemini_model=data["ai"].get("gemini_model", "gemini-2.0-flash-lite"),
                anthropic_api_key=data["ai"].get("anthropic_api_key", ""),
                anthropic_model=data["ai"].get("anthropic_model", "claude-sonnet-4-20250514"),
                max_tokens=data["ai"].get("max_tokens", 1024),
            ),
            output_folders=OutputFoldersConfig(
                youtube=data["output_folders"].get("youtube", "02 Sources/Videos"),
                article=data["output_folders"].get("article", "02 Sources/Articles"),
                default=data["output_folders"].get("default", "Clippings"),
            ),
            youtube=YouTubeConfig(
                fetch_transcript=data["youtube"].get("fetch_transcript", True),
                transcript_languages=data["youtube"].get(
                    "transcript_languages", ["en", "en-US", "en-GB"]
                ),
                cookies_from_browser=data["youtube"].get("cookies_from_browser") or None,
                cookies_file=data["youtube"].get("cookies_file") or None,
            ),
            vault_writer=VaultWriterConfig(
                daily_notes_folder=data.get("vault_writer", {}).get("daily_notes_folder", "06 Daily Notes"),
                ics_folder=data.get("vault_writer", {}).get("ics_folder", ""),
            ),
            channel_crawl=ChannelCrawlConfig(
                max_videos=data.get("channel_crawl", {}).get("max_videos", 25),
                title_filter_threshold=data.get("channel_crawl", {}).get("title_filter_threshold", 25),
                sleep_seconds=data.get("channel_crawl", {}).get("sleep_seconds", 2.0),
                transcript_filter_chars=data.get("channel_crawl", {}).get("transcript_filter_chars", 1500),
                state_file=data.get("channel_crawl", {}).get("state_file", "data/crawl_state.json"),
                manifest_timeout_seconds=data.get("channel_crawl", {}).get("manifest_timeout_seconds", 120.0),
            ),
            routing_categories=[
                RoutingCategory(name=cat["name"], folder=cat["folder"])
                for cat in data.get("routing_categories", [{"name": "Other", "folder": "02 Sources/Articles"}])
            ],
        )
