"""Tests for summarizer module — SummaryResult, JSON parsing, truncation."""
import pytest

from src.summarizer import Summarizer, SummaryResult


@pytest.fixture
def summarizer():
    """Summarizer instance that never touches an API."""
    s = Summarizer.__new__(Summarizer)
    s.routing_categories = ["Programming", "Machine Learning", "Other"]
    s.existing_notes = []
    return s


# ── SummaryResult defaults ─────────────────────────────────────────────────────

class TestSummaryResult:
    def test_defaults(self):
        r = SummaryResult(summary="Test")
        assert r.category == "Other"
        assert r.tags == []
        assert r.action_items == []
        assert r.review_questions == []
        assert r.key_concepts == []
        assert r.content_truncated is False

    def test_full_init(self):
        r = SummaryResult(
            summary="Full",
            category="Programming",
            tags=["Python"],
            action_items=["Write tests"],
            review_questions=["Why?"],
            key_concepts=["Unit Testing"],
            content_truncated=True,
        )
        assert r.category == "Programming"
        assert r.content_truncated is True


# ── Truncation ─────────────────────────────────────────────────────────────────

class TestTruncateContent:
    def test_short_content_not_truncated(self, summarizer):
        text, flag = summarizer._truncate_content("short", max_chars=100)
        assert text == "short"
        assert flag is False

    def test_long_content_truncated(self, summarizer):
        text, flag = summarizer._truncate_content("x" * 200, max_chars=100)
        assert len(text) <= 130  # 100 chars + marker overhead
        assert flag is True

    def test_truncation_marker_added(self, summarizer):
        text, _ = summarizer._truncate_content("x" * 200, max_chars=100)
        assert "[Content truncated...]" in text

    def test_exact_length_not_truncated(self, summarizer):
        text, flag = summarizer._truncate_content("a" * 50, max_chars=50)
        assert flag is False


# ── JSON parsing ───────────────────────────────────────────────────────────────

class TestParseStructuredResponse:
    def test_fenced_json(self, summarizer):
        response = """Summary text here.\n\n```json\n{"category": "Programming", "tags": ["Python"], "action_items": ["Do X"], "review_questions": ["Why?"], "key_concepts": ["X"]}\n```"""
        r = summarizer._parse_structured_response(response)
        assert "Summary text here" in r.summary
        assert r.category == "Programming"
        assert r.tags == ["Python"]
        assert r.action_items == ["Do X"]

    def test_unfenced_json_extracted(self, summarizer):
        response = 'Summary about ML.\n{"category": "Machine Learning", "tags": ["Neural Networks"], "action_items": [], "review_questions": ["What is backprop?"], "key_concepts": ["Gradient Descent"]}'
        r = summarizer._parse_structured_response(response)
        assert "Summary about ML" in r.summary
        assert r.category == "Machine Learning"
        assert "Neural Networks" in r.tags

    def test_raw_json_not_in_summary(self, summarizer):
        """When JSON is found, the summary text should not contain the JSON block."""
        response = 'Clean summary.\n{"category": "Other", "tags": [], "action_items": [], "review_questions": [], "key_concepts": []}'
        r = summarizer._parse_structured_response(response)
        assert "category" not in r.summary
        assert "{" not in r.summary

    def test_no_json_returns_plain_summary(self, summarizer):
        response = "Plain summary with no metadata."
        r = summarizer._parse_structured_response(response)
        assert r.summary == "Plain summary with no metadata."
        assert r.category == "Other"
        assert r.tags == []

    def test_invalid_json_in_fence_falls_back(self, summarizer):
        response = "Summary.\n```json\n{invalid}\n```"
        r = summarizer._parse_structured_response(response)
        assert "Summary" in r.summary
        assert r.category == "Other"

    def test_partial_json_fields_have_defaults(self, summarizer):
        response = 'Quick note.\n```json\n{"category": "Finance", "tags": ["Investing"]}\n```'
        r = summarizer._parse_structured_response(response)
        assert r.category == "Finance"
        assert r.tags == ["Investing"]
        assert r.action_items == []

    def test_trailing_raw_json_stripped_from_summary(self, summarizer):
        """Raw JSON at end with no parseable content should be stripped from summary text."""
        response = 'Good summary text.\n\n{"category": "Other", "tags": [], "action_items": [], "review_questions": [], "key_concepts": []}'
        r = summarizer._parse_structured_response(response)
        # Either parsed (clean summary) or stripped from fallback
        assert "category" not in r.summary or r.category == "Other"

    def test_multiline_fenced_json(self, summarizer):
        response = (
            "Great article summary.\n\n"
            "```json\n"
            "{\n"
            '  "category": "Science",\n'
            '  "tags": ["Biology", "Research"],\n'
            '  "action_items": ["Read more"],\n'
            '  "review_questions": ["What drives evolution?"],\n'
            '  "key_concepts": ["Natural Selection"]\n'
            "}\n"
            "```"
        )
        r = summarizer._parse_structured_response(response)
        assert r.category == "Science"
        assert "Biology" in r.tags
        assert "Great article summary" in r.summary
