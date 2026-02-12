"""Telegram bot integration - uses Telegram as the bookmark queue."""
import json
import re
from dataclasses import dataclass
from pathlib import Path

import requests


@dataclass
class TelegramMessage:
    """A message from Telegram containing a URL to process."""

    update_id: int
    message_id: int
    chat_id: int
    url: str
    user_notes: str | None  # Optional notes/description from user
    date: int


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

    def _load_last_update_id(self) -> int:
        """Load the last processed update ID from state file."""
        if self.state_file.exists():
            with open(self.state_file, "r") as f:
                state = json.load(f)
                return state.get("last_update_id", 0)
        return 0

    def _save_last_update_id(self) -> None:
        """Save the last processed update ID to state file."""
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self.state_file, "w") as f:
            json.dump({"last_update_id": self.last_update_id}, f)

    def _extract_url_and_notes(self, text: str) -> tuple[str | None, str | None]:
        """Extract URL and optional user notes from message text.

        Supports formats:
        - Just URL: https://youtube.com/...
        - URL + notes: https://youtube.com/... \n My notes here
        - URL + notes: https://youtube.com/... | My notes here
        """
        # Match URLs starting with http:// or https://
        url_pattern = r"https?://[^\s<>\"{}|\\^`\[\]]+"
        match = re.search(url_pattern, text)

        if not match:
            return None, None

        url = match.group(0)

        # Get everything after the URL as notes
        remaining = text[match.end():].strip()

        # Remove leading pipe or newline delimiter if present
        if remaining.startswith("|"):
            remaining = remaining[1:].strip()

        user_notes = remaining if remaining else None

        return url, user_notes

    def get_pending_urls(self) -> list[TelegramMessage]:
        """Fetch new messages from Telegram containing URLs."""
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
            url, user_notes = self._extract_url_and_notes(text)

            if url:
                messages.append(
                    TelegramMessage(
                        update_id=update_id,
                        message_id=msg["message_id"],
                        chat_id=chat_id,
                        url=url,
                        user_notes=user_notes,
                        date=msg.get("date", 0),
                    )
                )

        # Save state after processing
        self._save_last_update_id()
        return messages

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
