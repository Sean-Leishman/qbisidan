"""Tests for the mentioned_sources entity-note pipeline (Phase 5):
summarizer -> Processor._ensure_mentioned_sources -> VaultWriter.ensure_source_note
-> ObsidianNoteGenerator renders the resolved wikilinks. No network — the
summarizer's AI call is stubbed.
"""
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src.agents.vault_writer import VaultWriter
from src.linker import NoteLinkEngine
from src.obsidian import ObsidianNoteGenerator
from src.processor import Processor
from src.scrapers.base import ScrapedContent
from src.summarizer import SummaryResult


def make_processor(tmp_path):
    p = Processor.__new__(Processor)
    p.linker = NoteLinkEngine(vault_path=tmp_path)
    p.vault_writer = VaultWriter(vault_path=tmp_path, books_folder="02 Sources/Books")
    p.note_generator = ObsidianNoteGenerator(
        vault_path=tmp_path,
        output_folders={"article": "02 Sources/Articles", "default": "Clippings"},
    )
    p.summarizer = MagicMock()
    p.question_agent = MagicMock()
    p.config = SimpleNamespace(get_folder_for_category=lambda c: None)
    p.telegram = MagicMock()
    return p


@pytest.fixture
def scraped():
    return ScrapedContent(
        url="https://example.com/a",
        title="An Article About Habits",
        author="Jane",
        content="content",
        content_type="article",
    )


class TestEnsureMentionedSources:
    def test_empty_mentioned_sources_writes_nothing(self, tmp_path):
        p = make_processor(tmp_path)
        resolved = p._ensure_mentioned_sources([], "Referencing Note")
        assert resolved == []
        assert not (tmp_path / "02 Sources/Books").exists()

    def test_two_books_both_resolved(self, tmp_path):
        p = make_processor(tmp_path)
        mentioned = [
            {"title": "Sapiens", "type": "book", "author": "Yuval Noah Harari"},
            {"title": "Atomic Habits", "type": "book", "author": "James Clear"},
        ]
        resolved = p._ensure_mentioned_sources(mentioned, "Referencing Note")
        assert len(resolved) == 2
        assert any("Sapiens" in r for r in resolved)
        assert any("Atomic Habits" in r for r in resolved)
        assert (tmp_path / "02 Sources/Books/Sapiens.md").exists()
        assert (tmp_path / "02 Sources/Books/Atomic Habits.md").exists()
        sapiens = (tmp_path / "02 Sources/Books/Sapiens.md").read_text()
        assert "- [[Referencing Note]]" in sapiens

    def test_idempotent_across_two_calls(self, tmp_path):
        p = make_processor(tmp_path)
        mentioned = [{"title": "Sapiens", "type": "book"}]
        p._ensure_mentioned_sources(mentioned, "Referencing Note")
        p._ensure_mentioned_sources(mentioned, "Referencing Note")
        content = (tmp_path / "02 Sources/Books/Sapiens.md").read_text()
        assert content.count("- [[Referencing Note]]") == 1


class TestSaveScrapedRendersMentionedSources:
    def test_two_books_rendered_in_note(self, tmp_path, scraped):
        p = make_processor(tmp_path)
        p.summarizer.summarize.return_value = SummaryResult(
            summary="Summary text.",
            mentioned_sources=[
                {"title": "Sapiens", "type": "book"},
                {"title": "Atomic Habits", "type": "book"},
            ],
        )
        result = p._save_scraped(scraped, user_notes=None)
        assert result.success
        note_path = tmp_path / "02 Sources/Articles" / f"{scraped.title}.md"
        note_content = note_path.read_text()
        assert "Mentioned sources:" in note_content
        assert "Sapiens" in note_content
        assert "Atomic Habits" in note_content

    def test_empty_mentioned_sources_no_section_no_entity_notes(self, tmp_path, scraped):
        p = make_processor(tmp_path)
        p.summarizer.summarize.return_value = SummaryResult(summary="Summary text.")
        result = p._save_scraped(scraped, user_notes=None)
        assert result.success
        note_path = tmp_path / "02 Sources/Articles" / f"{scraped.title}.md"
        note_content = note_path.read_text()
        assert "Mentioned sources" not in note_content
        assert not (tmp_path / "02 Sources/Books").exists()
