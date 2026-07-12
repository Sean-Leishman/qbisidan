"""Tests for channel/playlist enumeration — routing, shorts filter, date filter, stale cookies."""
from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from src.scrapers.channel import ChannelEnumerator, StaleCookiesError, VideoStub


def _fake_ydl(info: dict):
    """Return a context-manager mock whose extract_info returns `info`."""
    ydl = MagicMock()
    ydl.__enter__.return_value = ydl
    ydl.extract_info.return_value = info
    return ydl


# ── URL routing ─────────────────────────────────────────────────────────────

class TestUrlRouting:
    def test_channel_url_gets_videos_suffix(self):
        enum = ChannelEnumerator()
        info = {"title": "Some Channel - Videos", "entries": []}
        ydl = _fake_ydl(info)
        with patch("yt_dlp.YoutubeDL", return_value=ydl):
            enum.list_videos("https://www.youtube.com/@somechannel")
        called_url = ydl.extract_info.call_args[0][0]
        assert called_url == "https://www.youtube.com/@somechannel/videos"

    def test_playlist_url_not_suffixed(self):
        enum = ChannelEnumerator()
        info = {"title": "My Playlist", "entries": [{"id": "abc123", "title": "Vid 1", "url": "https://www.youtube.com/watch?v=abc123"}]}
        ydl = _fake_ydl(info)
        with patch("yt_dlp.YoutubeDL", return_value=ydl):
            stubs = enum.list_videos("https://www.youtube.com/playlist?list=PLxxx")
        called_url = ydl.extract_info.call_args[0][0]
        assert called_url == "https://www.youtube.com/playlist?list=PLxxx"
        assert len(stubs) == 1

    def test_watch_url_with_list_param_not_suffixed(self):
        enum = ChannelEnumerator()
        info = {"title": "Mixed", "entries": []}
        ydl = _fake_ydl(info)
        url = "https://www.youtube.com/watch?v=abc&list=PLxxx"
        with patch("yt_dlp.YoutubeDL", return_value=ydl):
            with pytest.raises(StaleCookiesError):
                enum.list_videos(url)
        called_url = ydl.extract_info.call_args[0][0]
        assert called_url == url

    def test_liked_and_watch_later_treated_as_playlists(self):
        enum = ChannelEnumerator()
        info = {"title": "Liked videos", "entries": [{"id": "z1", "title": "Liked vid", "url": "https://www.youtube.com/watch?v=z1"}]}
        ydl = _fake_ydl(info)
        with patch("yt_dlp.YoutubeDL", return_value=ydl):
            stubs = enum.list_videos("https://www.youtube.com/playlist?list=LL")
        called_url = ydl.extract_info.call_args[0][0]
        assert called_url == "https://www.youtube.com/playlist?list=LL"
        assert len(stubs) == 1

    def test_exposes_last_title(self):
        enum = ChannelEnumerator()
        info = {"title": "Kitchen Renovation", "entries": []}
        ydl = _fake_ydl(info)
        with patch("yt_dlp.YoutubeDL", return_value=ydl):
            with pytest.raises(StaleCookiesError):
                enum.list_videos("https://www.youtube.com/playlist?list=PLxxx")
        assert enum.last_title == "Kitchen Renovation"


# ── Shorts filtering ─────────────────────────────────────────────────────────

class TestShortsFiltering:
    def test_shorts_dropped_from_channel(self):
        enum = ChannelEnumerator()
        info = {
            "title": "Channel",
            "entries": [
                {"id": "v1", "title": "Regular video", "url": "https://www.youtube.com/watch?v=v1"},
                {"id": "s1", "title": "A short", "url": "https://www.youtube.com/shorts/s1"},
            ],
        }
        ydl = _fake_ydl(info)
        with patch("yt_dlp.YoutubeDL", return_value=ydl):
            stubs = enum.list_videos("https://www.youtube.com/@somechannel")
        assert [s.video_id for s in stubs] == ["v1"]

    def test_shorts_kept_when_explicitly_in_playlist(self):
        enum = ChannelEnumerator()
        info = {
            "title": "Playlist",
            "entries": [
                {"id": "s1", "title": "A short", "url": "https://www.youtube.com/shorts/s1"},
            ],
        }
        ydl = _fake_ydl(info)
        with patch("yt_dlp.YoutubeDL", return_value=ydl):
            stubs = enum.list_videos("https://www.youtube.com/playlist?list=PLxxx")
        assert [s.video_id for s in stubs] == ["s1"]


# ── Date filtering ────────────────────────────────────────────────────────────

class TestDateFiltering:
    def test_fails_open_when_no_date(self):
        enum = ChannelEnumerator()
        info = {
            "title": "Channel",
            "entries": [{"id": "v1", "title": "No date info", "url": "https://www.youtube.com/watch?v=v1"}],
        }
        ydl = _fake_ydl(info)
        with patch("yt_dlp.YoutubeDL", return_value=ydl):
            stubs = enum.list_videos(
                "https://www.youtube.com/@somechannel",
                date_from=date(2020, 1, 1),
                date_to=date(2020, 12, 31),
            )
        assert len(stubs) == 1
        assert stubs[0].upload_date is None

    def test_filters_when_date_present(self):
        enum = ChannelEnumerator()
        info = {
            "title": "Channel",
            "entries": [
                {"id": "old", "title": "Old video", "url": "https://www.youtube.com/watch?v=old", "upload_date": "20190101"},
                {"id": "new", "title": "New video", "url": "https://www.youtube.com/watch?v=new", "upload_date": "20240601"},
            ],
        }
        ydl = _fake_ydl(info)
        with patch("yt_dlp.YoutubeDL", return_value=ydl):
            stubs = enum.list_videos(
                "https://www.youtube.com/@somechannel",
                date_from=date(2023, 1, 1),
            )
        assert [s.video_id for s in stubs] == ["new"]
        assert stubs[0].upload_date == date(2024, 6, 1)


# ── Stale cookies / empty playlist signal ────────────────────────────────────

class TestStaleCookies:
    def test_empty_playlist_raises(self):
        enum = ChannelEnumerator()
        info = {"title": "Playlist", "entries": []}
        ydl = _fake_ydl(info)
        with patch("yt_dlp.YoutubeDL", return_value=ydl):
            with pytest.raises(StaleCookiesError):
                enum.list_videos("https://www.youtube.com/playlist?list=PLxxx")

    def test_empty_channel_does_not_raise(self):
        enum = ChannelEnumerator()
        info = {"title": "Channel", "entries": []}
        ydl = _fake_ydl(info)
        with patch("yt_dlp.YoutubeDL", return_value=ydl):
            stubs = enum.list_videos("https://www.youtube.com/@somechannel")
        assert stubs == []


# ── VideoStub shape ───────────────────────────────────────────────────────────

def test_video_stub_fields():
    stub = VideoStub(video_id="abc", url="https://x", title="T", upload_date=None)
    assert stub.video_id == "abc"
    assert stub.upload_date is None
