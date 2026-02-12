"""Tests for config module - routing categories."""
import tempfile
from pathlib import Path

import pytest

from src.config import Config, RoutingCategory


class TestRoutingCategory:
    """Test RoutingCategory dataclass."""

    def test_initialization(self):
        """Test RoutingCategory creation."""
        cat = RoutingCategory(name="Programming", folder="02 Sources/Articles/Programming")

        assert cat.name == "Programming"
        assert cat.folder == "02 Sources/Articles/Programming"


class TestConfigRoutingCategories:
    """Test Config routing category methods."""

    @pytest.fixture
    def config_with_categories(self):
        """Create config with routing categories."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_file = Path(tmpdir) / "config.yaml"
            config_file.write_text(
                """
vault_path: "/tmp/vault"

telegram:
  bot_token: "test"
  allowed_chat_ids: []
  send_notifications: false

ai:
  provider: "groq"
  groq_api_key: "test"
  groq_model: "test"
  gemini_api_key: ""
  gemini_model: ""
  anthropic_api_key: ""
  anthropic_model: ""

output_folders:
  youtube: "02 Sources/Videos"
  article: "02 Sources/Articles"
  default: "Clippings"

youtube:
  fetch_transcript: true
  transcript_languages: ["en"]

routing_categories:
  - name: "Programming"
    folder: "02 Sources/Articles/Programming"
  - name: "Machine Learning"
    folder: "02 Sources/Articles/ML"
  - name: "Finance"
    folder: "02 Sources/Articles/Finance"
  - name: "Other"
    folder: "02 Sources/Articles"
"""
            )

            config = Config.load(str(config_file))
            yield config

    def test_get_routing_category_names(self, config_with_categories):
        """Test getting list of category names."""
        names = config_with_categories.get_routing_category_names()

        assert names == ["Programming", "Machine Learning", "Finance", "Other"]

    def test_get_folder_for_category_exact_match(self, config_with_categories):
        """Test folder lookup with exact match."""
        folder = config_with_categories.get_folder_for_category("Programming")

        assert folder == "02 Sources/Articles/Programming"

    def test_get_folder_for_category_case_insensitive(self, config_with_categories):
        """Test folder lookup is case-insensitive."""
        folder = config_with_categories.get_folder_for_category("programming")

        assert folder == "02 Sources/Articles/Programming"

        folder2 = config_with_categories.get_folder_for_category("MACHINE LEARNING")

        assert folder2 == "02 Sources/Articles/ML"

    def test_get_folder_for_unknown_category(self, config_with_categories):
        """Test folder lookup for unknown category returns None."""
        folder = config_with_categories.get_folder_for_category("Unknown Category")

        assert folder is None

    def test_config_loads_routing_categories(self, config_with_categories):
        """Test that routing categories are loaded from config."""
        assert len(config_with_categories.routing_categories) == 4
        assert config_with_categories.routing_categories[0].name == "Programming"
        assert config_with_categories.routing_categories[1].name == "Machine Learning"


class TestConfigDefaultCategories:
    """Test Config with default/missing routing categories."""

    @pytest.fixture
    def config_without_categories(self):
        """Create config without routing categories."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_file = Path(tmpdir) / "config.yaml"
            config_file.write_text(
                """
vault_path: "/tmp/vault"

telegram:
  bot_token: "test"
  allowed_chat_ids: []

ai:
  provider: "groq"
  groq_api_key: "test"
  groq_model: "test"
  gemini_api_key: ""
  gemini_model: ""
  anthropic_api_key: ""
  anthropic_model: ""

output_folders:
  youtube: "02 Sources/Videos"
  article: "02 Sources/Articles"
  default: "Clippings"

youtube:
  fetch_transcript: true
  transcript_languages: ["en"]
"""
            )

            config = Config.load(str(config_file))
            yield config

    def test_default_routing_category(self, config_without_categories):
        """Test default routing category when none specified."""
        assert len(config_without_categories.routing_categories) == 1
        assert config_without_categories.routing_categories[0].name == "Other"

    def test_get_names_with_default(self, config_without_categories):
        """Test getting names with default category."""
        names = config_without_categories.get_routing_category_names()

        assert names == ["Other"]
