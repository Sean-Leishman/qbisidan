# Notes

Loose observations from reading the codebase, plus what's currently in
flight.

## In-flight (working tree, uncommitted)

The repo only has a single `Initial commit` so far. Everything below is
either modified or untracked in the working tree, so the project is
mid-build:

- **New command surface.** `src/commands.py` (untracked) is a pure parser
  that turns raw Telegram text into `ParsedCommand` with a typed
  `command_type`. The processor used to handle URLs only; now it handles
  `/help`, `/search`, `/status`, `/todo`, `/someday`, `/read`, `/watch`,
  `/note`, `/daily [task]`, `/project … [todo]`, `/event`, replies-as-
  questions, and a plain-text fallback to Inbox. `tests/test_commands.py`
  covers it.
- **Vault writer.** `src/agents/vault_writer.py` (untracked) is the new
  home for every non-bookmark write — trackers, daily notes, fleeting
  notes, projects, events, ICS files, and the "reply to bot → append
  question" flow.
- **AI sub-agent base class.** `src/agents/base.py` consolidates the
  Groq/Gemini/Anthropic dispatch that used to live inside `Summarizer`.
  `Summarizer` now extends `BaseAgent`. There's also a new
  `QuestionAgent` for YouTube review questions.
- **Summariser rewrite.** `src/summarizer.py` prompts are rewritten to
  produce section-by-section breakdowns with timestamp ranges (YouTube)
  or inferred section headings (articles), plus a structured JSON tail
  for category/tags/action items/key concepts. `SummaryResult` gained a
  `content_truncated` flag that surfaces a `> [!warning]` callout in the
  generated note when the source had to be cut to fit context.
  `ai.max_tokens` jumped from 1024 → 8192 to support this.
- **Note linking.** `src/linker.py` gained `find_note_by_url` (duplicate
  detection — bot replies "Already in vault: …" instead of re-creating)
  and `resolve_tags` (plain-text terms → real wikilinks against
  `03 Tags/` and `04 Indexes/`, creating `03 Tags/<Term>.md` if nothing
  matches). `ObsidianNoteGenerator` now consumes pre-resolved tag
  wikilinks rather than guessing index paths itself.
- **Search.** `src/search.py` (untracked) implements `/search <query>`
  with title-weighted scoring and tracker/daily-note deprioritisation.
- **Telegram queue.** `TelegramMessage` was reshaped from "URL + notes"
  to "raw text + optional reply_to_text", with the URL parsing pushed
  down into `commands.py`. State file gained an `activity` map of per-
  day counters that powers `/status`.
- **Config.** New `vault_writer` block (`daily_notes_folder`,
  `ics_folder`) wired through `src/config.py`.
- **Tooling.** `pyproject.toml` adds a dev dep group with pytest and a
  `[tool.pytest.ini_options]` block (`testpaths = ["tests"]`,
  `addopts = "-q"`). All test files were rewritten alongside the source
  changes (`test_obsidian.py`, `test_summarizer.py`, `test_linker.py`,
  `test_telegram_queue.py`, plus the new `test_commands.py`,
  `test_search.py`).

The README has been rewritten as part of this work too — it documents
the full command surface, not just URL bookmarking, and is currently
untracked.

## Gotchas / quirks

- `templates/` exists but is empty. All markdown templates are inlined
  in `src/obsidian.py` and `src/agents/vault_writer.py`. If you go
  looking for a template file, you won't find one.
- Vault folder names are numbered (`01 Fleeting Notes`, `02 Sources`, …)
  and hard-coded in several places (`vault_writer.py`, `linker.py`,
  `search.py`). Renaming a folder in the vault means a code change.
- `linker.py` walks the entire vault on every URL bookmark for
  duplicate detection (`find_note_by_url`) and again for tag
  resolution. Fine for small vaults, will get slow once there are
  thousands of notes.
- `data/telegram_state.json` is gitignored, so the per-day `/status`
  counters reset whenever you wipe state. They're not derived from the
  vault.
- Only one chat is ever supported in practice — `allowed_chat_ids` is a
  list, but everything past that assumes a single user.
- Provider switching is by string. Adding a new AI provider means
  adding `_init_<name>` and `_call_<name>` to `BaseAgent`.

## Possible follow-ups (not started)

- Semantic search via embeddings (the README mentions Smart Connections
  as a possible source).
- Background indexing for `linker.py` instead of an O(vault) scan per
  bookmark.
- Webhook mode (Cloudflare Worker → push) instead of polling. PLAN.md
  notes this as out-of-scope but feasible.
- A real templates/ directory if the inlined markdown ever needs to
  diverge per user.

## Things that are unclear

- The `examples/example_output.py` file hasn't been touched in two
  months — it may be stale relative to the current `ObsidianNoteGenerator`
  output.
- `PLAN.md` is the original design doc and is now mostly out of date
  (it predates `commands.py`, `vault_writer`, `/search`, `/status`,
  the agent base class, etc.). Useful as historical context, not
  current spec.
