# Setup

The README has the full user-facing walkthrough. This file is the short
version plus the things that bit me that the README doesn't surface.

## Prerequisites

- Python 3.11+ (`.python-version` pins this).
- `uv` for dependency management (`uv.lock` is checked in).
- A Telegram bot (created via `@BotFather`) and your own chat ID.
- An API key for at least one of: Gemini (default), Groq, Anthropic.
- A local Obsidian vault path. Currently `config.yaml` points to a
  Linux vault path (WSL OneDrive mount until 2026-09-28):
  `/home/seanleishman/Projects/Nordorn`.

## Environment

Secrets live in env vars (the `.env` file is gitignored). `config.yaml`
references them via `${VAR}` placeholders that `Config.load` expands.

```bash
export TELEGRAM_BOT_TOKEN="…"
export GEMINI_API_KEY="…"        # default provider
# export GROQ_API_KEY="…"
# export ANTHROPIC_API_KEY="…"
```

## Config

`config.yaml` keys that actually matter:

- `vault_path` — absolute path to the Obsidian vault.
- `telegram.allowed_chat_ids` — list of Telegram chat IDs allowed to use
  the bot. Anything else is silently dropped by `TelegramQueue`.
- `ai.provider` — `gemini` | `groq` | `anthropic`. The provider's
  `*_api_key` and `*_model` keys must also be set.
- `ai.max_tokens` — currently 8192 (was 1024; bumped to give the
  section-by-section summariser room).
- `routing_categories` — list of `{name, folder}` pairs. The summariser is
  told the names; `Config.get_folder_for_category` maps the chosen name
  back to a folder under `02 Sources/Articles/`.
- `vault_writer.daily_notes_folder` — defaults to `06 Daily Notes`.
- `vault_writer.ics_folder` — leave blank if you don't want `.ics` files
  written; otherwise point at whatever your calendar app watches.

## Install and run

```bash
cd /home/seanleishman/Projects/obsidian_helper
uv sync
python main.py                   # one-shot (cron mode)
python main.py --watch           # poll every 60s
python main.py --watch --interval 30
python main.py --test https://…  # exercise the full URL pipeline once
python main.py -v                # DEBUG logging
```

Logs go to `obsidian_helper.log` and stdout. State (last update ID,
per-day counters) lives in `data/telegram_state.json`.

## Tests

```bash
uv run pytest                    # config in pyproject.toml ([tool.pytest.ini_options])
uv run pytest tests/test_commands.py -v
```

Tests don't hit the Telegram or AI APIs — they mock at the boundary.
There's no integration suite that talks to a real vault.

## Cron

The README shows a 5-minute cron. The script is idempotent against
already-processed Telegram updates because `TelegramQueue` persists
`last_update_id`, so missed runs catch up automatically the next time.

## Things to check first when something breaks

- `obsidian_helper.log` — every handler logs its own errors with
  `logger.exception`, so the traceback is in there.
- `data/telegram_state.json` — if you want to re-process the last
  message, delete it (you'll re-process **everything** in Telegram's
  retention window, so be careful).
- `config.yaml` env-var expansion: if a key looks literally like
  `${ANTHROPIC_API_KEY}` at runtime, the env var wasn't exported in the
  shell that launched the worker (cron is the usual culprit).
- The vault path must exist and be writable. The bot doesn't create the
  top-level vault, only sub-folders inside it.
