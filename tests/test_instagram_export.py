"""Tests for the Instagram DYI export reader — tolerating both known export
key layouts (saved posts' string_map_data, likes' string_list_data), skipping
malformed records instead of crashing, date-range filtering, and locating the
export file recursively under export_dir. No network."""
import json

import pytest

from src.scrapers.instagram_export import (
    ExportNotFoundError,
    find_export_file,
    load_export_stubs,
    load_instagram_export,
)


def _write(tmp_path, name: str, data: dict):
    path = tmp_path / name
    path.write_text(json.dumps(data))
    return path


# ── saved_posts.json shape (string_map_data) ────────────────────────────────

def _saved_posts_payload(**overrides) -> dict:
    payload = {
        "saved_saved_media": [
            {
                "title": "some_chef",
                "string_map_data": {
                    "Saved on": {
                        "href": "https://www.instagram.com/reel/Cabc123/",
                        "timestamp": 1700000000,
                    }
                },
            }
        ]
    }
    payload.update(overrides)
    return payload


class TestSavedPostsLayout:
    def test_parses_href_and_timestamp(self, tmp_path):
        from datetime import datetime

        path = _write(tmp_path, "saved_posts.json", _saved_posts_payload())
        stubs = load_export_stubs(path)
        assert len(stubs) == 1
        assert stubs[0].video_id == "Cabc123"
        assert stubs[0].url == "https://www.instagram.com/reel/Cabc123/"
        assert stubs[0].upload_date == datetime.fromtimestamp(1700000000).date()

    def test_title_mentions_poster(self, tmp_path):
        path = _write(tmp_path, "saved_posts.json", _saved_posts_payload())
        stubs = load_export_stubs(path)
        assert "some_chef" in stubs[0].title


# ── liked_posts.json shape (string_list_data) ───────────────────────────────

def _liked_posts_payload(**overrides) -> dict:
    payload = {
        "likes_media_likes": [
            {
                "title": "another_user",
                "string_list_data": [
                    {"href": "https://www.instagram.com/p/Cxyz456/", "timestamp": 1699000000}
                ],
            }
        ]
    }
    payload.update(overrides)
    return payload


class TestLikedPostsLayout:
    def test_parses_href_and_timestamp(self, tmp_path):
        path = _write(tmp_path, "liked_posts.json", _liked_posts_payload())
        stubs = load_export_stubs(path)
        assert len(stubs) == 1
        assert stubs[0].video_id == "Cxyz456"
        assert stubs[0].url == "https://www.instagram.com/p/Cxyz456/"


# ── Malformed record tolerance ───────────────────────────────────────────────

class TestMalformedRecords:
    def test_one_bad_record_does_not_sink_the_rest(self, tmp_path):
        payload = _saved_posts_payload()
        payload["saved_saved_media"].append(
            {"title": "broken", "string_map_data": {"Saved on": {"no_href_here": True}}}
        )
        path = _write(tmp_path, "saved_posts.json", payload)
        stubs = load_export_stubs(path)
        assert len(stubs) == 1  # only the good record survives

    def test_record_with_unrecognisable_url_is_skipped(self, tmp_path):
        payload = {
            "saved_saved_media": [
                {
                    "title": "x",
                    "string_map_data": {
                        "Saved on": {"href": "https://example.com/not-instagram", "timestamp": 123}
                    },
                }
            ]
        }
        path = _write(tmp_path, "saved_posts.json", payload)
        stubs = load_export_stubs(path)
        assert stubs == []

    def test_missing_timestamp_still_parses_with_no_date(self, tmp_path):
        payload = {
            "saved_saved_media": [
                {
                    "title": "x",
                    "string_map_data": {"Saved on": {"href": "https://www.instagram.com/reel/Cabc/"}},
                }
            ]
        }
        path = _write(tmp_path, "saved_posts.json", payload)
        stubs = load_export_stubs(path)
        assert len(stubs) == 1
        assert stubs[0].upload_date is None

    def test_corrupt_json_file_returns_empty_not_raise(self, tmp_path):
        path = tmp_path / "saved_posts.json"
        path.write_text("{not valid json")
        assert load_export_stubs(path) == []

    def test_missing_file_returns_empty_not_raise(self, tmp_path):
        assert load_export_stubs(tmp_path / "nope.json") == []


# ── Date-range filtering ─────────────────────────────────────────────────────

class TestDateRangeFiltering:
    def test_filters_out_of_range_entries(self, tmp_path):
        from datetime import date

        payload = {
            "saved_saved_media": [
                {"title": "a", "string_map_data": {"k": {"href": "https://www.instagram.com/reel/old1/", "timestamp": 1600000000}}},
                {"title": "b", "string_map_data": {"k": {"href": "https://www.instagram.com/reel/new1/", "timestamp": 1750000000}}},
            ]
        }
        path = _write(tmp_path, "saved_posts.json", payload)
        stubs = load_export_stubs(path, date_from=date(2025, 1, 1))
        assert [s.video_id for s in stubs] == ["new1"]


# ── find_export_file / load_instagram_export ────────────────────────────────

class TestFindExportFile:
    def test_finds_file_nested_under_subfolders(self, tmp_path):
        nested = tmp_path / "your_instagram_activity" / "saved"
        nested.mkdir(parents=True)
        (nested / "saved_posts.json").write_text(json.dumps(_saved_posts_payload()))
        found = find_export_file(tmp_path, "saved")
        assert found is not None
        assert found.name == "saved_posts.json"

    def test_returns_none_when_not_found(self, tmp_path):
        assert find_export_file(tmp_path, "saved") is None

    def test_returns_none_for_nonexistent_dir(self, tmp_path):
        assert find_export_file(tmp_path / "nope", "saved") is None


class TestLoadInstagramExport:
    def test_raises_clear_error_when_missing(self, tmp_path):
        with pytest.raises(ExportNotFoundError):
            load_instagram_export(tmp_path, "saved")

    def test_loads_when_present(self, tmp_path):
        (tmp_path / "saved_posts.json").write_text(json.dumps(_saved_posts_payload()))
        stubs = load_instagram_export(tmp_path, "saved")
        assert len(stubs) == 1
