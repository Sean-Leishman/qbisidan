"""Voice notes sent to the bot: never dropped, transcribed, then handled as if typed."""
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from src.agents.vault_writer import VaultWriter
from src.processor import Processor
from src.telegram_queue import TelegramMessage, TelegramQueue


@pytest.fixture
def queue(tmp_path):
    return TelegramQueue(bot_token="dummy", allowed_chat_ids=[123], state_file=str(tmp_path / "s.json"))


def update(**message):
    return {"update_id": 7, "message": {"message_id": 1, "chat": {"id": 123}, "date": 0, **message}}


class TestTelegramAcceptsAudio:
    def test_a_voice_note_is_no_longer_dropped(self, queue):
        msg = queue._parse_message_update(update(voice={"file_id": "v", "duration": 4, "mime_type": "audio/ogg"}))
        assert msg is not None and msg.audio_file_id == "v" and msg.audio_mime == "audio/ogg" and msg.text == ""

    def test_an_audio_file_keeps_its_mime_type(self, queue):
        msg = queue._parse_message_update(update(audio={"file_id": "a", "mime_type": "audio/mp4"}))
        assert msg.audio_file_id == "a" and msg.audio_mime == "audio/mp4"

    def test_plain_text_has_no_audio(self, queue):
        assert queue._parse_message_update(update(text="hi")).audio_file_id is None


SENT = datetime(2026, 11, 14, 9, 30)


def processor(tmp_path, transcript="", error=None):
    p = Processor.__new__(Processor)
    p.config = SimpleNamespace(ai=SimpleNamespace(gemini_api_key="k", gemini_model="m"),
                               telegram=SimpleNamespace(send_notifications=False))
    p.telegram = MagicMock()
    p.telegram.download_file.return_value = b"OGG"
    p.vault_writer = VaultWriter(vault_path=tmp_path)

    class Transcriber:
        def __init__(self, *a): pass

        def transcribe(self, audio, mime):
            assert audio == b"OGG" and mime == "audio/ogg"
            if error:
                raise error
            return transcript

    return p, Transcriber


def run(tmp_path, **kw):
    p, Transcriber = processor(tmp_path, **kw)
    msg = TelegramMessage(update_id=1, message_id=1, chat_id=123, text="", date=int(SENT.timestamp()),
                          audio_file_id="v", audio_mime="audio/ogg")
    with patch("src.scrapers.voice.VoiceTranscriber", Transcriber):
        return p.process_message(msg)


def daily(tmp_path):
    path = tmp_path / "06 Daily Notes" / "2026-11-14.md"
    return path.read_text() if path.exists() else ""


class TestVoiceRouting:
    def test_plain_speech_is_a_timed_line_in_the_day_it_was_sent(self, tmp_path):
        result = run(tmp_path, transcript="Met Sam for coffee,\nhe's moving to Leeds.")
        assert result.success
        assert "- 09:30 🎙 Met Sam for coffee, he's moving to Leeds." in daily(tmp_path)
        assert result.message == "🎙 Logged: Met Sam for coffee, he's moving to Leeds."

    @pytest.mark.parametrize("spoken", ["Todo buy milk", "To do: buy milk", "to-do, buy milk", "TODO. buy milk"])
    def test_a_spoken_todo_goes_to_the_inbox(self, tmp_path, spoken):
        result = run(tmp_path, transcript=spoken)
        assert result.success and result.message.startswith("🎙 Todo:")
        assert "🎙 buy milk" in (tmp_path / "08 Trackers" / "Inbox.md").read_text()
        assert daily(tmp_path) == ""

    def test_words_that_merely_start_like_todo_are_plain(self, tmp_path):
        run(tmp_path, transcript="Today was long")
        assert "🎙 Today was long" in daily(tmp_path)
        assert not (tmp_path / "08 Trackers" / "Inbox.md").exists()

    def test_a_transcription_failure_writes_nothing_and_is_retryable(self, tmp_path):
        result = run(tmp_path, error=RuntimeError("503"))
        assert not result.success and "couldn't transcribe" in result.error and "503" in result.error
        assert not any(tmp_path.rglob("*.md"))

    def test_silence_is_a_failure_not_an_empty_line(self, tmp_path):
        result = run(tmp_path, transcript="  \n")
        assert not result.success
        assert not any(tmp_path.rglob("*.md"))


class TestTranscriber:
    def test_sends_the_audio_inline_and_returns_the_text(self):
        from src.scrapers.voice import PROMPT, VoiceTranscriber
        client = MagicMock()
        client.models.generate_content.return_value = SimpleNamespace(text="  hello there \n")
        with patch("google.genai.Client", return_value=client):
            assert VoiceTranscriber("k", "m").transcribe(b"OGG", "audio/ogg") == "hello there"
        kwargs = client.models.generate_content.call_args.kwargs
        part, prompt = kwargs["contents"]
        assert part.inline_data.data == b"OGG" and part.inline_data.mime_type == "audio/ogg"
        assert prompt == PROMPT and kwargs["model"] == "m"
