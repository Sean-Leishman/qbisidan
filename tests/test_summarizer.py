"""Tests for summarizer module - SummaryResult and JSON parsing."""
import pytest

from src.summarizer import Summarizer, SummaryResult


class TestSummaryResult:
    """Test SummaryResult dataclass."""

    def test_default_values(self):
        """Test SummaryResult initializes with correct defaults."""
        result = SummaryResult(summary="Test summary")

        assert result.summary == "Test summary"
        assert result.category == "Other"
        assert result.tags == []
        assert result.action_items == []
        assert result.review_questions == []
        assert result.key_concepts == []

    def test_full_initialization(self):
        """Test SummaryResult with all fields."""
        result = SummaryResult(
            summary="Full summary",
            category="Programming",
            tags=["Python", "Testing"],
            action_items=["Write tests", "Review code"],
            review_questions=["What is pytest?"],
            key_concepts=["Unit Testing", "Mocking"],
        )

        assert result.summary == "Full summary"
        assert result.category == "Programming"
        assert result.tags == ["Python", "Testing"]
        assert result.action_items == ["Write tests", "Review code"]
        assert result.review_questions == ["What is pytest?"]
        assert result.key_concepts == ["Unit Testing", "Mocking"]


class TestParseStructuredResponse:
    """Test JSON parsing from AI responses."""

    @pytest.fixture
    def summarizer(self):
        """Create summarizer without API calls."""
        # Create with dummy values - we won't call the API
        s = Summarizer.__new__(Summarizer)
        s.routing_categories = ["Programming", "Machine Learning", "Other"]
        return s

    def test_parse_json_with_code_fence(self, summarizer):
        """Test parsing JSON in code fence."""
        response = """Here is a summary of the article.

It covers important topics about Python programming.

```json
{
  "category": "Programming",
  "tags": ["Python", "Web Development"],
  "action_items": ["Learn Flask", "Build API"],
  "review_questions": ["What is Flask?", "How do routes work?"],
  "key_concepts": ["REST API", "HTTP Methods"]
}
```"""

        result = summarizer._parse_structured_response(response)

        assert "summary of the article" in result.summary
        assert result.category == "Programming"
        assert result.tags == ["Python", "Web Development"]
        assert result.action_items == ["Learn Flask", "Build API"]
        assert result.review_questions == ["What is Flask?", "How do routes work?"]
        assert result.key_concepts == ["REST API", "HTTP Methods"]

    def test_parse_json_without_code_fence(self, summarizer):
        """Test parsing raw JSON without code fence."""
        response = """This is a summary about machine learning.

{"category": "Machine Learning", "tags": ["Neural Networks"], "action_items": [], "review_questions": ["What is backprop?"], "key_concepts": ["Gradient Descent"]}"""

        result = summarizer._parse_structured_response(response)

        assert "machine learning" in result.summary
        assert result.category == "Machine Learning"
        assert result.tags == ["Neural Networks"]
        assert result.review_questions == ["What is backprop?"]

    def test_parse_no_json_returns_full_response(self, summarizer):
        """Test fallback when no JSON found."""
        response = "This is just a plain summary without any JSON."

        result = summarizer._parse_structured_response(response)

        assert result.summary == response
        assert result.category == "Other"
        assert result.tags == []

    def test_parse_invalid_json_returns_fallback(self, summarizer):
        """Test fallback when JSON is malformed."""
        response = """Summary here.

```json
{invalid json content}
```"""

        result = summarizer._parse_structured_response(response)

        assert "Summary here" in result.summary
        assert result.category == "Other"

    def test_parse_partial_json_fields(self, summarizer):
        """Test parsing JSON with only some fields."""
        response = """Quick summary.

```json
{
  "category": "Finance",
  "tags": ["Investing"]
}
```"""

        result = summarizer._parse_structured_response(response)

        assert result.category == "Finance"
        assert result.tags == ["Investing"]
        assert result.action_items == []
        assert result.review_questions == []
        assert result.key_concepts == []
