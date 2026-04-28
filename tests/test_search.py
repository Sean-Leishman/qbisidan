"""Tests for VaultSearch — full-text search across vault notes."""
import tempfile
from pathlib import Path

import pytest

from src.search import VaultSearch, format_search_results


@pytest.fixture
def vault(tmp_path):
    """Create a minimal vault with varied content."""
    # Sources
    sources = tmp_path / "02 Sources" / "Articles" / "ML"
    sources.mkdir(parents=True)
    (sources / "Attention Mechanism.md").write_text(
        "---\nsource: https://example.com/attention\n---\n"
        "# Attention Mechanism\n"
        "Attention is a soft dictionary lookup. Keys and queries determine relevance.\n"
        "Transformers rely on self-attention to model long-range dependencies.\n"
    )
    (sources / "Gradient Descent.md").write_text(
        "# Gradient Descent\n"
        "An optimisation algorithm. Learning rate controls step size.\n"
    )

    videos = tmp_path / "02 Sources" / "Videos"
    videos.mkdir(parents=True)
    (videos / "Python Tutorial.md").write_text(
        "# Python Tutorial\n"
        "Learn Python from scratch. Variables, loops, functions.\n"
    )

    # Tags / Indexes
    tags = tmp_path / "03 Tags"
    tags.mkdir()
    (tags / "Machine Learning.md").write_text("# Machine Learning\n")

    indexes = tmp_path / "04 Indexes"
    indexes.mkdir()
    (indexes / "Python.md").write_text("# Python\n")

    # Daily notes — should be deprioritised
    daily = tmp_path / "06 Daily Notes"
    daily.mkdir()
    (daily / "2026-04-05.md").write_text(
        "# 2026-04-05\n## Notes\n- Interesting attention paper today\n"
    )

    # Trackers — deprioritised
    trackers = tmp_path / "08 Trackers"
    trackers.mkdir()
    (trackers / "Inbox.md").write_text("# Inbox\n- Read attention paper\n")

    return tmp_path


class TestVaultSearch:
    def test_finds_exact_title_match(self, vault):
        results = VaultSearch(vault).search("Attention Mechanism")
        assert results, "expected at least one result"
        assert results[0].title == "Attention Mechanism"

    def test_finds_body_content(self, vault):
        results = VaultSearch(vault).search("soft dictionary lookup")
        titles = [r.title for r in results]
        assert "Attention Mechanism" in titles

    def test_title_scores_higher_than_body(self, vault):
        # "attention" appears in the title of one note and in the body of the daily note
        results = VaultSearch(vault).search("attention")
        assert results[0].title == "Attention Mechanism"

    def test_daily_notes_deprioritised(self, vault):
        results = VaultSearch(vault).search("attention")
        titles = [r.title for r in results]
        # Daily note should appear after the dedicated article
        if "2026-04-05" in titles:
            assert titles.index("Attention Mechanism") < titles.index("2026-04-05")

    def test_trackers_deprioritised(self, vault):
        results = VaultSearch(vault).search("attention")
        titles = [r.title for r in results]
        if "Inbox" in titles:
            assert titles.index("Attention Mechanism") < titles.index("Inbox")

    def test_max_five_results(self, vault):
        # Create more than 5 matching files
        extra = vault / "02 Sources" / "Articles"
        extra.mkdir(exist_ok=True)
        for i in range(10):
            (extra / f"Note {i}.md").write_text(f"# Note {i}\nattention attention\n")
        results = VaultSearch(vault).search("attention")
        assert len(results) <= 5

    def test_no_results_for_missing_term(self, vault):
        results = VaultSearch(vault).search("xyznonexistentterm")
        assert results == []

    def test_empty_query_returns_empty(self, vault):
        results = VaultSearch(vault).search("")
        assert results == []

    def test_multi_word_query(self, vault):
        results = VaultSearch(vault).search("soft dictionary")
        titles = [r.title for r in results]
        assert "Attention Mechanism" in titles

    def test_result_has_folder(self, vault):
        results = VaultSearch(vault).search("attention mechanism")
        assert results[0].folder != ""
        assert "02 Sources" in results[0].folder

    def test_result_has_snippet(self, vault):
        results = VaultSearch(vault).search("attention")
        assert results[0].snippet != ""

    def test_snippet_length_bounded(self, vault):
        results = VaultSearch(vault).search("attention")
        for r in results:
            assert len(r.snippet) <= 250  # some slack for the ellipsis

    def test_hidden_dirs_skipped(self, vault):
        hidden = vault / ".smart-connections"
        hidden.mkdir()
        (hidden / "embeddings.md").write_text("# attention\n" * 100)
        results = VaultSearch(vault).search("attention")
        titles = [r.title for r in results]
        assert "embeddings" not in titles

    def test_case_insensitive(self, vault):
        upper = VaultSearch(vault).search("ATTENTION")
        lower = VaultSearch(vault).search("attention")
        assert [r.title for r in upper] == [r.title for r in lower]


class TestFormatSearchResults:
    def test_no_results_message(self):
        msg = format_search_results("foo", [])
        assert "No results" in msg
        assert "foo" in msg

    def test_result_count_in_header(self, vault):
        from src.search import SearchResult
        results = [
            SearchResult(title="Note A", folder="02 Sources", snippet="Some text", score=5.0),
            SearchResult(title="Note B", folder="03 Tags", snippet="Other text", score=3.0),
        ]
        msg = format_search_results("python", results)
        assert "2 results" in msg

    def test_titles_in_output(self, vault):
        from src.search import SearchResult
        results = [
            SearchResult(title="Attention Mechanism", folder="02 Sources/Articles/ML", snippet="Keys and queries", score=10.0),
        ]
        msg = format_search_results("attention", results)
        assert "Attention Mechanism" in msg
        assert "02 Sources/Articles/ML" in msg
        assert "Keys and queries" in msg

    def test_singular_result_label(self):
        from src.search import SearchResult
        results = [SearchResult(title="X", folder="", snippet="", score=1.0)]
        msg = format_search_results("x", results)
        assert "1 result" in msg
        assert "1 results" not in msg
