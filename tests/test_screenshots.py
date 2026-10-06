"""Screenshots sent to the bot: never dropped, never lost, always answered."""
import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from src.agents.place_classifier import Placement
from src.places import LOCAL, Place, load_inbox
from src.processor import Processor, _screenshot_reply
from src.telegram_queue import TelegramQueue


@pytest.fixture
def queue(tmp_path):
    return TelegramQueue(bot_token="dummy", allowed_chat_ids=[123], state_file=str(tmp_path / "s.json"))


def update(**message):
    return {"update_id": 7, "message": {"message_id": 1, "chat": {"id": 123}, "date": 0, **message}}


class TestTelegramAcceptsImages:
    def test_a_photo_without_a_caption_is_no_longer_dropped(self, queue):
        """Before: no `text`, so it returned None and the offset moved past it -- gone for good."""
        msg = queue._parse_message_update(update(photo=[
            {"file_id": "small", "file_size": 1_000, "width": 90, "height": 160},
            {"file_id": "large", "file_size": 90_000, "width": 1170, "height": 2532}]))
        assert msg is not None and msg.image_file_id == "large" and msg.text == ""

    def test_the_caption_comes_along(self, queue):
        msg = queue._parse_message_update(update(photo=[{"file_id": "p", "file_size": 1}], caption="for date night"))
        assert msg.text == "for date night" and msg.image_file_id == "p"

    def test_an_image_sent_as_a_file_counts(self, queue):
        msg = queue._parse_message_update(update(document={"file_id": "d", "mime_type": "image/png"}))
        assert msg.image_file_id == "d" and msg.image_mime == "image/png"

    def test_a_non_image_file_with_no_text_is_still_dropped(self, queue):
        assert queue._parse_message_update(update(document={"file_id": "d", "mime_type": "application/pdf"})) is None

    def test_plain_text_is_unchanged(self, queue):
        msg = queue._parse_message_update(update(text="/todo buy milk"))
        assert msg.text == "/todo buy milk" and msg.image_file_id is None

    def test_wrong_chat_is_still_refused(self, queue):
        assert queue._parse_message_update({"update_id": 1, "message": {
            "message_id": 1, "chat": {"id": 999}, "photo": [{"file_id": "x"}]}}) is None


class TestDownload:
    def test_get_file_then_the_file_url(self, queue):
        calls = []

        def fake_get(url, params=None, timeout=None):
            calls.append(url)
            r = MagicMock()
            r.raise_for_status = lambda: None
            if url.endswith("/getFile"):
                r.json = lambda: {"result": {"file_path": "photos/file_1.jpg", "file_size": 1234}}
            else:
                r.content = b"JPEG"
            return r

        with patch("src.telegram_queue.requests.get", side_effect=fake_get):
            assert queue.download_file("abc") == b"JPEG"
        assert calls[1] == "https://api.telegram.org/file/botdummy/photos/file_1.jpg"

    def test_an_oversized_file_is_refused_before_downloading(self, queue):
        r = MagicMock()
        r.raise_for_status = lambda: None
        r.json = lambda: {"result": {"file_path": "x.jpg", "file_size": 30_000_000}}
        with patch("src.telegram_queue.requests.get", return_value=r) as get:
            with pytest.raises(RuntimeError, match="over the 20 MB limit"):
                queue.download_file("abc")
        assert get.call_count == 1


def processor(reader_text="BAO Soho\n53 Lexington Street\nShows: a restaurant page", placement=None, reader_error=None):
    p = Processor.__new__(Processor)
    p.config = SimpleNamespace(ai=SimpleNamespace(gemini_api_key="k", gemini_model="m", provider="gemini",
                                                  api_key="k", model="m"))
    p.telegram = MagicMock()
    p.telegram.download_file.return_value = b"JPEG"

    class Reader:
        def __init__(self, *a): pass

        def read(self, image, mime):
            if reader_error:
                raise reader_error
            return reader_text

    class Classifier:
        def __init__(self, **k): pass

        def classify(self, texts):
            return [placement or Placement("food", "BAO Soho", "Soho", "open till 10pm")]

    return p, Reader, Classifier


def message(text=""):
    return SimpleNamespace(image_file_id="f", image_mime="image/jpeg", text=text, chat_id=123)


class TestScreenshotPath:
    def run(self, tmp_path, monkeypatch, **kw):
        monkeypatch.chdir(tmp_path)
        p, Reader, Classifier = processor(**kw)
        with patch("src.scrapers.screenshot.ScreenshotReader", Reader), \
             patch("src.agents.place_classifier.PlaceClassifierAgent", Classifier):
            return p._process_screenshot(message(kw.get("caption", "")))

    def test_a_place_is_kept_and_named_in_the_reply(self, tmp_path, monkeypatch):
        result = self.run(tmp_path, monkeypatch)
        assert result.success and "BAO Soho" in result.message and "open till 10pm" in result.message
        inbox = load_inbox()
        assert len(inbox) == 1 and inbox[0]["source"] == "Screenshot" and "Lexington" in inbox[0]["text"]

    def test_a_classifier_failure_still_keeps_the_capture(self, tmp_path, monkeypatch):
        result = self.run(tmp_path, monkeypatch, placement=Placement("unsorted"))
        assert result.success and "retry" in result.message
        assert len(load_inbox()) == 1, "kept before classifying, so a failure loses nothing"

    def test_an_unreadable_image_says_so_and_keeps_nothing(self, tmp_path, monkeypatch):
        result = self.run(tmp_path, monkeypatch, reader_error=RuntimeError("402"))
        assert not result.success and "couldn't read" in result.error
        assert load_inbox() == []

    def test_the_image_itself_is_never_stored(self, tmp_path, monkeypatch):
        self.run(tmp_path, monkeypatch)
        assert "JPEG" not in json.dumps(load_inbox())


class TestReplies:
    def test_every_outcome_says_what_happened(self):
        assert "*BAO*" in _screenshot_reply(Place("food", "BAO", why="bao"), LOCAL)
        assert "couldn't read *which* place" in _screenshot_reply(Place("pub"), LOCAL)
        assert "travel: Lisbon" in _screenshot_reply(Place("travel", area="Lisbon"), LOCAL)
        assert "doesn't look like a place" in _screenshot_reply(Place("other"), LOCAL)
        assert "retry" in _screenshot_reply(Place("unsorted"), LOCAL)

    def test_markdown_in_a_name_cannot_break_the_reply(self):
        assert "Bob\\_s" in _screenshot_reply(Place("pub", "Bob_s"), LOCAL)
