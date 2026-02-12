"""Tests for linker module - NoteLinkEngine."""
import tempfile
from pathlib import Path

import pytest

from src.linker import NoteLinkEngine


class TestNoteLinkEngine:
    """Test NoteLinkEngine functionality."""

    @pytest.fixture
    def temp_vault(self):
        """Create a temporary vault structure with test notes."""
        with tempfile.TemporaryDirectory() as tmpdir:
            vault = Path(tmpdir)

            # Create folder structure
            (vault / "03 Tags").mkdir()
            (vault / "04 Indexes").mkdir()
            (vault / "04 Indexes" / "Programming").mkdir()
            (vault / "05 Base Notes").mkdir()

            # Create test notes
            (vault / "03 Tags" / "Status.md").write_text("# Status Tag")
            (vault / "04 Indexes" / "Python.md").write_text("# Python Index")
            (vault / "04 Indexes" / "Machine Learning.md").write_text("# ML Index")
            (vault / "04 Indexes" / "Programming" / "Web Development.md").write_text(
                "# Web Dev"
            )
            (vault / "05 Base Notes" / "Neural Networks.md").write_text("# NN Notes")
            (vault / "05 Base Notes" / "REST API.md").write_text("# REST")

            yield vault

    def test_build_index(self, temp_vault):
        """Test that index is built correctly from vault."""
        linker = NoteLinkEngine(temp_vault)
        index = linker._build_index()

        assert "python" in index
        assert "machine learning" in index
        assert "neural networks" in index
        assert "web development" in index
        assert "status" in index

    def test_find_exact_match(self, temp_vault):
        """Test finding notes with exact title match."""
        linker = NoteLinkEngine(temp_vault)

        links = linker.find_related_notes(["Python"], [])

        assert len(links) == 1
        assert "Python" in links[0]
        assert "04 Indexes/Python" in links[0]

    def test_find_substring_match(self, temp_vault):
        """Test finding notes where term is in title."""
        linker = NoteLinkEngine(temp_vault)

        # "Learning" should match "Machine Learning"
        links = linker.find_related_notes(["Learning"], [])

        assert len(links) == 1
        assert "Machine Learning" in links[0]

    def test_find_reverse_match(self, temp_vault):
        """Test finding notes where title is in term."""
        linker = NoteLinkEngine(temp_vault)

        # "Python Programming" should match "Python"
        links = linker.find_related_notes(["Python Programming"], [])

        assert len(links) >= 1
        assert any("Python" in link for link in links)

    def test_find_multiple_matches(self, temp_vault):
        """Test finding multiple related notes."""
        linker = NoteLinkEngine(temp_vault)

        links = linker.find_related_notes(
            ["Python", "Neural Networks"], ["Machine Learning"]
        )

        assert len(links) == 3
        titles = " ".join(links)
        assert "Python" in titles
        assert "Neural Networks" in titles
        assert "Machine Learning" in titles

    def test_no_duplicates(self, temp_vault):
        """Test that duplicate matches are not returned."""
        linker = NoteLinkEngine(temp_vault)

        # Both should match Python, but only one link returned
        links = linker.find_related_notes(["Python", "python"], [])

        assert len(links) == 1

    def test_limit_results(self, temp_vault):
        """Test that results are limited to 7."""
        linker = NoteLinkEngine(temp_vault)

        # Even with many search terms, limit to 7
        links = linker.find_related_notes(
            ["Python", "Machine Learning", "Neural Networks", "REST API", "Web"],
            ["Status", "Development", "Programming", "API"],
        )

        assert len(links) <= 7

    def test_make_wikilink(self, temp_vault):
        """Test wikilink generation."""
        linker = NoteLinkEngine(temp_vault)
        path = temp_vault / "04 Indexes" / "Python.md"

        link = linker._make_wikilink(path, "Python")

        assert link == "[[04 Indexes/Python|Python]]"

    def test_nested_folder_wikilink(self, temp_vault):
        """Test wikilink for nested folders."""
        linker = NoteLinkEngine(temp_vault)
        path = temp_vault / "04 Indexes" / "Programming" / "Web Development.md"

        link = linker._make_wikilink(path, "Web Development")

        assert link == "[[04 Indexes/Programming/Web Development|Web Development]]"

    def test_cache_is_used(self, temp_vault):
        """Test that index cache is reused."""
        linker = NoteLinkEngine(temp_vault)

        # Build index twice
        index1 = linker._build_index()
        index2 = linker._build_index()

        # Should be same object (cached)
        assert index1 is index2

    def test_cache_clear(self, temp_vault):
        """Test cache clearing."""
        linker = NoteLinkEngine(temp_vault)

        linker._build_index()
        linker.clear_cache()

        assert linker._index_cache is None
        assert linker._cache_time is None

    def test_missing_folders_handled(self, temp_vault):
        """Test that missing scan folders don't cause errors."""
        # Create vault with only some folders
        with tempfile.TemporaryDirectory() as tmpdir:
            vault = Path(tmpdir)
            (vault / "04 Indexes").mkdir()
            (vault / "04 Indexes" / "Test.md").write_text("# Test")

            linker = NoteLinkEngine(vault)
            links = linker.find_related_notes(["Test"], [])

            assert len(links) == 1

    def test_case_insensitive_matching(self, temp_vault):
        """Test that matching is case-insensitive."""
        linker = NoteLinkEngine(temp_vault)

        links = linker.find_related_notes(["PYTHON"], [])

        assert len(links) == 1
        assert "Python" in links[0]  # Original casing preserved
