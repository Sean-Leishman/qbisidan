"""Tests for the Instagram reel scraper — cheap metadata path, degradation to
caption-only (missing key / over-duration / download or Gemini failure), and
temp-file + uploaded-Gemini-file cleanup. Mocks yt-dlp AND the Gemini client;
no network, no real API calls."""
import tempfile
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from src.scrapers.instagram import InstagramScraper


def _fake_ydl(info: dict):
    """Return a context-manager mock whose extract_info returns `info`."""
    ydl = MagicMock()
    ydl.__enter__.return_value = ydl
    ydl.__exit__.return_value = False  # don't swallow exceptions raised inside `with`
    ydl.extract_info.return_value = info
    return ydl


def _metadata_info(**overrides) -> dict:
    info = {
        "title": "A reel",
        "description": "Caption text #food",
        "uploader": "some_chef",
        "upload_date": "20250601",
        "thumbnail": "https://example.com/thumb.jpg",
        "duration": 45,
    }
    info.update(overrides)
    return info


def _fake_gemini_file(name="files/abc123", state_name="ACTIVE"):
    return SimpleNamespace(name=name, uri="gs://files/abc123", state=SimpleNamespace(name=state_name))


# ── can_handle ────────────────────────────────────────────────────────────

class TestCanHandle:
    def test_reel_url(self):
        scraper = InstagramScraper()
        assert scraper.can_handle("https://www.instagram.com/reel/Cabc123XYZ/")

    def test_reels_url(self):
        scraper = InstagramScraper()
        assert scraper.can_handle("https://www.instagram.com/reels/Cabc123XYZ/")

    def test_p_url(self):
        scraper = InstagramScraper()
        assert scraper.can_handle("https://www.instagram.com/p/Cabc123XYZ/")

    def test_rejects_non_instagram_url(self):
        scraper = InstagramScraper()
        assert not scraper.can_handle("https://www.youtube.com/watch?v=abc123")
        assert not scraper.can_handle("https://example.com/article")

    def test_rejects_instagram_profile_url(self):
        scraper = InstagramScraper()
        assert not scraper.can_handle("https://www.instagram.com/some_chef/")


# ── cheap metadata path ──────────────────────────────────────────────────

class TestGetMetadata:
    def test_metadata_only_does_not_download_video(self):
        scraper = InstagramScraper()
        ydl = _fake_ydl(_metadata_info())
        with patch("yt_dlp.YoutubeDL", return_value=ydl) as ydl_cls:
            metadata = scraper.get_metadata("https://www.instagram.com/reel/abc/")

        # extract_info called with download=False, and .download() (the
        # actual video-fetching call) never touched.
        ydl.extract_info.assert_called_once()
        assert ydl.extract_info.call_args.kwargs.get("download") is False
        ydl.download.assert_not_called()

        # skip_download must be set in the yt-dlp options used to build it.
        opts = ydl_cls.call_args[0][0]
        assert opts.get("skip_download") is True

        assert metadata["caption"] == "Caption text #food"
        assert metadata["author"] == "some_chef"
        assert metadata["duration"] == 45

    def test_metadata_uses_cookies_file(self):
        scraper = InstagramScraper(cookies_file="/tmp/cookies.txt")
        ydl = _fake_ydl(_metadata_info())
        with patch("yt_dlp.YoutubeDL", return_value=ydl) as ydl_cls:
            scraper.get_metadata("https://www.instagram.com/reel/abc/")
        opts = ydl_cls.call_args[0][0]
        assert opts.get("cookiefile") == "/tmp/cookies.txt"


# ── degradation paths ─────────────────────────────────────────────────────

class TestDegradation:
    def test_missing_gemini_key_degrades_to_caption_only(self):
        scraper = InstagramScraper(gemini_api_key="")  # no key configured
        ydl = _fake_ydl(_metadata_info())
        with patch("yt_dlp.YoutubeDL", return_value=ydl):
            with patch("google.genai.Client") as client_cls:
                result = scraper.scrape("https://www.instagram.com/reel/abc/")

        assert result.content_type == "instagram"
        assert result.content == "Caption text #food"
        assert result.title == "A reel"
        # Never even tried to talk to Gemini.
        client_cls.assert_not_called()

    def test_over_duration_degrades_to_caption_only(self):
        scraper = InstagramScraper(gemini_api_key="fake-key", max_duration_seconds=30)
        ydl = _fake_ydl(_metadata_info(duration=600))
        with patch("yt_dlp.YoutubeDL", return_value=ydl):
            with patch("google.genai.Client") as client_cls:
                result = scraper.scrape("https://www.instagram.com/reel/abc/")

        assert result.content == "Caption text #food"
        client_cls.assert_not_called()

    def test_gemini_call_failure_degrades_to_caption_only(self):
        scraper = InstagramScraper(gemini_api_key="fake-key")
        ydl = _fake_ydl(_metadata_info())

        client = MagicMock()
        client.files.upload.return_value = _fake_gemini_file()
        client.models.generate_content.side_effect = RuntimeError("Gemini is down")

        with patch("yt_dlp.YoutubeDL", return_value=ydl):
            with patch("google.genai.Client", return_value=client):
                result = scraper.scrape("https://www.instagram.com/reel/abc/")

        assert result.content == "Caption text #food"
        # Cleanup still ran despite the failure.
        client.files.delete.assert_called_once_with(name="files/abc123")

    def test_download_failure_degrades_to_caption_only(self, tmp_path, monkeypatch):
        monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
        scraper = InstagramScraper(gemini_api_key="fake-key")
        ydl = _fake_ydl(_metadata_info())

        with patch("yt_dlp.YoutubeDL", return_value=ydl):
            ydl.download.side_effect = RuntimeError("network blip")
            with patch("google.genai.Client") as client_cls:
                result = scraper.scrape("https://www.instagram.com/reel/abc/")

        assert result.content == "Caption text #food"
        # Never got as far as uploading anything.
        client_cls.return_value.files.upload.assert_not_called()
        # A failed download must not leave its (empty) temp file behind — a
        # backfill of thousands of reels would otherwise slowly fill /tmp.
        assert list(tmp_path.iterdir()) == []


# ── happy path: transcript + visual, cleanup ─────────────────────────────

class TestSuccessfulTranscription:
    def test_full_scrape_returns_transcript_and_visual(self):
        scraper = InstagramScraper(gemini_api_key="fake-key")
        ydl = _fake_ydl(_metadata_info())

        client = MagicMock()
        client.files.upload.return_value = _fake_gemini_file()
        client.models.generate_content.return_value = SimpleNamespace(
            text="TRANSCRIPT:\nCome check out this dish.\n\nVISUAL:\nA plated pasta dish on a wooden table."
        )

        with patch("yt_dlp.YoutubeDL", return_value=ydl):
            with patch("google.genai.Client", return_value=client):
                result = scraper.scrape("https://www.instagram.com/reel/abc/")

        assert "Come check out this dish." in result.content
        assert "A plated pasta dish on a wooden table." in result.content
        assert result.content_type == "instagram"
        assert result.description == "Caption text #food"

    def test_temp_file_and_uploaded_file_deleted_on_success(self):
        scraper = InstagramScraper(gemini_api_key="fake-key")
        ydl = _fake_ydl(_metadata_info())

        uploaded_file = _fake_gemini_file()
        client = MagicMock()
        client.files.upload.return_value = uploaded_file
        client.models.generate_content.return_value = SimpleNamespace(
            text="TRANSCRIPT:\nHi\n\nVISUAL:\nA room"
        )

        captured_path = {}
        real_download = InstagramScraper._download_video

        def spy_download(self, url):
            path = real_download(self, url)
            captured_path["path"] = path
            return path

        with patch("yt_dlp.YoutubeDL", return_value=ydl):
            with patch("google.genai.Client", return_value=client):
                with patch.object(InstagramScraper, "_download_video", spy_download):
                    scraper.scrape("https://www.instagram.com/reel/abc/")

        import os
        assert "path" in captured_path
        assert not os.path.exists(captured_path["path"])
        client.files.delete.assert_called_once_with(name=uploaded_file.name)

    def test_temp_file_deleted_when_generate_content_raises(self):
        scraper = InstagramScraper(gemini_api_key="fake-key")
        ydl = _fake_ydl(_metadata_info())

        client = MagicMock()
        client.files.upload.return_value = _fake_gemini_file()
        client.models.generate_content.side_effect = RuntimeError("boom")

        captured_path = {}
        real_download = InstagramScraper._download_video

        def spy_download(self, url):
            path = real_download(self, url)
            captured_path["path"] = path
            return path

        with patch("yt_dlp.YoutubeDL", return_value=ydl):
            with patch("google.genai.Client", return_value=client):
                with patch.object(InstagramScraper, "_download_video", spy_download):
                    scraper.scrape("https://www.instagram.com/reel/abc/")

        import os
        assert "path" in captured_path
        assert not os.path.exists(captured_path["path"])


# ── parsing helper ─────────────────────────────────────────────────────────

class TestParseTranscriptVisual:
    def test_parses_well_formed_response(self):
        scraper = InstagramScraper()
        transcript, visual = scraper._parse_transcript_visual(
            "TRANSCRIPT:\nHello there\n\nVISUAL:\nA kitchen counter"
        )
        assert transcript == "Hello there"
        assert visual == "A kitchen counter"

    def test_falls_back_to_raw_text_when_unparseable(self):
        scraper = InstagramScraper()
        transcript, visual = scraper._parse_transcript_visual("just some raw text")
        assert transcript == "just some raw text"
        assert visual == ""

    def test_empty_text(self):
        scraper = InstagramScraper()
        assert scraper._parse_transcript_visual("") == ("", "")
