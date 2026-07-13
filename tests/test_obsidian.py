"""Tests for obsidian module - note generation."""
import tempfile
from datetime import datetime
from pathlib import Path

import pytest

from src.obsidian import ObsidianNoteGenerator
from src.scrapers.base import ScrapedContent
from src.summarizer import SummaryResult


@pytest.fixture
def generator(tmp_path):
    return ObsidianNoteGenerator(
        vault_path=tmp_path,
        output_folders={
            "youtube": "02 Sources/Videos",
            "article": "02 Sources/Articles",
            "default": "Clippings",
        },
    )


@pytest.fixture
def scraped_article():
    return ScrapedContent(
        url="https://example.com/article",
        title="Test Article Title",
        author="John Doe",
        content="Article content here",
        content_type="article",
        published=datetime(2024, 1, 15),
    )


@pytest.fixture
def scraped_video():
    return ScrapedContent(
        url="https://youtube.com/watch?v=abc123",
        title="Test Video Title",
        author="Tech Channel",
        content="Video transcript here",
        content_type="youtube",
        published=datetime(2024, 2, 20),
    )


@pytest.fixture
def full_summary():
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


# ── Tag formatting ─────────────────────────────────────────────────────────────

class TestTagFormatting:
    def test_format_tag_links_non_empty(self, generator):
        links = ["[[03 Tags/Python|Python]]", "[[04 Indexes/ML|Machine Learning]]"]
        result = generator._format_tag_links(links)
        assert "[[03 Tags/Python|Python]]" in result
        assert "[[04 Indexes/ML|Machine Learning]]" in result

    def test_format_tag_links_empty(self, generator):
        assert generator._format_tag_links([]) == ""

    def test_resolved_tags_appear_in_properties(self, generator, scraped_article, full_summary):
        resolved = ["[[03 Tags/Python|Python]]", "[[04 Indexes/ML|Machine Learning]]"]
        note = generator.generate_note(scraped_article, full_summary, resolved_tags=resolved)
        assert "[[03 Tags/Python|Python]]" in note
        assert "[[04 Indexes/ML|Machine Learning]]" in note

    def test_no_resolved_tags_leaves_tags_blank(self, generator, scraped_article, full_summary):
        note = generator.generate_note(scraped_article, full_summary, resolved_tags=None)
        # Tags line should be empty but Properties callout still present
        assert "**Tags:**" in note


# ── Truncation warning ─────────────────────────────────────────────────────────

class TestTruncationWarning:
    def test_warning_shown_when_truncated(self, generator, scraped_article, full_summary):
        full_summary.content_truncated = True
        note = generator.generate_note(scraped_article, full_summary)
        assert "[!warning]" in note
        assert "truncated" in note.lower()

    def test_no_warning_when_not_truncated(self, generator, scraped_article, full_summary):
        full_summary.content_truncated = False
        note = generator.generate_note(scraped_article, full_summary)
        assert "[!warning]" not in note

    def test_warning_appears_before_summary(self, generator, scraped_article, full_summary):
        full_summary.content_truncated = True
        note = generator.generate_note(scraped_article, full_summary)
        warning_pos = note.find("[!warning]")
        summary_pos = note.find("## Summary")
        assert warning_pos < summary_pos


# ── Note content sections ──────────────────────────────────────────────────────

class TestNoteSections:
    def test_action_items_as_checkboxes(self, generator, scraped_article, full_summary):
        note = generator.generate_note(scraped_article, full_summary)
        assert "## Action Items" in note
        assert "- [ ] Set up pytest" in note
        assert "- [ ] Write unit tests" in note
        assert "- [ ] Add CI/CD" in note

    def test_review_questions_numbered(self, generator, scraped_article, full_summary):
        note = generator.generate_note(scraped_article, full_summary)
        assert "## Review Questions" in note
        assert "1. What is the purpose of unit testing?" in note
        assert "2. How does pytest discover tests?" in note

    def test_related_links_in_key_concepts(self, generator, scraped_article, full_summary):
        links = ["[[04 Indexes/Python|Python]]", "[[05 Base Notes/Unit Testing|Unit Testing]]"]
        note = generator.generate_note(scraped_article, full_summary, related_links=links)
        assert "## Key Concepts" in note
        assert "- [[04 Indexes/Python|Python]]" in note
        assert "- [[05 Base Notes/Unit Testing|Unit Testing]]" in note

    def test_minimal_summary_no_optional_sections(self, generator, scraped_article):
        minimal = SummaryResult(summary="Just a simple summary.")
        note = generator.generate_note(scraped_article, minimal)
        assert "## Summary" in note
        assert "Just a simple summary." in note
        assert "## Action Items" not in note
        assert "## Review Questions" not in note
        assert "## Key Concepts" not in note

    def test_user_notes_callout(self, generator, scraped_article, full_summary):
        note = generator.generate_note(scraped_article, full_summary, user_notes="Focus on caching")
        assert "## Notes" in note
        assert "> [!tip] My Notes" in note
        assert "> Focus on caching" in note

    def test_youtube_note_has_embed(self, generator, scraped_video, full_summary):
        note = generator.generate_note(scraped_video, full_summary)
        assert "![Test Video Title](https://youtube.com/watch?v=abc123)" in note

    def test_section_order(self, generator, scraped_article, full_summary):
        note = generator.generate_note(
            scraped_article, full_summary,
            user_notes="My notes",
            related_links=["[[04 Indexes/Python|Python]]"],
        )
        positions = {
            s: note.find(s)
            for s in ["## Summary", "## Key Concepts", "## Action Items", "## Review Questions", "## Notes"]
        }
        ordered = sorted(positions, key=positions.__getitem__)
        assert ordered == ["## Summary", "## Key Concepts", "## Action Items", "## Review Questions", "## Notes"]


# ── File saving ────────────────────────────────────────────────────────────────

class TestMentionedSources:
    def test_renders_two_book_wikilinks(self, generator, scraped_article, full_summary):
        sources = ["[[02 Sources/Books/Sapiens|Sapiens]]", "[[02 Sources/Books/Atomic Habits|Atomic Habits]]"]
        note = generator.generate_note(scraped_article, full_summary, resolved_sources=sources)
        assert "Mentioned sources:" in note
        assert "[[02 Sources/Books/Sapiens|Sapiens]]" in note
        assert "[[02 Sources/Books/Atomic Habits|Atomic Habits]]" in note

    def test_empty_mentioned_sources_renders_nothing(self, generator, scraped_article, full_summary):
        note = generator.generate_note(scraped_article, full_summary, resolved_sources=[])
        assert "Mentioned sources" not in note

    def test_none_mentioned_sources_renders_nothing(self, generator, scraped_article, full_summary):
        note = generator.generate_note(scraped_article, full_summary, resolved_sources=None)
        assert "Mentioned sources" not in note


class TestSaveNote:
    def test_save_uses_folder_override(self, generator, scraped_article):
        summary = SummaryResult(summary="Test", category="Programming")
        path, folder = generator.save_note(scraped_article, summary, folder_override="02 Sources/Articles/Programming")
        assert folder == "02 Sources/Articles/Programming"
        assert "02 Sources/Articles/Programming" in str(path)
        assert path.exists()

    def test_save_default_article_folder(self, generator, scraped_article):
        summary = SummaryResult(summary="Test")
        path, folder = generator.save_note(scraped_article, summary)
        assert folder == "02 Sources/Articles"
        assert path.exists()

    def test_save_youtube_default_folder(self, generator, scraped_video):
        summary = SummaryResult(summary="Test")
        path, folder = generator.save_note(scraped_video, summary)
        assert folder == "02 Sources/Videos"
        assert path.exists()

    def test_duplicate_filename_gets_counter(self, generator, scraped_article):
        summary = SummaryResult(summary="Test")
        path1, _ = generator.save_note(scraped_article, summary)
        path2, _ = generator.save_note(scraped_article, summary)
        assert path1 != path2
        assert path1.exists()
        assert path2.exists()
