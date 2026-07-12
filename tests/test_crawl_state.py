"""Tests for crawl_state module — CrawlRunState defaults, store round-trip/resume."""
from pathlib import Path

from src.crawl_state import CrawlRunState, CrawlStateStore


class TestCrawlRunState:
    def test_defaults_are_empty_lists(self):
        state = CrawlRunState()
        assert state.processed_ids == []
        assert state.filtered_out_ids == []
        assert state.created_titles == []
        assert state.skipped_titles == []
        assert state.errors == []

    def test_defaults_are_independent_instances(self):
        """Mutating one instance's list must not leak into another (default_factory, not shared)."""
        a = CrawlRunState()
        b = CrawlRunState()
        a.processed_ids.append("v1")
        assert b.processed_ids == []


class TestMakeKey:
    def test_key_includes_all_parts(self):
        key = CrawlStateStore.make_key("mychannel", "2026-01-01", "2026-02-01", "cooking")
        assert "mychannel" in key
        assert "2026-01-01" in key
        assert "2026-02-01" in key
        assert "cooking" in key

    def test_key_handles_none_topic(self):
        key = CrawlStateStore.make_key("mychannel", "all", "all", None)
        assert "mychannel" in key

    def test_different_topics_yield_different_keys(self):
        k1 = CrawlStateStore.make_key("c", "all", "all", "food")
        k2 = CrawlStateStore.make_key("c", "all", "all", "coding")
        assert k1 != k2


class TestCrawlStateStore:
    def test_load_unknown_key_returns_fresh_state(self, tmp_path):
        store = CrawlStateStore(str(tmp_path / "state.json"))
        state = store.load("nope")
        assert state == CrawlRunState()

    def test_save_then_load_round_trip(self, tmp_path):
        store = CrawlStateStore(str(tmp_path / "state.json"))
        state = CrawlRunState(
            processed_ids=["v1", "v2"],
            filtered_out_ids=["v3"],
            created_titles=["Title 1"],
            skipped_titles=["Title 2"],
            errors=["v4: boom"],
        )
        store.save("key1", state)

        loaded = store.load("key1")
        assert loaded == state

    def test_save_writes_file_to_disk(self, tmp_path):
        state_file = tmp_path / "state.json"
        store = CrawlStateStore(str(state_file))
        store.save("key1", CrawlRunState(processed_ids=["v1"]))
        assert state_file.exists()

    def test_save_creates_parent_dirs(self, tmp_path):
        state_file = tmp_path / "nested" / "dir" / "state.json"
        store = CrawlStateStore(str(state_file))
        store.save("key1", CrawlRunState(processed_ids=["v1"]))
        assert state_file.exists()

    def test_multiple_keys_coexist(self, tmp_path):
        store = CrawlStateStore(str(tmp_path / "state.json"))
        store.save("key1", CrawlRunState(processed_ids=["v1"]))
        store.save("key2", CrawlRunState(processed_ids=["v2"]))

        assert store.load("key1").processed_ids == ["v1"]
        assert store.load("key2").processed_ids == ["v2"]

    def test_resume_across_multiple_saves(self, tmp_path):
        """Simulates .save() after every video: state accumulates correctly."""
        store = CrawlStateStore(str(tmp_path / "state.json"))
        key = "resume-key"

        state = store.load(key)
        state.processed_ids.append("v1")
        store.save(key, state)

        # Simulate a fresh process picking the key back up mid-crawl.
        state2 = store.load(key)
        assert state2.processed_ids == ["v1"]
        state2.processed_ids.append("v2")
        store.save(key, state2)

        state3 = store.load(key)
        assert state3.processed_ids == ["v1", "v2"]

    def test_clear_removes_key(self, tmp_path):
        store = CrawlStateStore(str(tmp_path / "state.json"))
        store.save("key1", CrawlRunState(processed_ids=["v1"]))
        store.clear("key1")
        assert store.load("key1") == CrawlRunState()

    def test_clear_leaves_other_keys_intact(self, tmp_path):
        store = CrawlStateStore(str(tmp_path / "state.json"))
        store.save("key1", CrawlRunState(processed_ids=["v1"]))
        store.save("key2", CrawlRunState(processed_ids=["v2"]))
        store.clear("key1")
        assert store.load("key1") == CrawlRunState()
        assert store.load("key2").processed_ids == ["v2"]

    def test_clear_unknown_key_is_a_noop(self, tmp_path):
        store = CrawlStateStore(str(tmp_path / "state.json"))
        store.clear("nope")  # must not raise

    def test_load_survives_corrupt_file(self, tmp_path):
        state_file = tmp_path / "state.json"
        state_file.write_text("{not valid json")
        store = CrawlStateStore(str(state_file))
        assert store.load("key1") == CrawlRunState()
