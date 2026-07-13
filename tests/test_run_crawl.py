"""Tests for Processor.run_crawl — the generalised engine behind both /crawl
and /backfill (Phase 4). Built via Processor.__new__ (same idiom as
test_manifest.py) so no real AI clients, Telegram token, or vault path are
needed — only the attributes run_crawl actually touches. No network.

Focus:
  - the filter-on-cheap-metadata ordering guarantee: an interest-filter
    rejection must never reach an expensive scrape (the whole point of
    Phase 4 — this is what protects the 18M-token blind-transcription case).
  - the pre-existing YouTube-shaped path (no cheap metadata) is unchanged.
  - resume across invocations.
  - max_items capping is logged and surfaced in the result message, not silent.
"""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from src.crawl_state import CrawlRunState, CrawlStateStore
from src.processor import Processor, ProcessingResult
from src.scrapers.channel import VideoStub


class FakeCheapScraper:
    """Shape of a scraper with a cheap metadata path (like InstagramScraper):
    get_metadata is metadata-only, scrape() is the expensive download+AI path."""

    def __init__(self, metadata=None, fail_metadata_urls=()):
        self.metadata = metadata or {}
        self.fail_metadata_urls = set(fail_metadata_urls)
        self.metadata_calls = []
        self.scrape_calls = []

    def can_handle(self, url):
        return True

    def get_metadata(self, url):
        self.metadata_calls.append(url)
        if url in self.fail_metadata_urls:
            raise RuntimeError("metadata fetch failed")
        return self.metadata.get(url, {"title": "cheap title", "caption": "a caption"})

    def scrape(self, url):
        self.scrape_calls.append(url)
        return SimpleNamespace(
            url=url, title="scraped title", description="", content="expensive content",
            content_type="instagram",
        )


class FakeExpensiveOnlyScraper:
    """Shape of a scraper with no cheap path (like YouTubeScraper) — the only
    way to see any content is the full scrape()."""

    def __init__(self):
        self.scrape_calls = []

    def can_handle(self, url):
        return True

    def scrape(self, url):
        self.scrape_calls.append(url)
        return SimpleNamespace(
            url=url, title="scraped title", description="", content="transcript",
            content_type="youtube",
        )


def make_processor(tmp_path, scraper, topic_filter=None, linker_hit=None):
    p = Processor.__new__(Processor)
    p.config = SimpleNamespace(
        channel_crawl=SimpleNamespace(
            max_videos=25,
            title_filter_threshold=25,
            sleep_seconds=0,
            transcript_filter_chars=1500,
            manifest_timeout_seconds=120.0,
        ),
    )
    p.scrapers = [scraper]
    p.crawl_state = CrawlStateStore(str(tmp_path / "state.json"))
    p.linker = MagicMock()
    p.linker.find_note_by_url.return_value = linker_hit
    p.vault_writer = MagicMock()
    p.vault_writer.write_channel_index.return_value = Path("index.md")
    p.topic_filter = topic_filter or MagicMock()
    p._save_scraped = MagicMock(
        return_value=ProcessingResult(url="x", success=True, title="Saved Title", folder="folder")
    )
    return p


def make_stubs(n):
    return [
        VideoStub(video_id=f"v{i}", url=f"https://example.com/{i}", title=f"Item {i}", upload_date=None)
        for i in range(n)
    ]


class TestFilterOnCheapMetadataOrdering:
    def test_rejected_item_never_reaches_expensive_scrape(self, tmp_path):
        """The crux of Phase 4: an interest-filter rejection must never pay
        for a download + Gemini call."""
        scraper = FakeCheapScraper()
        topic_filter = MagicMock()
        topic_filter.is_relevant.return_value = False
        p = make_processor(tmp_path, scraper, topic_filter=topic_filter)
        stubs = make_stubs(1)

        result = p.run_crawl(stubs, state_key="k", display_name="Test", topic="food")

        assert scraper.metadata_calls == ["https://example.com/0"]
        assert scraper.scrape_calls == []  # never reached
        p._save_scraped.assert_not_called()
        assert result.success

        state = p.crawl_state.load("k")
        # state was cleared on successful completion of the run; re-derive
        # via the fact save() was called with filtered_out before the clear —
        # simplest proxy is just checking the run didn't error.
        assert state == CrawlRunState()

    def test_accepted_item_reaches_expensive_scrape(self, tmp_path):
        scraper = FakeCheapScraper()
        topic_filter = MagicMock()
        topic_filter.is_relevant.return_value = True
        p = make_processor(tmp_path, scraper, topic_filter=topic_filter)
        stubs = make_stubs(1)

        p.run_crawl(stubs, state_key="k", display_name="Test", topic="food")

        assert scraper.scrape_calls == ["https://example.com/0"]
        p._save_scraped.assert_called_once()

    def test_cheap_metadata_filter_uses_metadata_not_stub_title(self, tmp_path):
        scraper = FakeCheapScraper(
            metadata={"https://example.com/0": {"title": "Actual caption title", "caption": "yum"}}
        )
        topic_filter = MagicMock()
        topic_filter.is_relevant.return_value = True
        p = make_processor(tmp_path, scraper, topic_filter=topic_filter)
        stubs = make_stubs(1)

        p.run_crawl(stubs, state_key="k", display_name="Test", topic="food")

        args = topic_filter.is_relevant.call_args[0]
        assert args[0] == "Actual caption title"
        assert args[1] == "yum"

    def test_metadata_fetch_failure_falls_back_to_post_scrape_filter(self, tmp_path):
        """If the cheap fetch itself errors, fail open to the old
        scrape-then-filter path rather than silently dropping the item."""
        scraper = FakeCheapScraper(fail_metadata_urls=["https://example.com/0"])
        topic_filter = MagicMock()
        topic_filter.is_relevant.return_value = True
        p = make_processor(tmp_path, scraper, topic_filter=topic_filter)
        stubs = make_stubs(1)

        p.run_crawl(stubs, state_key="k", display_name="Test", topic="food")

        assert scraper.scrape_calls == ["https://example.com/0"]

    def test_no_topic_skips_cheap_metadata_entirely(self, tmp_path):
        scraper = FakeCheapScraper()
        p = make_processor(tmp_path, scraper)
        stubs = make_stubs(1)

        p.run_crawl(stubs, state_key="k", display_name="Test", topic=None)

        assert scraper.metadata_calls == []
        assert scraper.scrape_calls == ["https://example.com/0"]

    def test_scraper_without_cheap_path_filters_after_scraping(self, tmp_path):
        """YouTube-shaped scraper: no get_metadata, so filtering still happens
        on the already-scraped content — unchanged pre-Phase-4 behaviour."""
        scraper = FakeExpensiveOnlyScraper()
        topic_filter = MagicMock()
        topic_filter.is_relevant.return_value = False
        p = make_processor(tmp_path, scraper, topic_filter=topic_filter)
        stubs = make_stubs(1)

        result = p.run_crawl(stubs, state_key="k", display_name="Test", topic="food")

        assert scraper.scrape_calls == ["https://example.com/0"]  # had to scrape to get an excerpt
        p._save_scraped.assert_not_called()
        assert result.success


class TestDedupeAndSkip:
    def test_existing_vault_note_is_skipped_without_scraping(self, tmp_path):
        scraper = FakeCheapScraper()
        p = make_processor(tmp_path, scraper, linker_hit="Already Here")
        stubs = make_stubs(1)

        p.run_crawl(stubs, state_key="k", display_name="Test")

        assert scraper.scrape_calls == []
        assert scraper.metadata_calls == []
        p._save_scraped.assert_not_called()


class TestResume:
    def test_already_processed_stub_is_skipped_on_a_fresh_processor(self, tmp_path):
        """Simulates resuming a crawl in a new process: pre-populate the
        state file (as if a prior invocation got partway through), then run
        again with the same key and confirm the already-processed item is
        skipped without re-scraping."""
        store = CrawlStateStore(str(tmp_path / "state.json"))
        store.save("resume-key", CrawlRunState(processed_ids=["v0"]))

        scraper = FakeExpensiveOnlyScraper()
        p = make_processor(tmp_path, scraper)
        stubs = make_stubs(2)

        p.run_crawl(stubs, state_key="resume-key", display_name="Test", resume=True)

        assert scraper.scrape_calls == ["https://example.com/1"]  # v0 skipped, v1 processed

    def test_resume_false_ignores_prior_state(self, tmp_path):
        store = CrawlStateStore(str(tmp_path / "state.json"))
        store.save("resume-key", CrawlRunState(processed_ids=["v0"]))

        scraper = FakeExpensiveOnlyScraper()
        p = make_processor(tmp_path, scraper)
        stubs = make_stubs(2)

        p.run_crawl(stubs, state_key="resume-key", display_name="Test", resume=False)

        assert sorted(scraper.scrape_calls) == ["https://example.com/0", "https://example.com/1"]

    def test_scrape_failure_is_not_marked_processed_and_run_continues(self, tmp_path):
        class FlakyScraper(FakeExpensiveOnlyScraper):
            def scrape(self, url):
                if url.endswith("/0"):
                    self.scrape_calls.append(url)
                    raise RuntimeError("network blip")
                return super().scrape(url)

        scraper = FlakyScraper()
        p = make_processor(tmp_path, scraper)
        stubs = make_stubs(2)

        result = p.run_crawl(stubs, state_key="k", display_name="Test")

        assert scraper.scrape_calls == ["https://example.com/0", "https://example.com/1"]
        assert "1 failed" in result.message


class TestMaxItemsCap:
    def test_cap_is_logged_and_surfaced_in_result_not_silent(self, tmp_path, caplog):
        scraper = FakeExpensiveOnlyScraper()
        p = make_processor(tmp_path, scraper)
        stubs = make_stubs(3)

        with caplog.at_level("WARNING"):
            result = p.run_crawl(stubs, state_key="k", display_name="Test", max_items=1)

        assert len(scraper.scrape_calls) == 1  # only the cap survives this run
        assert any("Capping" in r.message for r in caplog.records)
        assert "Capped" in result.message
        assert "2" in result.message  # the count of items not attempted

    def test_no_cap_message_when_under_the_limit(self, tmp_path):
        scraper = FakeExpensiveOnlyScraper()
        p = make_processor(tmp_path, scraper)
        stubs = make_stubs(2)

        result = p.run_crawl(stubs, state_key="k", display_name="Test", max_items=5)

        assert "Capped" not in result.message


class TestEmptyStubs:
    def test_no_stubs_is_a_clean_failure_not_a_crash(self, tmp_path):
        scraper = FakeExpensiveOnlyScraper()
        p = make_processor(tmp_path, scraper)

        result = p.run_crawl([], state_key="k", display_name="Test")

        assert not result.success
