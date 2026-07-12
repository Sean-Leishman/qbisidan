"""Telegram bot integration - uses Telegram as the bookmark queue."""
import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterator

import requests


@dataclass
class TelegramMessage:
    """A message from Telegram."""

    update_id: int
    message_id: int
    chat_id: int
    text: str  # Raw message text
    date: int
    reply_to_text: str | None = None  # Text of the message being replied to
    # Legacy fields kept for backward compatibility
    url: str | None = None
    user_notes: str | None = None


class TelegramQueue:
    """Manages the Telegram bot queue for bookmark URLs."""

    def __init__(
        self,
        bot_token: str,
        allowed_chat_ids: list[int],
        state_file: str = "data/telegram_state.json",
    ):
        self.bot_token = bot_token
        self.allowed_chat_ids = allowed_chat_ids
        self.base_url = f"https://api.telegram.org/bot{bot_token}"
        self.state_file = Path(state_file)
        self.last_update_id = self._load_last_update_id()

    def _load_state(self) -> dict:
        if self.state_file.exists():
            with open(self.state_file, "r") as f:
                return json.load(f)
        return {}

    def _save_state(self, state: dict) -> None:
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self.state_file, "w") as f:
            json.dump(state, f, indent=2)

    # ── Buffered messages ──────────────────────────────────────────────────────
    # Messages wait_for_callback saw while polling for a button tap. They must be
    # persisted, not held in memory: last_update_id is saved past them the moment
    # they're seen, so Telegram will never resend them. A run-once/cron process
    # exits as soon as the crawl ends — an in-memory buffer would lose whatever
    # the user sent while the manifest was open, permanently.

    def _buffer_message(self, msg: TelegramMessage) -> None:
        state = self._load_state()
        state.setdefault("buffered", []).append(asdict(msg))
        self._save_state(state)

    def _take_buffered(self) -> list[TelegramMessage]:
        state = self._load_state()
        buffered = state.pop("buffered", [])
        if buffered:
            self._save_state(state)
        return [TelegramMessage(**m) for m in buffered]

    def _load_last_update_id(self) -> int:
        return self._load_state().get("last_update_id", 0)

    def _save_last_update_id(self) -> None:
        state = self._load_state()
        state["last_update_id"] = self.last_update_id
        self._save_state(state)

    # ── Activity tracking ──────────────────────────────────────────────────────

    def log_activity(self, activity_type: str) -> None:
        """Increment today's counter for the given activity type."""
        from datetime import date
        today = date.today().isoformat()
        state = self._load_state()
        activity = state.setdefault("activity", {})
        day = activity.setdefault(today, {})
        day[activity_type] = day.get(activity_type, 0) + 1
        self._save_state(state)

    def get_today_stats(self) -> dict[str, int]:
        """Return today's activity counters."""
        from datetime import date
        today = date.today().isoformat()
        state = self._load_state()
        return state.get("activity", {}).get(today, {})

    def _parse_message_update(self, update: dict) -> "TelegramMessage | None":
        """Turn a getUpdates `message` update into a TelegramMessage, or None if it
        should be dropped (wrong chat, blank text). Shared by get_pending_messages
        and wait_for_callback's buffering."""
        msg = update.get("message", {})
        chat_id = msg.get("chat", {}).get("id")

        # Security: only process messages from allowed chats
        if self.allowed_chat_ids and chat_id not in self.allowed_chat_ids:
            return None

        text = msg.get("text", "")
        if not text.strip():
            return None

        reply_to_text = None
        reply_msg = msg.get("reply_to_message")
        if reply_msg:
            reply_to_text = reply_msg.get("text")

        return TelegramMessage(
            update_id=update["update_id"],
            message_id=msg["message_id"],
            chat_id=chat_id,
            text=text,
            date=msg.get("date", 0),
            reply_to_text=reply_to_text,
        )

    def get_pending_messages(self) -> list[TelegramMessage]:
        """Fetch all new messages from Telegram (URLs, commands, plain text).

        Also returns any messages wait_for_callback buffered while it was
        blocked waiting on a button tap.
        """
        messages = self._take_buffered()

        try:
            response = requests.get(
                f"{self.base_url}/getUpdates",
                params={"offset": self.last_update_id + 1, "timeout": 10},
                timeout=15,
            )
            response.raise_for_status()
            updates = response.json().get("result", [])
        except requests.RequestException as e:
            print(f"Error fetching Telegram updates: {e}")
            return messages

        for update in updates:
            self.last_update_id = update["update_id"]

            # callback_query updates (inline button taps) are handled by
            # wait_for_callback, not the regular message flow.
            if "callback_query" in update:
                continue

            msg = self._parse_message_update(update)
            if msg:
                messages.append(msg)

        # Save state after processing
        self._save_last_update_id()
        return messages

    # Keep old method as alias for backward compatibility
    def get_pending_urls(self) -> list[TelegramMessage]:
        """Deprecated: use get_pending_messages() instead."""
        return self.get_pending_messages()

    def send_notification(self, chat_id: int, text: str) -> bool:
        """Send a notification message back to the user."""
        try:
            response = requests.post(
                f"{self.base_url}/sendMessage",
                json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"},
                timeout=10,
            )
            response.raise_for_status()
            return True
        except requests.RequestException as e:
            print(f"Error sending Telegram notification: {e}")
            return False

    def send_message_get_id(self, chat_id: int, text: str) -> int | None:
        """Send a message and return its message_id (for later edits). None on failure."""
        try:
            response = requests.post(
                f"{self.base_url}/sendMessage",
                json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"},
                timeout=10,
            )
            response.raise_for_status()
            return response.json().get("result", {}).get("message_id")
        except requests.RequestException as e:
            print(f"Error sending Telegram message: {e}")
            return None

    def edit_message(self, chat_id: int, message_id: int, text: str) -> bool:
        """Edit an existing bot message in-place (used for progress bars)."""
        try:
            response = requests.post(
                f"{self.base_url}/editMessageText",
                json={
                    "chat_id": chat_id,
                    "message_id": message_id,
                    "text": text,
                    "parse_mode": "Markdown",
                },
                timeout=10,
            )
            # Telegram rejects edits with identical content; treat that as success.
            if response.status_code == 400 and "not modified" in response.text.lower():
                return True
            response.raise_for_status()
            return True
        except requests.RequestException as e:
            print(f"Error editing Telegram message: {e}")
            return False

    # ── Inline keyboards (model-choice manifest) ────────────────────────────────

    def send_buttons(self, chat_id: int, text: str, keyboard: list[list[dict]]) -> int | None:
        """Send a message with an inline keyboard. Returns message_id, or None on failure."""
        try:
            response = requests.post(
                f"{self.base_url}/sendMessage",
                json={
                    "chat_id": chat_id,
                    "text": text,
                    "parse_mode": "Markdown",
                    "reply_markup": {"inline_keyboard": keyboard},
                },
                timeout=10,
            )
            response.raise_for_status()
            return response.json().get("result", {}).get("message_id")
        except requests.RequestException as e:
            print(f"Error sending Telegram buttons: {e}")
            return None

    def edit_message_reply_markup(self, chat_id: int, message_id: int, keyboard: list[list[dict]]) -> bool:
        """Redraw a message's inline keyboard in place (after a button tap)."""
        try:
            response = requests.post(
                f"{self.base_url}/editMessageReplyMarkup",
                json={
                    "chat_id": chat_id,
                    "message_id": message_id,
                    "reply_markup": {"inline_keyboard": keyboard},
                },
                timeout=10,
            )
            if response.status_code == 400 and "not modified" in response.text.lower():
                return True
            response.raise_for_status()
            return True
        except requests.RequestException as e:
            print(f"Error editing Telegram keyboard: {e}")
            return False

    def answer_callback_query(self, callback_query_id: str) -> bool:
        """Acknowledge a button tap. Required after every tap or the Telegram
        client shows an endless loading spinner on the button."""
        try:
            response = requests.post(
                f"{self.base_url}/answerCallbackQuery",
                json={"callback_query_id": callback_query_id},
                timeout=10,
            )
            response.raise_for_status()
            return True
        except requests.RequestException as e:
            print(f"Error answering Telegram callback query: {e}")
            return False

    def wait_for_callback(self, chat_id: int, message_id: int, timeout: float) -> Iterator[str]:
        """Block, polling getUpdates, yielding each button tap's callback_data for
        `message_id` until the timeout elapses. The caller drives the loop: keep
        iterating to redraw between taps, stop iterating (break) once it sees a
        terminal tap like "start"/"cancel". If the generator runs out (for/else)
        the wait timed out with no terminal tap.

        # ponytail: this is a blocking in-process wait — fine because this bot
        # serves a single user running one crawl at a time. If the process dies
        # mid-wait, the crawl resumes and re-asks on the next run. Upgrade path
        # if that ever gets annoying: a persistent pending-decision store instead
        # of blocking here.

        Any non-callback message seen while polling is buffered (not lost, not
        silently dropped) and handed back by the next get_pending_messages() call
        — a button tap must never eat a message the user sent in the meantime.
        """
        deadline = time.monotonic() + timeout
        poll_timeout = 2  # short long-poll so we can keep rechecking the deadline

        while time.monotonic() < deadline:
            try:
                response = requests.get(
                    f"{self.base_url}/getUpdates",
                    params={"offset": self.last_update_id + 1, "timeout": poll_timeout},
                    timeout=poll_timeout + 5,
                )
                response.raise_for_status()
                updates = response.json().get("result", [])
            except requests.RequestException as e:
                print(f"Error polling Telegram updates: {e}")
                updates = []
                time.sleep(poll_timeout)  # avoid hammering the API on a network error

            if not updates:
                continue

            # Process the whole batch (buffering messages, advancing
            # last_update_id) BEFORE yielding anything. Otherwise a caller that
            # breaks out right after the first yielded callback would leave
            # later updates in this same batch unbuffered and unprocessed.
            callbacks = []
            for update in updates:
                self.last_update_id = update["update_id"]
                cq = update.get("callback_query")
                if cq:
                    self.answer_callback_query(cq["id"])
                    if cq.get("message", {}).get("message_id") == message_id:
                        callbacks.append(cq.get("data", ""))
                    continue
                buffered = self._parse_message_update(update)
                if buffered:
                    self._buffer_message(buffered)
            self._save_last_update_id()

            for data in callbacks:
                yield data

    def send_processing_started(self, chat_id: int, url: str) -> bool:
        """Notify user that processing has started."""
        return self.send_notification(chat_id, f"Processing: {url}")

    def send_success(self, chat_id: int, title: str, folder: str) -> bool:
        """Notify user of successful processing."""
        return self.send_notification(
            chat_id, f"Created note: *{title}*\nSaved to: `{folder}`"
        )

    def send_error(self, chat_id: int, url: str, error: str) -> bool:
        """Notify user of processing error."""
        return self.send_notification(chat_id, f"Failed to process: {url}\nError: {error}")
