"""Tests for topic_filter module — index parsing, out-of-range guard, fail-open behavior."""
import pytest

from src.agents.topic_filter import TopicFilterAgent


@pytest.fixture
def agent():
    """TopicFilterAgent instance that never touches an API; _call is stubbed per-test."""
    return TopicFilterAgent.__new__(TopicFilterAgent)


class TestFilterTitles:
    def test_empty_titles_returns_empty(self, agent):
        assert agent.filter_titles([], "cooking") == []

    def test_keeps_valid_indices(self, agent, monkeypatch):
        monkeypatch.setattr(agent, "_call", lambda prompt: "[0, 2]")
        result = agent.filter_titles(["a", "b", "c"], "topic")
        assert result == [0, 2]

    def test_fenced_json_response(self, agent, monkeypatch):
        monkeypatch.setattr(agent, "_call", lambda prompt: "```json\n[1]\n```")
        result = agent.filter_titles(["a", "b"], "topic")
        assert result == [1]

    def test_out_of_range_indices_are_dropped(self, agent, monkeypatch):
        """A hallucinating model returning bad indices must not crash stubs[i] downstream."""
        monkeypatch.setattr(agent, "_call", lambda prompt: "[0, 5, -1, 99]")
        result = agent.filter_titles(["a", "b", "c"], "topic")
        assert result == [0]

    def test_empty_array_means_nothing_relevant_not_keep_all(self, agent, monkeypatch):
        """A valid [] is a real 'none match' answer — must NOT fall back to keep-all."""
        monkeypatch.setattr(agent, "_call", lambda prompt: "[]")
        result = agent.filter_titles(["a", "b", "c"], "topic")
        assert result == []

    def test_all_out_of_range_falls_back_to_keep_all(self, agent, monkeypatch):
        monkeypatch.setattr(agent, "_call", lambda prompt: "[5, 6, 7]")
        result = agent.filter_titles(["a", "b", "c"], "topic")
        assert result == [0, 1, 2]

    def test_garbage_response_keeps_all(self, agent, monkeypatch):
        monkeypatch.setattr(agent, "_call", lambda prompt: "not json at all")
        result = agent.filter_titles(["a", "b", "c"], "topic")
        assert result == [0, 1, 2]

    def test_non_int_entries_ignored(self, agent, monkeypatch):
        monkeypatch.setattr(agent, "_call", lambda prompt: '[0, "one", null]')
        result = agent.filter_titles(["a", "b", "c"], "topic")
        assert result == [0]

    def test_llm_call_raises_keeps_all(self, agent, monkeypatch):
        def boom(prompt):
            raise RuntimeError("api down")

        monkeypatch.setattr(agent, "_call", boom)
        result = agent.filter_titles(["a", "b", "c"], "topic")
        assert result == [0, 1, 2]

    def test_duplicate_indices_deduped_and_sorted(self, agent, monkeypatch):
        monkeypatch.setattr(agent, "_call", lambda prompt: "[2, 0, 2, 0]")
        result = agent.filter_titles(["a", "b", "c"], "topic")
        assert result == [0, 2]


class TestIsRelevant:
    def test_yes_response_is_relevant(self, agent, monkeypatch):
        monkeypatch.setattr(agent, "_call", lambda prompt: "YES")
        assert agent.is_relevant("title", "excerpt", "topic") is True

    def test_no_response_is_not_relevant(self, agent, monkeypatch):
        monkeypatch.setattr(agent, "_call", lambda prompt: "NO")
        assert agent.is_relevant("title", "excerpt", "topic") is False

    def test_lowercase_no(self, agent, monkeypatch):
        monkeypatch.setattr(agent, "_call", lambda prompt: "no, unrelated")
        assert agent.is_relevant("title", "excerpt", "topic") is False

    def test_garbage_response_keeps_relevant(self, agent, monkeypatch):
        monkeypatch.setattr(agent, "_call", lambda prompt: "uh honestly not sure")
        assert agent.is_relevant("title", "excerpt", "topic") is True

    def test_llm_call_raises_keeps_relevant(self, agent, monkeypatch):
        def boom(prompt):
            raise RuntimeError("api down")

        monkeypatch.setattr(agent, "_call", boom)
        assert agent.is_relevant("title", "excerpt", "topic") is True
