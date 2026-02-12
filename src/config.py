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

    @property
    def api_key(self) -> str:
        if self.provider == "groq":
            return self.groq_api_key
        if self.provider == "gemini":
            return self.gemini_api_key
        return self.anthropic_api_key

    @property
    def model(self) -> str:
        if self.provider == "groq":
            return self.groq_model
        if self.provider == "gemini":
            return self.gemini_model
        return self.anthropic_model


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


@dataclass
class RoutingCategory:
    """A category for AI-powered content routing."""

    name: str
    folder: str


@dataclass
class Config:
    vault_path: Path
    telegram: TelegramConfig
    ai: AIConfig
    output_folders: OutputFoldersConfig
    youtube: YouTubeConfig
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
            ),
            routing_categories=[
                RoutingCategory(name=cat["name"], folder=cat["folder"])
                for cat in data.get("routing_categories", [{"name": "Other", "folder": "02 Sources/Articles"}])
            ],
        )
