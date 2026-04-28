"""Telegram bot integration - uses Telegram as the bookmark queue."""
import json
from dataclasses import dataclass
from pathlib import Path

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

    def get_pending_messages(self) -> list[TelegramMessage]:
        """Fetch all new messages from Telegram (URLs, commands, plain text)."""
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
            return []

        messages = []
        for update in updates:
            update_id = update["update_id"]
            self.last_update_id = update_id

            msg = update.get("message", {})
            chat_id = msg.get("chat", {}).get("id")

            # Security: only process messages from allowed chats
            if self.allowed_chat_ids and chat_id not in self.allowed_chat_ids:
                continue

            text = msg.get("text", "")
            if not text.strip():
                continue

            # Extract reply-to context if present
            reply_to_text = None
            reply_msg = msg.get("reply_to_message")
            if reply_msg:
                reply_to_text = reply_msg.get("text")

            messages.append(
                TelegramMessage(
                    update_id=update_id,
                    message_id=msg["message_id"],
                    chat_id=chat_id,
                    text=text,
                    date=msg.get("date", 0),
                    reply_to_text=reply_to_text,
                )
            )

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
