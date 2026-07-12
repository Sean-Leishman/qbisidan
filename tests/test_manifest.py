"""Tests for the crawl model-choice manifest (Processor._run_model_manifest /
_build_manifest_keyboard). These build a bare Processor via __new__ so no real
AI clients, Telegram token, or vault path are needed — only the attributes the
manifest code actually touches."""
from types import SimpleNamespace
from unittest.mock import MagicMock

from src.processor import Processor
from src.scrapers.channel import VideoStub


def make_processor(provider: str = "gemini") -> Processor:
    p = Processor.__new__(Processor)
    p.config = SimpleNamespace(ai=SimpleNamespace(provider=provider))
    p.telegram = MagicMock()
    p.telegram.send_buttons.return_value = 999
    return p


def make_stubs(n: int) -> list[VideoStub]:
    return [
        VideoStub(video_id=f"v{i}", url=f"https://example.com/{i}", title=f"Video {i}", upload_date=None)
        for i in range(n)
    ]


class TestManifestCallbackData:
    def test_callback_data_stays_under_64_bytes_with_long_title(self):
        p = make_processor()
        stubs = [VideoStub(video_id="abc123", url="https://x", title="X" * 500, upload_date=None)]
        keyboard = p._build_manifest_keyboard(stubs, "gemini", {})

        for row in keyboard:
            for button in row:
                assert len(button["callback_data"].encode("utf-8")) < 64
        # And it's index-based, never the (huge) title.
        video_row_data = [b["callback_data"] for row in keyboard for b in row if b["callback_data"].startswith("v")]
        assert video_row_data == ["v0"]


class TestManifestCycleLogic:
    def test_five_taps_cycles_back_to_default(self):
        """default -> gemini -> groq -> claude -> skip -> default"""
        p = make_processor()
        stubs = make_stubs(1)
        p.telegram.wait_for_callback.return_value = iter(["v0", "v0", "v0", "v0", "v0", "start"])

        result = p._run_model_manifest(chat_id=1, display_name="Test", stubs=stubs, timeout=5)

        assert result is not None
        _, overrides = result
        assert stubs[0].video_id not in overrides  # back to "default" == absent

    def test_single_tap_selects_gemini(self):
        p = make_processor(provider="claude")
        stubs = make_stubs(1)
        p.telegram.wait_for_callback.return_value = iter(["v0", "start"])

        _, overrides = p._run_model_manifest(chat_id=1, display_name="Test", stubs=stubs, timeout=5)

        assert overrides[stubs[0].video_id] == "gemini"

    def test_four_taps_selects_skip(self):
        p = make_processor()
        stubs = make_stubs(1)
        p.telegram.wait_for_callback.return_value = iter(["v0", "v0", "v0", "v0", "start"])

        _, overrides = p._run_model_manifest(chat_id=1, display_name="Test", stubs=stubs, timeout=5)

        assert overrides[stubs[0].video_id] == "skip"

    def test_default_model_button_cycles_gemini_groq_claude(self):
        p = make_processor(provider="gemini")
        stubs = make_stubs(1)
        p.telegram.wait_for_callback.return_value = iter(["d", "start"])

        default_provider, _ = p._run_model_manifest(chat_id=1, display_name="Test", stubs=stubs, timeout=5)

        assert default_provider == "groq"

    def test_redraws_keyboard_after_each_tap(self):
        p = make_processor()
        stubs = make_stubs(1)
        p.telegram.wait_for_callback.return_value = iter(["v0", "v0", "start"])

        p._run_model_manifest(chat_id=1, display_name="Test", stubs=stubs, timeout=5)

        assert p.telegram.edit_message_reply_markup.call_count == 2  # once per non-terminal tap


class TestManifestTimeoutAndCancel:
    def test_timeout_with_no_start_proceeds_on_config_default(self):
        p = make_processor(provider="claude")
        stubs = make_stubs(1)
        # Taps happen but Start never comes — generator just runs out (timeout).
        p.telegram.wait_for_callback.return_value = iter(["v0", "v0"])

        default_provider, overrides = p._run_model_manifest(chat_id=1, display_name="Test", stubs=stubs, timeout=5)

        assert default_provider == "claude"
        assert overrides == {}  # untapped taps are discarded, not just unconfirmed

    def test_cancel_aborts_and_returns_none(self):
        p = make_processor()
        stubs = make_stubs(2)
        p.telegram.wait_for_callback.return_value = iter(["v0", "cancel"])

        result = p._run_model_manifest(chat_id=1, display_name="Test", stubs=stubs, timeout=5)

        assert result is None

    def test_manifest_not_shown_returns_config_default(self):
        """send_buttons failing (e.g. Telegram API error) must not block the crawl."""
        p = make_processor(provider="groq")
        p.telegram.send_buttons.return_value = None
        stubs = make_stubs(1)

        default_provider, overrides = p._run_model_manifest(chat_id=1, display_name="Test", stubs=stubs, timeout=5)

        assert default_provider == "groq"
        assert overrides == {}
        p.telegram.wait_for_callback.assert_not_called()
