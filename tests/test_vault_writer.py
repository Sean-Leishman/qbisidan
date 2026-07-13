"""Tests for VaultWriter.ensure_source_note — book/magazine entity notes."""
import pytest

from src.agents.vault_writer import VaultWriter


@pytest.fixture
def writer(tmp_path):
    return VaultWriter(vault_path=tmp_path, books_folder="02 Sources/Books")


class TestEnsureSourceNote:
    def test_creates_note_with_frontmatter(self, writer, tmp_path):
        path = writer.ensure_source_note("Sapiens", "book", author="Yuval Noah Harari")
        assert path == tmp_path / "02 Sources/Books/Sapiens.md"
        assert path.exists()
        content = path.read_text(encoding="utf-8")
        assert 'title: "Sapiens"' in content
        assert "type: book" in content
        assert 'author: "Yuval Noah Harari"' in content
        assert "## Referenced by" in content

    def test_creates_note_without_author(self, writer):
        path = writer.ensure_source_note("Some Podcast", "podcast")
        content = path.read_text(encoding="utf-8")
        assert "author:" not in content

    def test_appends_backlink(self, writer):
        path = writer.ensure_source_note("Sapiens", "book", referenced_by="My Note")
        content = path.read_text(encoding="utf-8")
        assert "- [[My Note]]" in content

    def test_idempotent_backlink_not_duplicated(self, writer):
        writer.ensure_source_note("Sapiens", "book", referenced_by="My Note")
        path = writer.ensure_source_note("Sapiens", "book", referenced_by="My Note")
        content = path.read_text(encoding="utf-8")
        assert content.count("- [[My Note]]") == 1

    def test_second_reference_from_different_note_both_kept(self, writer):
        writer.ensure_source_note("Sapiens", "book", referenced_by="Note A")
        path = writer.ensure_source_note("Sapiens", "book", referenced_by="Note B")
        content = path.read_text(encoding="utf-8")
        assert "- [[Note A]]" in content
        assert "- [[Note B]]" in content

    def test_does_not_recreate_existing_note(self, writer):
        path1 = writer.ensure_source_note("Sapiens", "book", author="Yuval Noah Harari")
        # Second call with no author shouldn't wipe the existing frontmatter.
        path2 = writer.ensure_source_note("Sapiens", "book")
        assert path1 == path2
        content = path2.read_text(encoding="utf-8")
        assert 'author: "Yuval Noah Harari"' in content

    def test_exact_title_match_only(self, writer):
        """No fuzzy matching — different titles make different notes."""
        path1 = writer.ensure_source_note("Sapiens", "book")
        path2 = writer.ensure_source_note("Sapiens: A Brief History of Humankind", "book")
        assert path1 != path2
