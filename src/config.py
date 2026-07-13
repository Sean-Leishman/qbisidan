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
    instagram: str = "02 Sources/Reels"
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
class InstagramConfig:
    """Configuration for the Instagram reel scraper's video understanding call."""

    # Netscape-format cookies.txt for reels that need a logged-in session
    # (private accounts, age-gated content). Same idiom as youtube.cookies_file.
    cookies_file: str | None = None
    # Gemini model used for the video-understanding call (speech transcript +
    # on-screen visuals). Must be a model that accepts video input via the
    # Files API. Deliberately the same default as ai.gemini_model — already
    # known to support video input via the Files API.
    video_model: str = "gemini-2.0-flash"
    # Video is ~300 tokens/second, so a stray 20-minute reel would otherwise
    # silently become a ~400k-token call. Over this limit -> caption-only.
    max_duration_seconds: int = 180
    # Folder holding Instagram's "Download your information" (DYI) export —
    # searched recursively for saved_posts.json / liked_posts.json. No login,
    # no scraping; re-export manually for each future backfill chunk.
    export_dir: str = ""


@dataclass
class InterestsConfig:
    """Renders to the free-text topic string the existing TopicFilterAgent
    already accepts — not a new agent, just a nicer config shape for it."""

    include: list[str] = field(default_factory=list)
    exclude: list[str] = field(default_factory=list)

    def render(self) -> str | None:
        if not self.include and not self.exclude:
            return None
        parts = []
        if self.include:
            parts.append("Include content about: " + ", ".join(self.include) + ".")
        if self.exclude:
            parts.append("Exclude content about: " + ", ".join(self.exclude) + ".")
        return " ".join(parts)


@dataclass
class BackfillConfig:
    """Configuration for /backfill (and --backfill)."""

    # ponytail: small default cap per run — Instagram rate-limits hard, so a
    # backfill of thousands of likes is meant to run in chunks across several
    # invocations (resumable via CrawlStateStore), not one heroic pass.
    max_items: int = 50


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
    instagram: InstagramConfig = field(default_factory=InstagramConfig)
    vault_writer: VaultWriterConfig = field(default_factory=VaultWriterConfig)
    channel_crawl: ChannelCrawlConfig = field(default_factory=ChannelCrawlConfig)
    interests: InterestsConfig = field(default_factory=InterestsConfig)
    backfill: BackfillConfig = field(default_factory=BackfillConfig)
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
                instagram=data["output_folders"].get("instagram", "02 Sources/Reels"),
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
            instagram=InstagramConfig(
                cookies_file=data.get("instagram", {}).get("cookies_file") or None,
                video_model=data.get("instagram", {}).get("video_model", "gemini-2.0-flash"),
                max_duration_seconds=data.get("instagram", {}).get("max_duration_seconds", 180),
                export_dir=data.get("instagram", {}).get("export_dir", ""),
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
            interests=InterestsConfig(
                include=data.get("interests", {}).get("include", []),
                exclude=data.get("interests", {}).get("exclude", []),
            ),
            backfill=BackfillConfig(
                max_items=data.get("backfill", {}).get("max_items", 50),
            ),
            routing_categories=[
                RoutingCategory(name=cat["name"], folder=cat["folder"])
                for cat in data.get("routing_categories", [{"name": "Other", "folder": "02 Sources/Articles"}])
            ],
        )
