"""Tests for place_classifier — one classifier for Instagram saves and screenshots alike."""
import json

import pytest

from src.agents.place_classifier import BATCH, UNSORTED, PlaceClassifierAgent


@pytest.fixture
def agent():
    """Never touches an API; _call is stubbed per-test."""
    return PlaceClassifierAgent.__new__(PlaceClassifierAgent)


def answer(*rows):
    return lambda prompt: json.dumps(list(rows))


class TestClassify:
    def test_sorts_a_mix_of_sources_with_one_call(self, agent, monkeypatch):
        """An Instagram caption and screenshot text go through the same classifier."""
        calls = []
        monkeypatch.setattr(agent, "_call", lambda p: calls.append(p) or json.dumps([
            {"i": 0, "category": "pub", "venue": "The Royal Oak", "area": "Hackney"},
            {"i": 1, "category": "food", "venue": "Bao Soho", "area": "Soho, London"},
            {"i": 2, "category": "other", "venue": None, "area": None},
        ]))
        got = agent.classify([
            "best pint in east london 🍺 @theroyaloak",              # Instagram caption
            "BAO Soho 53 Lexington St Open 12-10pm",                 # screenshot, read by vision
            "5 ways to fold a fitted sheet",                          # neither
        ])
        assert len(calls) == 1, "a batch is one call, not one per item"
        assert [p.category for p in got] == ["pub", "food", "other"]
        assert got[1].venue == "Bao Soho" and got[1].area == "Soho, London"
        assert got[2].venue is None

    def test_other_and_unsorted_are_different_answers(self, agent, monkeypatch):
        """'not a place' is a decision; 'no answer' is a failure. They must not look alike."""
        monkeypatch.setattr(agent, "_call", answer({"i": 0, "category": "other"}))
        decided = agent.classify(["a meme"])
        monkeypatch.setattr(agent, "_call", lambda p: "I'm not sure, sorry!")
        failed = agent.classify(["a meme"])
        assert decided[0].category == "other"
        assert failed[0].category == UNSORTED

    def test_api_failure_leaves_everything_unsorted_not_dropped(self, agent, monkeypatch):
        def boom(prompt):
            raise RuntimeError("503")
        monkeypatch.setattr(agent, "_call", boom)
        got = agent.classify(["a", "b", "c"])
        assert [p.category for p in got] == [UNSORTED] * 3, "a failed call loses nothing"

    def test_invented_category_and_bad_indices_are_contained(self, agent, monkeypatch):
        monkeypatch.setattr(agent, "_call", answer(
            {"i": 0, "category": "brunch"},                     # not a real category
            {"i": 1, "category": "PUB", "venue": " The Plough "},
            {"i": 7, "category": "food", "venue": "Ghost"},      # out of range
            {"i": -1, "category": "food"},
            "garbage",
        ))
        got = agent.classify(["x", "y"])
        assert got[0].category == UNSORTED, "an invented category is a failure for that item"
        assert got[1].category == "pub" and got[1].venue == "The Plough"
        assert len(got) == 2, "a hallucinated index never adds or overwrites an item"

    def test_missing_items_stay_unsorted(self, agent, monkeypatch):
        monkeypatch.setattr(agent, "_call", answer({"i": 1, "category": "activity"}))
        got = agent.classify(["first", "second", "third"])
        assert [p.category for p in got] == [UNSORTED, "activity", UNSORTED]

    def test_null_ish_names_become_none(self, agent, monkeypatch):
        monkeypatch.setattr(agent, "_call", answer({"i": 0, "category": "food", "venue": "null", "area": "  "}))
        got = agent.classify(["best spot in town 🔥"])
        assert got[0].venue is None and got[0].area is None, "no invented venue for a vague caption"

    def test_fenced_json(self, agent, monkeypatch):
        monkeypatch.setattr(agent, "_call", lambda p: '```json\n[{"i": 0, "category": "travel", "area": "Lisbon"}]\n```')
        got = agent.classify(["rooftop sunsets in lisbon"])
        assert got[0].category == "travel" and got[0].area == "Lisbon"

    def test_large_input_is_batched_and_order_is_kept(self, agent, monkeypatch):
        calls = []

        def fake(prompt):
            calls.append(prompt)
            n = prompt.split("Items:\n", 1)[1].split("\n\nAnswer", 1)[0].count("\n") + 1
            return json.dumps([{"i": i, "category": "food", "venue": f"v{len(calls)}-{i}"} for i in range(n)])

        monkeypatch.setattr(agent, "_call", fake)
        got = agent.classify([f"item {k}" for k in range(BATCH * 2 + 3)])
        assert len(calls) == 3
        assert len(got) == BATCH * 2 + 3
        assert got[0].venue == "v1-0" and got[BATCH].venue == "v2-0" and got[-1].venue == "v3-2"

    def test_empty(self, agent):
        assert agent.classify([]) == []


def test_classifier_turns_gemini_thinking_off():
    """Thinking shares the output budget; for a mechanical task it truncated the JSON."""
    seen = {}

    class FakeModels:
        def generate_content(self, model, contents, config):
            seen["config"] = config
            return type("R", (), {"text": "[]"})()

    agent = PlaceClassifierAgent.__new__(PlaceClassifierAgent)
    agent.provider, agent.model, agent.max_tokens = "gemini", "m", 100
    agent.client = type("C", (), {"models": FakeModels()})()
    agent._call_gemini("x")
    assert seen["config"].thinking_config.thinking_budget == 0
    assert seen["config"].temperature == 0, "a classifier must not flip a venue between runs"


def test_a_handle_never_reaches_the_geocoder(agent, monkeypatch):
    monkeypatch.setattr(agent, "_call", answer({"i": 0, "category": "pub", "venue": "@theroyaloakhackney", "area": "Hackney"}))
    got = agent.classify(["best pint @theroyaloakhackney"])
    assert got[0].category == "pub" and got[0].venue is None and got[0].area == "Hackney"


def test_why_is_kept_short_and_null_ish_is_none(agent, monkeypatch):
    monkeypatch.setattr(agent, "_call", answer(
        {"i": 0, "category": "food", "venue": "Dishoom", "why": "  Black daal; worth the queue. "},
        {"i": 1, "category": "pub", "venue": "X", "why": "null"},
        {"i": 2, "category": "pub", "venue": "Y", "why": "word " * 40},
    ))
    got = agent.classify(["a", "b", "c"])
    assert got[0].why == "Black daal; worth the queue"
    assert got[1].why is None
    assert len(got[2].why) <= 90 and got[2].why.endswith("\u2026"), "a rambling answer is capped"


def test_prompt_forbids_reasons_the_text_does_not_give():
    """The reason must come from the saved text -- a made-up reputation is a wrong pin in prose."""
    prompt = PlaceClassifierAgent.PROMPT
    assert "ONLY from the text" in prompt and "Never add" in prompt
