"""Tests for NoteLinkEngine — related notes, tag resolution, URL lookup."""
import tempfile
from pathlib import Path

import pytest

from src.linker import NoteLinkEngine


@pytest.fixture
def vault(tmp_path):
    (tmp_path / "03 Tags").mkdir()
    (tmp_path / "04 Indexes").mkdir()
    (tmp_path / "04 Indexes" / "Programming").mkdir()
    (tmp_path / "05 Base Notes").mkdir()

    (tmp_path / "03 Tags" / "Status.md").write_text("# Status Tag")
    (tmp_path / "04 Indexes" / "Python.md").write_text("# Python Index")
    (tmp_path / "04 Indexes" / "Machine Learning.md").write_text("# ML Index")
    (tmp_path / "04 Indexes" / "Programming" / "Web Development.md").write_text("# Web Dev")
    (tmp_path / "05 Base Notes" / "Neural Networks.md").write_text("# NN Notes")
    (tmp_path / "05 Base Notes" / "REST API.md").write_text("# REST")

    return tmp_path


# ── Index building ─────────────────────────────────────────────────────────────

class TestBuildIndex:
    def test_index_contains_expected_keys(self, vault):
        linker = NoteLinkEngine(vault)
        index = linker._build_index()
        assert "python" in index
        assert "machine learning" in index
        assert "neural networks" in index
        assert "web development" in index
        assert "status" in index

    def test_cache_reused(self, vault):
        linker = NoteLinkEngine(vault)
        assert linker._build_index() is linker._build_index()

    def test_clear_cache(self, vault):
        linker = NoteLinkEngine(vault)
        linker._build_index()
        linker.clear_cache()
        assert linker._index_cache is None

    def test_missing_folders_ok(self, tmp_path):
        (tmp_path / "04 Indexes").mkdir()
        (tmp_path / "04 Indexes" / "Test.md").write_text("# Test")
        links = NoteLinkEngine(tmp_path).find_related_notes(["Test"], [])
        assert len(links) == 1


# ── Related notes ──────────────────────────────────────────────────────────────

class TestFindRelatedNotes:
    def test_exact_match(self, vault):
        links = NoteLinkEngine(vault).find_related_notes(["Python"], [])
        assert len(links) == 1
        assert "Python" in links[0]
        assert "04 Indexes/Python" in links[0]

    def test_substring_match(self, vault):
        links = NoteLinkEngine(vault).find_related_notes(["Learning"], [])
        assert any("Machine Learning" in l for l in links)

    def test_reverse_match(self, vault):
        links = NoteLinkEngine(vault).find_related_notes(["Python Programming"], [])
        assert any("Python" in l for l in links)

    def test_multiple_terms(self, vault):
        links = NoteLinkEngine(vault).find_related_notes(["Python", "Neural Networks"], ["Machine Learning"])
        titles = " ".join(links)
        assert "Python" in titles
        assert "Neural Networks" in titles
        assert "Machine Learning" in titles

    def test_no_duplicates(self, vault):
        links = NoteLinkEngine(vault).find_related_notes(["Python", "python"], [])
        assert len(links) == 1

    def test_limit_to_seven(self, vault):
        links = NoteLinkEngine(vault).find_related_notes(
            ["Python", "Machine Learning", "Neural Networks", "REST API", "Web"],
            ["Status", "Development", "Programming", "API"],
        )
        assert len(links) <= 7

    def test_case_insensitive(self, vault):
        links = NoteLinkEngine(vault).find_related_notes(["PYTHON"], [])
        assert len(links) == 1
        assert "Python" in links[0]


# ── Wikilink generation ───────────────────────────────────���────────────────────

class TestMakeWikilink:
    def test_flat_path(self, vault):
        path = vault / "04 Indexes" / "Python.md"
        assert NoteLinkEngine(vault)._make_wikilink(path, "Python") == "[[04 Indexes/Python|Python]]"

    def test_nested_path(self, vault):
        path = vault / "04 Indexes" / "Programming" / "Web Development.md"
        link = NoteLinkEngine(vault)._make_wikilink(path, "Web Development")
        assert link == "[[04 Indexes/Programming/Web Development|Web Development]]"


# ── Tag normalisation ──────────────────────────────────────────────────────────

class TestNormalizeTag:
    def test_plain_term(self):
        assert NoteLinkEngine._normalize_tag("Python") == "Python"

    def test_strips_hash(self):
        assert NoteLinkEngine._normalize_tag("#machine-learning") == "machine-learning"

    def test_strips_wikilink(self):
        assert NoteLinkEngine._normalize_tag("[[Python]]") == "Python"

    def test_strips_wikilink_with_display(self):
        assert NoteLinkEngine._normalize_tag("[[04 Indexes/Python|Python]]") == "Python"

    def test_strips_whitespace(self):
        assert NoteLinkEngine._normalize_tag("  Python  ") == "Python"

    def test_empty_string(self):
        assert NoteLinkEngine._normalize_tag("") == ""


# ── Tag resolution ─────────────────────────────────────────────────────────────

class TestResolveTags:
    def test_links_to_existing_index(self, vault):
        links = NoteLinkEngine(vault).resolve_tags(["Python"])
        assert len(links) == 1
        assert "04 Indexes/Python" in links[0]
        assert "|Python" in links[0]

    def test_links_to_existing_tag(self, vault):
        links = NoteLinkEngine(vault).resolve_tags(["Status"])
        assert len(links) == 1
        assert "03 Tags/Status" in links[0]

    def test_creates_missing_tag_file(self, vault):
        NoteLinkEngine(vault).resolve_tags(["New Concept"])
        assert (vault / "03 Tags" / "New Concept.md").exists()

    def test_missing_tag_wikilink(self, vault):
        links = NoteLinkEngine(vault).resolve_tags(["Brand New Topic"])
        assert len(links) == 1
        assert "03 Tags/Brand New Topic" in links[0]

    def test_deduplicates_same_term(self, vault):
        links = NoteLinkEngine(vault).resolve_tags(["Python", "Python"])
        assert len(links) == 1

    def test_deduplicates_case_insensitive(self, vault):
        links = NoteLinkEngine(vault).resolve_tags(["python", "Python"])
        assert len(links) == 1

    def test_strips_wikilink_format_from_ai(self, vault):
        links = NoteLinkEngine(vault).resolve_tags(["[[Python]]"])
        assert len(links) == 1
        assert "Python" in links[0]

    def test_strips_hash_format_from_ai(self, vault):
        links = NoteLinkEngine(vault).resolve_tags(["#machine-learning"])
        # Should create a tag for "machine-learning"
        assert len(links) == 1

    def test_skips_empty_terms(self, vault):
        links = NoteLinkEngine(vault).resolve_tags(["", "  ", "Python"])
        assert len(links) == 1

    def test_existing_tag_file_not_overwritten(self, vault):
        tag_file = vault / "03 Tags" / "Existing Tag.md"
        tag_file.write_text("# My custom content")
        NoteLinkEngine(vault).resolve_tags(["Existing Tag"])
        assert tag_file.read_text() == "# My custom content"


# ── URL lookup ─────────────────────────────────────────────────────────────────

class TestFindNoteByUrl:
    def test_finds_note_containing_url(self, vault):
        url = "https://example.com/article"
        (vault / "02 Sources").mkdir()
        note = vault / "02 Sources" / "My Article.md"
        note.write_text(f'---\nsource: "{url}"\n---\n# My Article\n')

        result = NoteLinkEngine(vault).find_note_by_url(url)
        assert result == "My Article"

    def test_returns_none_when_not_found(self, vault):
        result = NoteLinkEngine(vault).find_note_by_url("https://not-in-vault.com")
        assert result is None

    def test_skips_hidden_dirs(self, vault):
        hidden = vault / ".obsidian"
        hidden.mkdir()
        (hidden / "config.md").write_text("https://example.com/secret")
        result = NoteLinkEngine(vault).find_note_by_url("https://example.com/secret")
        assert result is None
