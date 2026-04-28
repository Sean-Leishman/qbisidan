# Architecture

## High-level shape

Telegram is both the inbox and the queue. A single Python process polls
`getUpdates`, parses each message into a typed command, dispatches to a
handler, and writes the result to the Obsidian vault on local disk.

```
phone/desktop ──► Telegram chat ──► getUpdates (long poll)
                                          │
                                          ▼
                                  TelegramQueue
                                          │
                                          ▼
                                  parse_message  (commands.py)
                                          │
                                          ▼
                                  Processor._HANDLERS
                            ┌─────────────┼─────────────┐
                            ▼             ▼             ▼
                        URL pipeline   VaultWriter   reply handlers
                            │           (trackers,    (search, status,
                            ▼            daily,        help)
                  scraper → summarizer    fleeting,
                       │                  events,
                       ▼                  questions)
                  NoteLinkEngine
                  resolve_tags / dedup
                       │
                       ▼
                  ObsidianNoteGenerator → file in vault
```

Bot replies (success/error) come from `TelegramQueue.send_*` and are driven
by the `ProcessingResult` returned by each handler.

## Module map

- `main.py` — argparse + logging + mode selection. Calls `Processor.run()` once
  or in a 60-second loop.
- `src/config.py` — dataclasses for every config section. `Config.load(path)`
  reads YAML and expands `${ENV}` placeholders. Provides
  `get_folder_for_category` for AI-routed articles.
- `src/telegram_queue.py` — wraps Telegram Bot API. Stores `last_update_id`
  and per-day activity counters in `data/telegram_state.json`.
  `TelegramMessage` keeps raw `text` plus optional `reply_to_text` (used to
  detect "reply to bot's Created note: …" → question).
- `src/commands.py` — pure parser. Returns a `ParsedCommand` with a
  `command_type` from a fixed set: `url`, `help`, `search`, `status`,
  `todo_inbox`, `todo_deadline`, `someday`, `read`, `watch`, `fleeting_note`,
  `daily_note`, `daily_task`, `project_todo`, `project_note`, `event`,
  `question`, `inbox`. Date parsing handles `@today`, `@friday`,
  `@2026-05-15`, etc.
- `src/processor.py` — `Processor` constructs every collaborator in `__init__`
  and routes via the `_HANDLERS` dict (`command_type` → method name). All
  Telegram replies are sent here, not from inside handlers.
- `src/scrapers/` — `BaseScraper` interface (`can_handle`, `scrape`).
  YouTube scraper tries `youtube-transcript-api` first, then `yt-dlp` for
  metadata. Webpage scraper uses `trafilatura` with a BeautifulSoup fallback.
  `ScrapedContent` is the shared dataclass returned to the summariser.
- `src/summarizer.py` — extends `BaseAgent`. Two prompt templates
  (YouTube vs. article) request a structured-output JSON block at the end
  with `category`, `tags`, `action_items`, `review_questions`,
  `key_concepts`. Tracks `content_truncated` when the source had to be
  shortened to fit the context window.
- `src/agents/base.py` — `BaseAgent` abstracts Groq / Gemini / Anthropic so
  every sub-agent shares one client and one `_call(prompt)` method.
- `src/agents/question_agent.py` — Socratic review questions for YouTube
  notes only.
- `src/agents/vault_writer.py` — every non-bookmark mutation:
  - `append_to_inbox` / `append_to_deadlines` / `append_to_someday` /
    `append_to_read` / `append_to_watch`
  - `create_fleeting_note` (auto-titled, `01 Fleeting Notes/`)
  - `append_to_daily_note` / `append_task_to_daily_note`
    (`06 Daily Notes/<YYYY-MM-DD>.md`)
  - `append_to_project_todo` / `append_to_project_note`
    (`07 Projects/<name>/`)
  - `create_event` (writes `.ics` if `ics_folder` is configured) and
    `add_event_to_daily_note`
  - `append_question_to_note` (replies → existing source note's
    `## Questions` section)
- `src/linker.py` — `NoteLinkEngine`:
  - Builds an in-memory case-insensitive index of every `.md` in the vault
    (skips dot-prefixed paths) with a TTL cache.
  - `find_note_by_url(url)` — duplicate detection: scans every md for the
    URL string, returns the first match's stem.
  - `resolve_tags(raw_tags)` — for each plain-text tag, links to an existing
    `03 Tags/` or `04 Indexes/` page if it exists, otherwise creates a
    minimal `03 Tags/<Term>.md` and links to that.
  - `find_related_notes(key_concepts, tags)` — surfaces other notes for the
    "Related" block at the bottom of generated source notes.
- `src/obsidian.py` — `ObsidianNoteGenerator`. Pure markdown assembly:
  YAML frontmatter, `> [!note]- **Properties**` callout, summary,
  optional truncation warning, related links, and a tag line of
  pre-resolved wikilinks. Filename comes from the scraped title.
- `src/search.py` — `VaultSearch.search(query)` walks `*.md`, scores
  `title_hits * 5 + min(content_hits, 20)`, and deprioritises
  `06 Daily Notes` and `08 Trackers` (× 0.3). Returns top 5 with a
  cleaned snippet from the densest line.

## Vault layout (referenced by code)

```
01 Fleeting Notes/      <- /note
02 Sources/Videos/      <- YouTube bookmarks
02 Sources/Articles/<Cat>/  <- article bookmarks (AI-routed by category)
03 Tags/                <- created on demand by linker.resolve_tags
04 Indexes/             <- pre-existing curated index pages
06 Daily Notes/<YYYY-MM-DD>.md
07 Projects/<name>/{Todo,Notes}.md
08 Trackers/{Inbox,Deadlines,Someday,To Be Read,To Be Watched}.md
```

## State

- `data/telegram_state.json` — `last_update_id` and `activity[<YYYY-MM-DD>]`
  counters used by `/status`. Gitignored.
- `obsidian_helper.log` — append-only log. Gitignored.
- The vault on disk is the database; nothing else is persisted.
