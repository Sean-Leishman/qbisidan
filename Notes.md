# Notes

Running log of decisions. Newest at the bottom.

### 2026-07-13 — Ingest expansion: playlists, model manifest, Instagram reels

Plan lives in `PLAN-ingest.md` (6 phases). Phases 0–3 shipped this session.

**The repo did not import.** `processor.py`, `main.py`, `config.yaml` and
`scrapers/__init__.py` all referenced a channel-crawl feature whose three core
modules were never committed — `scrapers/channel.py`, `crawl_state.py`,
`agents/topic_filter.py`. The last commit landed the *callers* but not the
feature. Rebuilt all three to the contract the existing callers already pinned.

**Core design call: one crawl engine, many enumerators.** Playlists, Instagram
saved posts, Instagram likes and YouTube Liked/Watch-Later are not four
features — they are four ways to produce a list of `(id, url, title)` stubs fed
to the crawl loop that already exists (enumerate → topic-filter → dedupe against
vault → scrape → summarise → write → resumable state). This collapsed the whole
ask into one generalisation plus small enumerators.

**Private playlists needed no new auth.** yt-dlp with the user's cookies *is*
the user. Liked (`?list=LL`) and Watch Later (`?list=WL`) are therefore free —
they are just private playlists. The only real work was the failure mode: an
empty `list=` enumeration means stale cookies, not an empty playlist, so it
raises `StaleCookiesError` rather than reporting "no videos found".

**Model choice: one manifest, not N popups.** Rejected a per-video popup (a
40-video playlist = 40 popups, 40 waits, 40 timeouts). Instead: one editable
Telegram message, one button per video, tapping cycles
`default → gemini → groq → claude → skip`. Pick a default once, override the
few that matter, hit Start. Strictly *less* code than the popup design — one
keyboard, one blocking wait. The topic filter runs *before* the manifest, so
junk never reaches the screen and the manifest doubles as a sanity check on the
filter.

**Reels: Gemini video, not Whisper.** Gemini takes video files directly and
`google-genai` was already a dependency, so one call does speech transcription
*and* on-screen visual description. No Whisper, no ffmpeg, no new dep. This
lives in the *scraper*, not a new pipeline stage — so summarizer, linker, tag
resolution and the note generator all run unchanged, just on richer content.
Video understanding is pinned to Gemini even when the summarizer provider is
Groq or Anthropic (Anthropic won't take raw video; Groq is audio-only), so the
scraper reads `ai.gemini_api_key` directly.

**Two bugs caught in review that would only have shown up in production:**

1. `TopicFilterAgent.filter_titles` treated a valid `[]` from the model ("none
   of these match your topic") identically to an unparseable response, and both
   fell back to keep-all. The filter working *perfectly* would have summarised
   every video it correctly rejected. Fail-open is right for *parse failure*,
   wrong for a decisive empty answer.
2. The manifest's message buffer was in-memory while `last_update_id` was
   persisted past it immediately. In cron/run-once mode the process exits when
   the crawl ends, so any message sent while the manifest was open was lost
   **permanently** — Telegram never resends past an advanced offset. Buffer now
   persists to the same state file as the offset, so the two move together.

**Cost constraint that shapes Phase 4.** Video is ~300 tokens/second: a 60s reel
is ~18k tokens. Fine for one; ruinous for a blind backfill of thousands. So the
Instagram scraper exposes a cheap `get_metadata()` (caption/uploader, no
download) separately from the expensive `scrape()`. Backfill order is fixed and
non-negotiable: **metadata → interest filter → (survivors only) download +
Gemini**. Never transcribe before filtering.

**Remaining:** Phase 4 (backfill + interest filter; Instagram "Download your
information" JSON export as the enumerator — no login, no ban risk) and Phase 5
(books/magazines as entity notes with backlinks). Phase 5 is fully independent.

### 2026-07-13 — Phase 4: generic crawl engine, Instagram export backfill, interest filter

`Processor.run_channel_crawl`'s body is now `Processor.run_crawl(stubs, state_key,
display_name, topic=None, chat_id=None, progress_callback=None, resume=True,
date_from=None, date_to=None, max_items=None)` — a generic engine that takes a
pre-built stub list and knows nothing about where they came from.
`run_channel_crawl` kept its exact old signature and became a ~20-line wrapper
(enumerate via `ChannelEnumerator` → call `run_crawl`) so `main.py --crawl` and
Telegram's `/crawl` didn't need to change at all. `Processor.run_backfill(source,
...)` is the second caller — `source` is `"instagram:saved"`, `"instagram:likes"`,
or any playlist URL (YouTube Liked = `?list=LL` already worked via Phase 1).
One engine, one `CrawlStateStore`, one dedupe path, one manifest, as planned —
no parallel copy of the loop.

**The ordering fix, precisely.** The engine now asks each scraper "do you have
a `get_metadata()`?" (a plain `hasattr` check — `InstagramScraper` has one,
`YouTubeScraper` doesn't). If yes and a topic is set: fetch metadata, run the
interest filter on *that*, and only call the real `scrape()` (download + Gemini)
for survivors — a rejection never reaches the expensive path. If no cheap path
exists (YouTube), behaviour is byte-for-byte what it was before this phase:
scrape first, filter the transcript excerpt after. Same `hasattr` check also
gates the bulk title-pre-filter stage, which only makes sense when stub titles
carry real signal — Instagram's export gives no caption at enumeration time, so
that stage is skipped for it and the per-item cheap-metadata filter does the
equivalent job. The regression test that matters here (`test_run_crawl.py`)
asserts `scraper.scrape_calls == []` when the cheap filter rejects an item —
that's the assertion protecting a potential 18M-token blind-transcription bill.

**Interests config is not a new agent.** `InterestsConfig.render()` turns
`include`/`exclude` lists into the same free-text topic string
`TopicFilterAgent` already accepted; `run_backfill` defaults `topic` to it when
the caller doesn't pass one explicitly.

**Instagram export reader tolerates version drift by shape, not by key name.**
Rather than hard-code `saved_saved_media` / `likes_media_likes` (both have
already changed across Instagram's export versions), `instagram_export.py`
walks the whole JSON tree for any dict carrying `string_map_data` (saved) or
`string_list_data` (likes) — that inner shape has stayed stable. Each record is
parsed in its own `try/except`; one malformed entry is skipped, not fatal.

**Rate limiting / no silent truncation.** Backfill reuses
`channel_crawl.sleep_seconds` (one pace setting, not a forked one) and adds
`backfill.max_items` (default 50) as its own cap, since a backfill and a
channel crawl have very different natural batch sizes. When the cap trims the
stub list, the engine now logs a warning *and* appends a line to the final
summary message ("Capped at N items — M more remain; resume to continue") —
this now applies to `/crawl` too, previously silent there, which is a
by-product improvement rather than scope creep since it's the same code path.

**One behavioural nuance worth flagging:** the crawl index note's filename and
the manifest/summary display name are now uniformly `channel_enumerator.last_title
or <url-parsed slug>` (previously the index filename used the raw URL slug and
only the manifest used the nicer title). Nothing in the test suite pinned the
old filename, and it's a straightforwardly nicer name, but flagging it since
the plan didn't call this distinction out explicitly.

Phase 5 (books/magazines as entity notes) is the only thing left from
`PLAN-ingest.md` and remains fully independent of everything above.
