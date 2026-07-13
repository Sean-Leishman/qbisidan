# Obsidian Helper

Telegram bot that bridges your phone to your Obsidian vault. Send URLs, todos, notes, and calendar events from Telegram and they land in the right place automatically.

## Setup

### 1. Create a Telegram Bot

1. Open Telegram, search for `@BotFather`
2. Send `/newbot`, choose a name and username
3. Save the API token

### 2. Get Your Chat ID

1. Send any message to your bot
2. Visit `https://api.telegram.org/bot<TOKEN>/getUpdates`
3. Find `"chat":{"id":123456789}`

### 3. Configure

```bash
export TELEGRAM_BOT_TOKEN="your-bot-token"
export GEMINI_API_KEY="your-gemini-key"      # default provider
# export ANTHROPIC_API_KEY="your-key"        # alternative
# export GROQ_API_KEY="your-key"             # alternative
```

Edit `config.yaml`:
- `vault_path` — path to your Obsidian vault
- `telegram.allowed_chat_ids` — your Telegram chat ID
- `vault_writer.ics_folder` — path your calendar app watches for `.ics` files (optional)

### 4. Install & Run

```bash
uv sync
python main.py               # run once (for cron)
python main.py --watch       # poll continuously (every 60s)
python main.py --test URL    # test with a specific URL
```

---

## Command Reference

| Command | Destination | Notes |
|---------|-------------|-------|
| `<URL>` | `02 Sources/Videos/` or `Articles/` | Full AI pipeline |
| `<URL> \| my notes` | Same, with personal context | Notes guide AI summarisation |
| *(reply to bot msg)* `question` | That note's `## Questions` | Reply to the "Created note:" message |
| `/help` | Telegram reply | Full command reference in chat |
| `/search <query>` | Telegram reply | Full-text search across all vault notes |
| `/status` | Telegram reply | Today's capture activity summary |
| `/todo <text>` | `08 Trackers/Inbox.md` | Unsorted capture, triage later |
| `/todo <text> @<date>` | `08 Trackers/Deadlines.md` | Parsed due date |
| `/someday <text>` | `08 Trackers/Someday.md` | No urgency, park it |
| `/read <url or title>` | `08 Trackers/To Be Read.md` | Book / article reading list |
| `/watch <url or title>` | `08 Trackers/To Be Watched.md` | Video / film watch list |
| `/note <text>` | `01 Fleeting Notes/<auto-title>.md` | Research capture, new file |
| `/note <url>` | `01 Fleeting Notes/<auto-title>.md` | Raw link, no AI processing |
| `/daily <text>` | `06 Daily Notes/<today>.md` → `### Notes` | |
| `/daily task <text>` | `06 Daily Notes/<today>.md` → `### Tasks` | Checkbox |
| `/project <name> todo <text>` | `07 Projects/<name>/Todo.md` | |
| `/project <name> todo <text> @<date>` | `07 Projects/<name>/Todo.md` | With due date |
| `/project <name> <text>` | `07 Projects/<name>/Notes.md` | |
| `/event <title> <date> <time>` | ICS file + `06 Daily Notes/<date>.md` | |
| *(plain text)* | `08 Trackers/Inbox.md` | Safe default |

### Date syntax (`@`)

Use `@` to attach a due date:

```
/todo Submit paper @friday
/todo Tax return @2026-04-30
/todo Call dentist @tomorrow
/project my-project todo Fix auth @monday
```

---

## Workflows

### Bookmark a URL

Send any URL. The bot scrapes, summarises with AI, and saves to your vault. YouTube videos get AI-generated review questions.

```
You:  https://www.youtube.com/watch?v=dQw4w9WgXcQ
Bot:  Processing...
Bot:  Created note: *Rick Astley – Never Gonna Give You Up*
      Saved to: 02 Sources/Videos
```

Add personal notes to guide the summary:

```
You:  https://example.com/article | Focus on the caching section
```

### Ask a Question About a Note

Reply to the bot's "Created note:" message with any question. It gets appended to that note's `## Questions` section.

```
Bot:  Created note: *Why Transformers Work*
You:  (reply) Why did attention replace recurrence rather than complement it?
Bot:  Question added to: Why Transformers Work
```

### Capture Todos

```
You:  /todo Review literature by end of week
Bot:  Added to Inbox: Review literature

You:  /todo Submit conference paper @2026-05-15
Bot:  Added to Deadlines: Submit conference paper (2026-05-15)

You:  /someday Learn Rust properly
Bot:  Added to Someday: Learn Rust properly
```

### Reading & Watch Lists

```
You:  /read The Buried Giant, Kazuo Ishiguro
Bot:  Added to To Be Read: The Buried Giant, Kazuo Ishiguro

You:  /watch https://youtube.com/watch?v=xyz
Bot:  Added to To Be Watched: https://youtube.com/watch?v=xyz
```

### Fleeting Notes

Creates a new file in `01 Fleeting Notes/` with an auto-generated title from the first few words.

```
You:  /note Attention is essentially a soft dictionary lookup — keys and queries determine relevance
Bot:  Created note: Attention is essentially a soft

You:  /note https://arxiv.org/abs/2501.12948
Bot:  Created note: https arxiv.org abs 2501.12948   (raw, no AI)
```

### Daily Note

```
You:  /daily task Review PR for obsidian-helper
Bot:  Added task to daily note: Review PR for obsidian-helper

You:  /daily Interesting conversation about sparse attention
Bot:  Added to daily note: Interesting conversation...

You:  Just a random thought                    ← plain text
Bot:  Added to Inbox: Just a random thought    ← goes to Inbox, not daily note
```

### Project Files

```
You:  /project obsidian-helper todo Add webhook support @friday
Bot:  Added todo to obsidian-helper: Add webhook support (2026-04-10)

You:  /project obsidian-helper Consider async polling for lower latency
Bot:  Added note to obsidian-helper: Consider async polling...
```

### Calendar Events

Creates an `.ics` file (if `ics_folder` is configured) and adds an entry to that date's daily note.

```
You:  /event Dentist 2026-04-10 14:00
Bot:  Event: Dentist at 2026-04-10 14:00

You:  /event Team sync tomorrow 10am
```

---

## Vault Layout

```
Obsidian Vault/
  01 Fleeting Notes/
    <auto-title>.md          ← /note <text>  or  /note <url>
  02 Sources/
    Videos/                  ← YouTube bookmarks
    Reels/                   ← Instagram reel/post bookmarks
    Articles/<Category>/     ← Article bookmarks (AI-routed)
  03 Tags/
    <Tag>.md                 ← auto-created for new tag terms
  04 Indexes/
    <Index>.md               ← existing index pages (linked if matched)
  06 Daily Notes/
    YYYY-MM-DD.md            ← /daily task, /daily, /event
  07 Projects/
    <name>/
      Todo.md                ← /project <name> todo
      Notes.md               ← /project <name> <text>
  08 Trackers/
    Inbox.md                 ← /todo (no date), plain text
    Deadlines.md             ← /todo @<date>
    Someday.md               ← /someday
    To Be Read.md            ← /read
    To Be Watched.md         ← /watch
```

---

## Architecture

```
Telegram message
      │
      ▼
 Command Parser ─────────────────────────────────────────────────────────────┐
      │                                                                       │
      ├─ URL ──────► Scraper ──► Summarizer ──► QuestionAgent                │
      │                              │                                        │
      │                         NoteLinkEngine.resolve_tags                  │
      │                         (looks up 03 Tags / 04 Indexes,              │
      │                          creates tag files for new terms)            │
      │                              │                                        │
      │                         Note file                                    │
      │                                                                       │
      ├─ /help           ──► Telegram reply (command reference)              │
      ├─ /todo           ──► VaultWriter.append_to_inbox / append_to_deadlines│
      ├─ /someday        ──► VaultWriter.append_to_someday                    │
      ├─ /read           ──► VaultWriter.append_to_read                       │
      ├─ /watch          ──► VaultWriter.append_to_watch                      │
      ├─ /note           ──► VaultWriter.create_fleeting_note                 │
      ├─ /daily          ──► VaultWriter.append_to_daily_note / _task         │
      ├─ /project todo   ──► VaultWriter.append_to_project_todo               │
      ├─ /project        ──► VaultWriter.append_to_project_note               │
      ├─ /event          ──► VaultWriter.create_event + daily note            │
      ├─ reply to bot    ──► VaultWriter.append_question_to_note              │
      └─ plain text      ──► VaultWriter.append_to_inbox                     │
                                                                              │
      ◄──────────────── Telegram notification ──────────────────────────────┘
```

### AI Sub-Agents

- **Summarizer** (`src/summarizer.py`) — structured summary + metadata (category, plain-text tag terms, action items, key concepts). YouTube summaries cover the full video with timestamped segments.
- **QuestionAgent** (`src/agents/question_agent.py`) — Socratic review questions for YouTube videos.
- **InstagramScraper** (`src/scrapers/instagram.py`) — downloads a reel with yt-dlp and sends it straight to Gemini's Files API for one call that both transcribes speech and describes on-screen visuals. Always uses `ai.gemini_api_key` regardless of the configured summarizer provider (video input is Gemini-only); missing key, over-duration video, or a failed download/Gemini call all degrade to a caption-only note rather than failing the bookmark.

Both use the provider from `config.yaml → ai:` (currently Gemini Flash).

### Search

`/search <query>` does a multi-term keyword search across all `.md` files in the vault. Scoring weights title matches 5× over body hits, deprioritises daily notes and trackers, and returns the top 5 results with a snippet from the most relevant line.

Semantic search (vector similarity via embeddings) is not implemented but could be layered on top — Obsidian's [Smart Connections](https://github.com/brianpetro/obsidian-smart-connections) plugin writes embeddings to `.smart-connections/` that could be read from Python if you use that plugin.

### Tag Resolution

After summarisation, `NoteLinkEngine.resolve_tags` (`src/linker.py`) maps each plain-text tag term to a real vault page:

1. Searches `03 Tags/` and `04 Indexes/` for an exact match (case-insensitive).
2. If found → wikilinks to the existing note.
3. If not found → creates a minimal note in `03 Tags/` and wikilinks to it.

Tags in notes are always vault-relative wikilinks — never bare `#hashtags`.

---

## Configuration (`config.yaml`)

```yaml
ai:
  provider: "gemini"          # gemini | groq | anthropic

vault_writer:
  daily_notes_folder: "06 Daily Notes"
  ics_folder: ""              # path your calendar app watches for ICS files

instagram:
  cookies_file: ""            # Netscape cookies.txt, for private/age-gated reels
  video_model: "gemini-2.0-flash"   # must accept video input; always uses the Gemini key
  max_duration_seconds: 180   # longer reels degrade to caption-only, not a huge Gemini bill

routing_categories:           # AI routes articles to category subfolders
  - name: "Programming"
    folder: "02 Sources/Articles/Programming"
  - name: "Machine Learning"
    folder: "02 Sources/Articles/ML"
```

---

## Cron Setup

```bash
# Run every 5 minutes
*/5 * * * * cd /home/seanleishman/personal/obsidian_helper && .venv/bin/python main.py >> obsidian_helper.log 2>&1
```
