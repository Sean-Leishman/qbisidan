"""Tests for Processor.run_backfill — routes an Instagram export kind or a
playlist URL into the shared run_crawl engine. run_crawl itself is mocked
here (it's covered by test_run_crawl.py) so these tests isolate routing:
export lookup, interests-as-default-topic, error paths, and that max_items /
state_key / display_name are threaded through correctly. No network."""
from types import SimpleNamespace
from unittest.mock import MagicMock

from src.processor import Processor, ProcessingResult
from src.scrapers.channel import StaleCookiesError, VideoStub
from src.scrapers.instagram_export import ExportNotFoundError


def make_processor(export_dir="/exports", max_items=50, include=None, exclude=None):
    p = Processor.__new__(Processor)
    p.config = SimpleNamespace(
        instagram=SimpleNamespace(export_dir=export_dir),
        backfill=SimpleNamespace(max_items=max_items),
        interests=SimpleNamespace(
            render=lambda: (
                None
                if not (include or exclude)
                else f"Include: {include or []}. Exclude: {exclude or []}."
            )
        ),
    )
    p.channel_enumerator = MagicMock()
    p.run_crawl = MagicMock(return_value=ProcessingResult(url=None, success=True, message="done"))
    return p


class TestInstagramSource:
    def test_saved_loads_export_and_calls_run_crawl(self, monkeypatch):
        p = make_processor()
        stubs = [VideoStub(video_id="a", url="https://x/a", title="t", upload_date=None)]
        loader = MagicMock(return_value=stubs)
        monkeypatch.setattr("src.processor.load_instagram_export", loader)

        result = p.run_backfill(source="instagram:saved")

        loader.assert_called_once_with("/exports", "saved", None, None)
        p.run_crawl.assert_called_once()
        assert p.run_crawl.call_args.kwargs["stubs"] == stubs
        assert result.success

    def test_likes_kind_routes_correctly(self, monkeypatch):
        p = make_processor()
        loader = MagicMock(return_value=[])
        monkeypatch.setattr("src.processor.load_instagram_export", loader)

        p.run_backfill(source="instagram:likes")

        loader.assert_called_once_with("/exports", "likes", None, None)

    def test_unknown_kind_is_an_error(self):
        p = make_processor()
        result = p.run_backfill(source="instagram:bogus")
        assert not result.success
        p.run_crawl.assert_not_called()

    def test_missing_export_dir_is_an_error(self):
        p = make_processor(export_dir="")
        result = p.run_backfill(source="instagram:saved")
        assert not result.success
        assert "export_dir" in result.error
        p.run_crawl.assert_not_called()

    def test_export_not_found_surfaces_as_error_not_exception(self, monkeypatch):
        p = make_processor()
        monkeypatch.setattr(
            "src.processor.load_instagram_export",
            MagicMock(side_effect=ExportNotFoundError("no export here")),
        )
        result = p.run_backfill(source="instagram:saved")
        assert not result.success
        assert "no export here" in result.error

    def test_no_stubs_is_an_error_before_run_crawl(self, monkeypatch):
        p = make_processor()
        monkeypatch.setattr("src.processor.load_instagram_export", MagicMock(return_value=[]))
        result = p.run_backfill(source="instagram:saved")
        assert not result.success
        p.run_crawl.assert_not_called()

    def test_max_items_defaults_to_config(self, monkeypatch):
        p = make_processor(max_items=7)
        monkeypatch.setattr(
            "src.processor.load_instagram_export",
            MagicMock(return_value=[VideoStub(video_id="a", url="u", title="t", upload_date=None)]),
        )
        p.run_backfill(source="instagram:saved")
        assert p.run_crawl.call_args.kwargs["max_items"] == 7

    def test_max_items_override_wins(self, monkeypatch):
        p = make_processor(max_items=7)
        monkeypatch.setattr(
            "src.processor.load_instagram_export",
            MagicMock(return_value=[VideoStub(video_id="a", url="u", title="t", upload_date=None)]),
        )
        p.run_backfill(source="instagram:saved", max_items=3)
        assert p.run_crawl.call_args.kwargs["max_items"] == 3


class TestPlaylistSource:
    def test_playlist_url_uses_channel_enumerator(self):
        p = make_processor()
        stubs = [VideoStub(video_id="a", url="https://x/a", title="t", upload_date=None)]
        p.channel_enumerator.list_videos.return_value = stubs
        p.channel_enumerator.last_title = "Liked Videos"

        result = p.run_backfill(source="https://youtube.com/playlist?list=LL")

        p.channel_enumerator.list_videos.assert_called_once()
        assert p.run_crawl.call_args.kwargs["display_name"] == "Liked Videos"
        assert result.success

    def test_stale_cookies_surfaces_as_error(self):
        p = make_processor()
        p.channel_enumerator.list_videos.side_effect = StaleCookiesError("cookies expired")

        result = p.run_backfill(source="https://youtube.com/playlist?list=LL")

        assert not result.success
        assert "cookies expired" in result.error
        p.run_crawl.assert_not_called()


class TestInterestsDefaultTopic:
    def test_topic_defaults_to_rendered_interests(self, monkeypatch):
        p = make_processor(include=["food"], exclude=["memes"])
        monkeypatch.setattr(
            "src.processor.load_instagram_export",
            MagicMock(return_value=[VideoStub(video_id="a", url="u", title="t", upload_date=None)]),
        )
        p.run_backfill(source="instagram:saved")
        topic = p.run_crawl.call_args.kwargs["topic"]
        assert "food" in topic
        assert "memes" in topic

    def test_explicit_topic_overrides_interests(self, monkeypatch):
        p = make_processor(include=["food"])
        monkeypatch.setattr(
            "src.processor.load_instagram_export",
            MagicMock(return_value=[VideoStub(video_id="a", url="u", title="t", upload_date=None)]),
        )
        p.run_backfill(source="instagram:saved", topic="rust programming")
        assert p.run_crawl.call_args.kwargs["topic"] == "rust programming"

    def test_no_interests_configured_topic_is_none(self, monkeypatch):
        p = make_processor()
        monkeypatch.setattr(
            "src.processor.load_instagram_export",
            MagicMock(return_value=[VideoStub(video_id="a", url="u", title="t", upload_date=None)]),
        )
        p.run_backfill(source="instagram:saved")
        assert p.run_crawl.call_args.kwargs["topic"] is None


class TestResultUrlStamping:
    def test_url_stamped_with_source_when_engine_leaves_it_none(self, monkeypatch):
        p = make_processor()
        monkeypatch.setattr(
            "src.processor.load_instagram_export",
            MagicMock(return_value=[VideoStub(video_id="a", url="u", title="t", upload_date=None)]),
        )
        result = p.run_backfill(source="instagram:saved")
        assert result.url == "instagram:saved"
