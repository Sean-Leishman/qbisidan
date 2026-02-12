"""Tests for obsidian module - enhanced note generation."""
import tempfile
from datetime import datetime
from pathlib import Path

import pytest

from src.obsidian import ObsidianNoteGenerator
from src.scrapers.base import ScrapedContent
from src.summarizer import SummaryResult


class TestObsidianNoteGenerator:
    """Test enhanced ObsidianNoteGenerator."""

    @pytest.fixture
    def generator(self):
        """Create generator with temp vault."""
        with tempfile.TemporaryDirectory() as tmpdir:
            vault = Path(tmpdir)
            yield ObsidianNoteGenerator(
                vault_path=vault,
                output_folders={
                    "youtube": "02 Sources/Videos",
                    "article": "02 Sources/Articles",
                    "default": "Clippings",
                },
            )

    @pytest.fixture
    def scraped_article(self):
        """Sample scraped article content."""
        return ScrapedContent(
            url="https://example.com/article",
            title="Test Article Title",
            author="John Doe",
            content="Article content here",
            content_type="article",
            published=datetime(2024, 1, 15),
        )

    @pytest.fixture
    def scraped_video(self):
        """Sample scraped YouTube content."""
        return ScrapedContent(
            url="https://youtube.com/watch?v=abc123",
            title="Test Video Title",
            author="Tech Channel",
            content="Video transcript here",
            content_type="youtube",
            published=datetime(2024, 2, 20),
        )

    @pytest.fixture
    def full_summary(self):
        """Sample full SummaryResult."""
        return SummaryResult(
            summary="This is a comprehensive summary of the content.",
            category="Programming",
            tags=["Python", "Testing", "Best Practices"],
            action_items=["Set up pytest", "Write unit tests", "Add CI/CD"],
            review_questions=[
                "What is the purpose of unit testing?",
                "How does pytest discover tests?",
            ],
            key_concepts=["Unit Testing", "Test Coverage", "Mocking"],
        )

    def test_generate_note_with_tags(self, generator, scraped_article, full_summary):
        """Test that tags are formatted as wikilinks in properties."""
        note = generator.generate_note(scraped_article, full_summary)

        # Check tags in Properties section
        assert "[[../04 Indexes/Python|Python]]" in note
        assert "[[../04 Indexes/Testing|Testing]]" in note
        assert "[[../04 Indexes/Best Practices|Best Practices]]" in note

    def test_generate_note_with_action_items(
        self, generator, scraped_article, full_summary
    ):
        """Test that action items are formatted as checkboxes."""
        note = generator.generate_note(scraped_article, full_summary)

        assert "## Action Items" in note
        assert "- [ ] Set up pytest" in note
        assert "- [ ] Write unit tests" in note
        assert "- [ ] Add CI/CD" in note

    def test_generate_note_with_review_questions(
        self, generator, scraped_article, full_summary
    ):
        """Test that review questions are numbered."""
        note = generator.generate_note(scraped_article, full_summary)

        assert "## Review Questions" in note
        assert "1. What is the purpose of unit testing?" in note
        assert "2. How does pytest discover tests?" in note

    def test_generate_note_with_related_links(
        self, generator, scraped_article, full_summary
    ):
        """Test that related links appear in Key Concepts section."""
        related_links = [
            "[[04 Indexes/Python|Python]]",
            "[[05 Base Notes/Unit Testing|Unit Testing]]",
        ]

        note = generator.generate_note(
            scraped_article, full_summary, related_links=related_links
        )

        assert "## Key Concepts" in note
        assert "- [[04 Indexes/Python|Python]]" in note
        assert "- [[05 Base Notes/Unit Testing|Unit Testing]]" in note

    def test_generate_note_without_optional_sections(
        self, generator, scraped_article
    ):
        """Test note generation when summary has no action items/questions."""
        minimal_summary = SummaryResult(summary="Just a simple summary.")

        note = generator.generate_note(scraped_article, minimal_summary)

        assert "## Summary" in note
        assert "Just a simple summary." in note
        assert "## Action Items" not in note
        assert "## Review Questions" not in note
        assert "## Key Concepts" not in note

    def test_generate_note_with_user_notes(
        self, generator, scraped_article, full_summary
    ):
        """Test that user notes appear in Notes section."""
        note = generator.generate_note(
            scraped_article, full_summary, user_notes="Focus on testing patterns"
        )

        assert "## Notes" in note
        assert "> [!tip] My Notes" in note
        assert "> Focus on testing patterns" in note

    def test_generate_youtube_note_has_embed(
        self, generator, scraped_video, full_summary
    ):
        """Test that YouTube notes include video embed."""
        note = generator.generate_note(scraped_video, full_summary)

        assert "![Test Video Title](https://youtube.com/watch?v=abc123)" in note

    def test_format_tags_as_wikilinks(self, generator):
        """Test tag formatting helper."""
        tags = ["Python", "Machine Learning"]

        result = generator._format_tags_as_wikilinks(tags)

        assert "[[../04 Indexes/Python|Python]]" in result
        assert "[[../04 Indexes/Machine Learning|Machine Learning]]" in result

    def test_format_empty_tags(self, generator):
        """Test formatting with no tags."""
        result = generator._format_tags_as_wikilinks([])

        assert result == ""

    def test_save_note_with_folder_override(self, generator, scraped_article):
        """Test saving note to custom folder."""
        summary = SummaryResult(summary="Test summary", category="Programming")

        file_path, folder = generator.save_note(
            scraped_article,
            summary,
            folder_override="02 Sources/Articles/Programming",
        )

        assert folder == "02 Sources/Articles/Programming"
        assert "02 Sources/Articles/Programming" in str(file_path)
        assert file_path.exists()

    def test_save_note_default_folder(self, generator, scraped_article):
        """Test saving note uses default folder when no override."""
        summary = SummaryResult(summary="Test summary")

        file_path, folder = generator.save_note(scraped_article, summary)

        assert folder == "02 Sources/Articles"
        assert file_path.exists()

    def test_note_structure_order(self, generator, scraped_article, full_summary):
        """Test that note sections appear in correct order."""
        related_links = ["[[04 Indexes/Python|Python]]"]

        note = generator.generate_note(
            scraped_article, full_summary, user_notes="My notes", related_links=related_links
        )

        # Find positions of each section
        summary_pos = note.find("## Summary")
        concepts_pos = note.find("## Key Concepts")
        actions_pos = note.find("## Action Items")
        questions_pos = note.find("## Review Questions")
        notes_pos = note.find("## Notes")

        # Verify order: Summary -> Key Concepts -> Action Items -> Review Questions -> Notes
        assert summary_pos < concepts_pos < actions_pos < questions_pos < notes_pos
