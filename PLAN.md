# Obsidian Bookmark Automation - Implementation Plan

## Overview

A Python-based automated workflow that:
1. Monitors a queue file for bookmarked URLs
2. Fetches content and metadata from webpages/YouTube
3. Generates AI summaries using Claude API
4. Creates templated Obsidian notes in the appropriate folder

## Architecture

```
┌─────────────────┐     ┌──────────────────┐     ┌────────────────────────┐
│  Phone/Desktop  │     │  Telegram Bot    │     │  Python Worker (cron)  │
│  Share URL ─────┼────►│  (message queue) │◄────┤  Polls for new URLs    │
└─────────────────┘     └──────────────────┘     └───────────┬────────────┘
                                                             │
                               ┌─────────────────────────────┼─────────────────┐
                               │                             │                 │
                               ▼                             ▼                 ▼
                        ┌──────────────┐            ┌──────────────┐   ┌──────────────────┐
                        │ Claude API   │            │ YouTube API  │   │ Web Scraping     │
                        │ (summaries)  │            │ (transcripts)│   │ (article content)│
                        └──────────────┘            └──────────────┘   └──────────────────┘
                                                             │
                                                             ▼
                                                  ┌────────────────────────┐
                                                  │  Obsidian Vault        │
                                                  │  - 02 Sources/Videos   │
                                                  │  - 02 Sources/Articles │
                                                  └────────────────────────┘
```

## Project Structure

```
obsidian_helper/
├── config.yaml              # Configuration (vault path, API keys, etc.)
├── requirements.txt         # Python dependencies
├── main.py                  # Entry point - runs the worker
├── data/
│   └── telegram_state.json  # Tracks last processed message ID
├── src/
│   ├── __init__.py
│   ├── config.py            # Configuration loading
│   ├── telegram_queue.py    # Telegram bot polling
│   ├── processor.py         # Main processing orchestrator
│   ├── scrapers/
│   │   ├── __init__.py
│   │   ├── base.py          # Base scraper interface
│   │   ├── youtube.py       # YouTube: transcript + metadata
│   │   └── webpage.py       # General webpage scraping
│   ├── summarizer.py        # Claude API integration
│   └── obsidian.py          # Markdown file generation
└── templates/
    ├── video.md             # Template for YouTube videos
    └── article.md           # Template for articles/webpages
```

## Components

### 1. Telegram Queue (`src/telegram_queue.py`)
- Polls Telegram Bot API for new messages
- Filters to only allowed chat IDs (your account)
- Extracts URLs from messages
- Sends success/failure notifications back to chat
- Tracks last processed message ID to avoid duplicates

### 2. URL Detection & Routing
Routes URLs to appropriate scrapers and output folders:
| URL Pattern | Scraper | Output Folder |
|-------------|---------|---------------|
| `youtube.com/watch` | YouTube | `02 Sources/Videos` |
| `youtu.be/*` | YouTube | `02 Sources/Videos` |
| Other HTTPS | Webpage | `02 Sources/Articles` |

### 3. YouTube Scraper (`src/scrapers/youtube.py`)
- Uses `youtube-transcript-api` for transcript fetching
- Uses `yt-dlp` for metadata (title, author, upload date, thumbnail)
- Fallback: YouTube oEmbed API for basic metadata if transcript unavailable

### 4. Webpage Scraper (`src/scrapers/webpage.py`)
- Uses `newspaper3k` or `trafilatura` for article extraction
- Extracts: title, author, publish date, main content
- Falls back to BeautifulSoup + meta tags if extraction fails

### 5. AI Summarizer (`src/summarizer.py`)
- Uses Anthropic Claude API (claude-sonnet-4-20250514 for cost efficiency)
- Prompt template tailored for content type:
  - YouTube: summarize video based on transcript
  - Article: summarize key points and takeaways
- Returns structured summary (3-5 bullet points + brief overview)

### 6. Obsidian Note Generator (`src/obsidian.py`)
Generates markdown matching your existing format:
```markdown
---
title: "Video/Article Title"
author:
  - "[[Author Name]]"
published: 2025-01-15
source: "https://..."
image: "https://..."
created: 2025-02-11
tags:
  - "videos"  # or "articles"
---
> [!note]- **Properties**
> **Created:** 2025-02-11T10:00:00
> **Published:** 2025-01-15
> **Source:** https://...
> **Origin:**
> **Status:** #Inbox
> **Tags:**

---
# Overview

## Summary
[AI-generated summary here]

## Notes

---
![Title](https://youtube.com/...)  # for videos
```

## Configuration (`config.yaml`)

```yaml
vault_path: "/mnt/c/Users/leish/OneDrive/Documents/Obsidian Vault"

telegram:
  bot_token: "${TELEGRAM_BOT_TOKEN}"     # from environment
  allowed_chat_ids:
    - 123456789                          # your Telegram chat ID
  send_notifications: true               # reply with success/failure

anthropic:
  api_key: "${ANTHROPIC_API_KEY}"        # from environment
  model: "claude-sonnet-4-20250514"
  max_tokens: 1024

output_folders:
  youtube: "02 Sources/Videos"
  article: "02 Sources/Articles"
  default: "Clippings"

youtube:
  fetch_transcript: true
  transcript_languages: ["en", "en-US", "en-GB"]
```

## Dependencies (`requirements.txt`)

```
anthropic>=0.18.0
youtube-transcript-api>=0.6.0
yt-dlp>=2024.0.0
trafilatura>=1.6.0
beautifulsoup4>=4.12.0
requests>=2.31.0
pyyaml>=6.0
python-dateutil>=2.8.0
```

## Cron Setup

```bash
# Run every hour
0 * * * * cd /home/seanleishman/personal/obsidian_helper && /usr/bin/python3 main.py >> /var/log/obsidian_helper.log 2>&1
```

Or use systemd timer for more robust scheduling.

## Implementation Order

1. **Phase 1: Core Infrastructure**
   - [ ] `config.py` - Configuration loading
   - [ ] `main.py` - Entry point
   - [ ] `telegram_queue.py` - Telegram bot polling

2. **Phase 2: Content Fetching**
   - [ ] `scrapers/base.py` - Base interface
   - [ ] `scrapers/youtube.py` - YouTube transcript + metadata
   - [ ] `scrapers/webpage.py` - Article extraction

3. **Phase 3: AI Integration**
   - [ ] `summarizer.py` - Claude API integration
   - [ ] Summary prompt templates

4. **Phase 4: Output Generation**
   - [ ] `obsidian.py` - Markdown generation
   - [ ] Templates matching your existing format

5. **Phase 5: Integration & Polish**
   - [ ] `processor.py` - Orchestration
   - [ ] Telegram notifications (success/failure replies)
   - [ ] Error handling & logging
   - [ ] Cron/systemd setup

## Usage

```bash
# 1. Set up environment variables
export TELEGRAM_BOT_TOKEN="your-bot-token"
export ANTHROPIC_API_KEY="your-api-key"

# 2. Add a bookmark (from phone or desktop)
#    Just send any URL to your Telegram bot!

# 3. Process manually (or let cron handle it)
python main.py

# 4. Check logs
tail -f obsidian_helper.log

# 5. Bot replies in Telegram with success/failure
```

## Error Handling

- Failed URLs: Bot replies with error message in Telegram
- Retries: 3 attempts with exponential backoff
- Partial failures don't block other URLs
- Missing transcript: falls back to metadata-only summary
- All errors logged to `obsidian_helper.log`

## Mobile Integration via Telegram Bot

Use Telegram as both the capture mechanism AND the queue. No serverless hosting needed - the worker polls Telegram directly.

### How It Works

```
┌─────────────┐     ┌─────────────────┐     ┌──────────────────┐
│   Phone     │     │    Telegram     │     │  Python Worker   │
│  (any app)  │ ──► │   Bot + Chat    │ ◄── │  (cron/polling)  │
└─────────────┘     └─────────────────┘     └──────────────────┘
      │                     │                        │
      │ Share URL           │ Stores messages        │ getUpdates API
      │ to bot              │ (acts as queue)        │ processes URLs
      └─────────────────────┴────────────────────────┘
```

### Setup Steps

#### 1. Create Telegram Bot
1. Open Telegram, search for `@BotFather`
2. Send `/newbot`
3. Choose a name: `Obsidian Bookmark Bot`
4. Choose a username: `your_obsidian_bookmark_bot`
5. Save the **API token** (looks like `123456789:ABCdefGHIjklMNOpqrsTUVwxyz`)

#### 2. Get Your Chat ID
1. Send any message to your new bot
2. Visit: `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates`
3. Find `"chat":{"id":123456789}` - this is your chat ID
4. Save this ID (used to restrict bot to only your messages)

#### 3. Configure Worker

```yaml
# config.yaml
telegram:
  bot_token: "${TELEGRAM_BOT_TOKEN}"
  allowed_chat_ids:
    - 123456789  # Your chat ID (security: ignore messages from others)

  # Optional: send status updates back to Telegram
  send_notifications: true
```

### New Components

#### `src/telegram_queue.py`
```python
"""Telegram bot integration - uses Telegram as the bookmark queue."""
import requests
from dataclasses import dataclass

@dataclass
class TelegramMessage:
    message_id: int
    chat_id: int
    text: str
    date: int

class TelegramQueue:
    def __init__(self, bot_token: str, allowed_chat_ids: list[int]):
        self.bot_token = bot_token
        self.allowed_chat_ids = allowed_chat_ids
        self.base_url = f"https://api.telegram.org/bot{bot_token}"
        self.last_update_id = self._load_last_update_id()

    def get_pending_urls(self) -> list[TelegramMessage]:
        """Fetch new messages from Telegram (long polling)."""
        response = requests.get(
            f"{self.base_url}/getUpdates",
            params={"offset": self.last_update_id + 1, "timeout": 30}
        )
        updates = response.json().get("result", [])

        messages = []
        for update in updates:
            self.last_update_id = update["update_id"]
            msg = update.get("message", {})

            # Security: only process messages from allowed chats
            if msg.get("chat", {}).get("id") not in self.allowed_chat_ids:
                continue

            text = msg.get("text", "")
            if self._is_url(text):
                messages.append(TelegramMessage(
                    message_id=msg["message_id"],
                    chat_id=msg["chat"]["id"],
                    text=text.strip(),
                    date=msg["date"]
                ))

        self._save_last_update_id()
        return messages

    def send_notification(self, chat_id: int, text: str):
        """Send processing status back to user."""
        requests.post(
            f"{self.base_url}/sendMessage",
            json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}
        )

    def _is_url(self, text: str) -> bool:
        return text.startswith(("http://", "https://"))
```

### Usage Flow

1. **From Phone:** Share any URL to your Telegram bot
   - YouTube app → Share → Telegram → Your Bot
   - Browser → Share → Telegram → Your Bot
   - Or just paste URL in chat with bot

2. **Worker runs (cron):**
   - Polls Telegram for new messages
   - Extracts URLs from messages
   - Processes each URL (fetch, summarize, create note)
   - Sends confirmation back to Telegram: "✓ Created note: Video Title"

3. **Check status:** Bot replies with success/failure for each bookmark

### Example Telegram Conversation

```
You: https://www.youtube.com/watch?v=dQw4w9WgXcQ

Bot: 📥 Received! Processing...

Bot: ✅ Created note: Rick Astley – Never Gonna Give You Up
     📁 Saved to: 02 Sources/Videos

You: https://example.com/interesting-article

Bot: 📥 Received! Processing...

Bot: ✅ Created note: Interesting Article Title
     📁 Saved to: 02 Sources/Articles
```

### Updated Project Structure

```
obsidian_helper/
├── config.yaml
├── requirements.txt
├── main.py
├── data/
│   └── telegram_state.json     # Stores last_update_id
├── src/
│   ├── __init__.py
│   ├── config.py
│   ├── telegram_queue.py       # NEW: Telegram integration
│   ├── processor.py
│   ├── scrapers/
│   │   ├── __init__.py
│   │   ├── base.py
│   │   ├── youtube.py
│   │   └── webpage.py
│   ├── summarizer.py
│   └── obsidian.py
└── templates/
    ├── video.md
    └── article.md
```

### Updated Dependencies

```
# requirements.txt (additions)
python-telegram-bot>=20.0    # Optional: for richer bot features
# OR just use requests (already included) for simple polling
```

### Security Notes

- `allowed_chat_ids` ensures only YOU can add bookmarks
- Bot token should be in environment variable, not committed to git
- Consider rate limiting if needed

### Optional: Webhook Mode (For Instant Processing)

If you want instant processing instead of polling, deploy to a free serverless platform:

**Cloudflare Workers (Free: 100k requests/day):**
- Receives webhook from Telegram instantly
- Writes URL to a file or triggers the Python worker
- More complex setup but zero delay

**For now:** Polling mode is simpler and works well with hourly cron.

## Future Enhancements (Out of Scope)

- Browser extension for one-click capture
- Podcast/audio transcription support
- PDF processing
- Raindrop.io / Pocket integration
- Webhook mode for instant processing
