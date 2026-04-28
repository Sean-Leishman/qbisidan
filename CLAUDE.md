# obsidian_helper

## Purpose

Telegram bot that bridges a phone to an Obsidian vault. The user sends URLs,
todos, fleeting notes, daily-note entries, project notes, and calendar events
to a personal Telegram bot; a Python worker polls the bot, parses each message
into a typed command, and writes the result to the right place in the vault
(tracker files, daily note, project folder, ICS file, or a fully AI-summarised
source note). Runs from cron or in long-poll watch mode.

## Tech stack

- Python 3.11+, managed with `uv`.
- AI providers (pluggable): Gemini (default), Anthropic, Groq — see
  `src/agents/base.py`.
- Scraping: `trafilatura` for articles, `yt-dlp` +
  `youtube-transcript-api` for YouTube. Telegram via raw `requests`.

## Key files / entry points

- `main.py` — CLI entry; modes are `run-once` (cron), `--watch`, `--test URL`.
- `config.yaml` — vault path, Telegram chat IDs, AI provider, routing
  categories, vault_writer paths.
- `src/config.py` — loads/validates config, expands `${ENV}` API keys.
- `src/processor.py` — orchestrator. Maps `ParsedCommand.command_type` to
  `_process_*` handlers via `_HANDLERS`.
- `src/commands.py` — pure parser: raw Telegram text → `ParsedCommand`. Handles
  `@date` syntax, `/project X todo …`, replies-as-questions, etc.
- `src/telegram_queue.py` — long-polling, allowed-chat filtering,
  `data/telegram_state.json` (last `update_id` + per-day activity counters).
- `src/summarizer.py` — structured summary + JSON metadata (category, tags,
  action items, key concepts, review questions, `content_truncated`).
- `src/agents/vault_writer.py` — every non-bookmark write (trackers, daily
  notes, fleeting notes, projects, events, replying to bot → questions block).
- `src/agents/question_agent.py` — Socratic review questions for YouTube notes.
- `src/linker.py` — `NoteLinkEngine`: vault wikilinks, related-note lookup,
  URL-based duplicate detection, tag resolution against `03 Tags/` /
  `04 Indexes/`.
- `src/obsidian.py` — markdown generator. Consumes pre-resolved tag wikilinks.
- `src/search.py` — keyword vault search (`/search`), title-weighted scoring.
- `src/scrapers/{youtube,webpage}.py` — extractors behind `can_handle`/`scrape`.
- `tests/` — pytest suite mirrors src modules.

## How to run / dev

```bash
uv sync
export TELEGRAM_BOT_TOKEN=... GEMINI_API_KEY=...   # see README for alternatives
python main.py                  # one shot (cron-friendly)
python main.py --watch          # poll every 60s
python main.py --test <URL>     # bypass Telegram, exercise the bookmark pipeline
uv run pytest                   # tests (pytest config lives in pyproject.toml)
```

Cron line is in README. Logs go to `obsidian_helper.log` and stdout.

## Conventions noticed

- Command parsing is pure (no I/O) and lives in `src/commands.py`; the
  processor only routes.
- New AI sub-agents extend `BaseAgent` (`src/agents/base.py`) for provider
  dispatch — don't reimplement Groq/Gemini/Anthropic clients elsewhere.
- All vault writes go through `VaultWriter` or `ObsidianNoteGenerator`. Direct
  `Path.write_text` against the vault from a handler is a smell.
- Tags are always vault-relative wikilinks resolved by `NoteLinkEngine` —
  never bare `#hashtags`.
- Folder layout in the vault is numbered (`01 Fleeting Notes`,
  `02 Sources`, …) and hard-coded into vault writers; renaming requires
  updating both code and the vault.

## Gaps / honest notes

- Only one commit in git history (`Initial commit`); the working tree is the
  current source of truth.
- No semantic / vector search — only keyword.
- `templates/` directory exists but is empty (templates are inlined).
- `.env` is committed-ignored but `config.yaml` is not — secrets must stay in
  env vars.
- Single user by design (`allowed_chat_ids`); no multi-tenant story.
