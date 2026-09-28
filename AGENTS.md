# obsidian_helper

<!-- vault:shared:begin -- generated from `99 Templates/Project AGENTS.md`; edits here are overwritten by `vault-project sync` -->
## Project memory

This repo's working memory lives at the repo root, **symlinked from the second-brain vault**
(canonical copy in `~/Projects/Nordorn/07 Projects/<name>/`). Edit through the symlink —
never fork a copy into the repo. A forked copy is invisible to the vault, and the wiki
cannot reason about what it cannot see. The repo gitignores these paths for that reason.

- `Notes.md`     — current truth: overview, architecture, subsystem map.
- `LOG.md`       — append-only session log, newest first.
- `Todo.md`      — the build order. Keep it honest each session.
- `PLAN.md`      — active plan for in-flight work (absent when nothing's in flight).
- `DECISIONS.md` — load-bearing decisions; each carries the constraint it implies.

## The vault is reachable from this repo

`.vault/` is a symlink to the second-brain vault root (gitignored — local only). Every vault
path in this file resolves through it, so read them directly rather than guessing:

- `.vault/04 Indexes/Working with Claude.md` — how the vault operates. **Read this before
  writing anything into the vault.**
- `.vault/CLAUDE.md` — the vault's binding contract (raw layers are read-only; Base Notes are
  proposed, never silently created).
- `.vault/00 Vault Guide.md` — folder purposes, capture pipeline, note types.
- `.vault/08 Trackers/Current WIP.md` — the board. What the user is actually meant to do next.
- `.vault/04 Indexes/Wiki Index.md` — catalog of what the wiki already knows. Check here
  before concluding something is a new idea.
- `.vault/05 Base Notes/` — synthesised knowledge. Link to a Base Note rather than restating it.

The vault's rules bind you when you write into it: don't touch `01 Fleeting Notes/`,
`02 Sources/` or `06 Daily Notes/` (raw, read-only), and *propose* Base Notes rather than
creating them silently.

## Session start — read before acting

1. Read `Notes.md`, the top 2–3 `LOG.md` entries, and `PLAN.md` if present.
2. Skim `DECISIONS.md` — the **Constraint** lines are the do/don't rules not to violate.
3. The latest `## Open / next` in `LOG.md` is the starting point.
4. Check for divergence before building on top: unpushed commits, un-pulled remote changes,
   uncommitted files from a prior session.

Don't re-derive what's already written. If `PLAN.md` and the ask disagree, confirm first.

## Session end / before auto-compact — write before stopping

1. Prepend or refresh a dated `LOG.md` entry (What changed / Decisions / Learnings / Open-next),
   written for a reader who wasn't there. Do this *before* compaction, not only at session end.
2. Update `Notes.md` if state or architecture changed (propose the diff; the user owns it),
   and check off / add to `Todo.md`.
3. A load-bearing decision → `### D-NNN` in `DECISIONS.md`, with its **Referent** and
   **Constraint** lines. Supersede, never delete.
4. A learning useful on a *different* project → say so and offer to promote it to the vault.
   Project work that traps generalisable knowledge in the project folder is a leak.
5. If this session changed the project's *status* — a kill, a falsification, a shipped premise —
   update `08 Trackers/Current WIP.md` in the vault in the same pass. The log is history;
   the board is the instruction. A finding that doesn't reach the board doesn't exist.
6. Commit (one line, no `Co-Authored-By` trailer) and push if a remote exists.

These memory files are not code — touching them never requires touching the codebase.
Full convention: `04 Indexes/Working with Claude.md` in the vault.

## This region is generated — don't edit it here

Everything between the `vault:shared` markers is rendered from `99 Templates/Project AGENTS.md`
in the vault and is overwritten by `scripts/vault-project sync`. To change what *every* project
tells its agent, edit the template. Project-specific instructions go **below the closing
marker**, where they survive syncs.

`AGENTS.md` is the canonical file. `CLAUDE.md` is a symlink to it so Claude Code, Codex,
Cursor, Gemini CLI and friends all read the same instructions.
<!-- vault:shared:end -->

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
