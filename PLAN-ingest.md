# Plan — Playlists, Instagram reels, backfill, source entities

Status: proposed, not started. Written 2026-07-12.
Supersedes nothing; `PLAN.md` is the original 2025 build plan and is done.

## Core insight

These are not four features. They are **one crawl engine with pluggable
enumerators**.

The channel crawl already has the whole spine: enumerate → topic-filter →
dedupe against vault → scrape → summarise → write note → resumable state.
Playlists, Instagram saved posts, Instagram likes and YouTube Liked/Watch-Later
are all just *different ways to produce a list of `(id, url, title)` stubs*.
Feed them into the same loop and the four asks collapse into one
generalisation plus three small enumerators.

Build order: fix the engine, generalise its input, add enumerators, bolt the
model-choice UI onto its loop.

---

## Phase 0 — Unbreak the tree (BLOCKING)

`python main.py` currently dies on import. `processor.py` imports three modules
that exist in neither the working tree nor git — the last commit landed the
*callers* of the channel-crawl feature but not the feature.

| File | Must expose |
|---|---|
| `src/scrapers/channel.py` | `VideoStub(video_id, url, title, upload_date)`; `ChannelEnumerator.list_videos(url, date_from, date_to)` — yt-dlp flat extraction, drops Shorts |
| `src/crawl_state.py` | `CrawlRunState(processed_ids, filtered_out_ids, created_titles, skipped_titles, errors)`; `CrawlStateStore.load/save/clear/make_key` over `data/crawl_state.json` |
| `src/agents/topic_filter.py` | `TopicFilterAgent(BaseAgent)` with `filter_titles(titles, topic) -> list[int]` and `is_relevant(title, excerpt, topic) -> bool` |

Not a design task — `processor.run_channel_crawl`, `main.run_crawl`,
`vault_writer.write_channel_index` and the `channel_crawl` block in
`config.yaml` already pin the exact contract. Write to it.

Known ceiling to comment in code: yt-dlp flat extraction does not reliably
return `upload_date` for channel entries, so `from:`/`to:` filtering degrades to
"keep it if we cannot date it" unless we pay for a per-video metadata fetch.

Check: one pytest for `CrawlStateStore` round-trip + resume, and one for
`TopicFilterAgent` response parsing with a stubbed `_call`.

## Phase 1 — Playlists, including private

To yt-dlp, a playlist URL *is* a channel URL. `ChannelEnumerator` handles
`list=` URLs unchanged, so `/crawl <playlist-url>` works with no new command and
no new class. The index note takes its title from the playlist name.

**Private/unlisted playlists need no new auth.** `youtube.cookies_file` is
already passed to `ChannelEnumerator` in `Processor.__init__`, and yt-dlp with
your cookies *is you* — it sees what you see.

The only real work is the failure mode: empty enumeration on a `list=` URL is
almost always **stale cookies**, not an empty playlist. That case gets its own
message ("playlist empty or cookies expired — re-export `youtube_cookies.txt`")
because it will recur every few weeks as the cookie rotates.

Same mechanism yields Liked Videos (`?list=LL`) and Watch Later (`?list=WL`)
free in Phase 4 — they are just private playlists.

## Phase 2 — Model choice: one manifest with cycling overrides

The only genuinely new machinery, because Telegram buttons mean the worker must
wait for a human mid-loop.

### UI

One message, one keyboard, editable in place:

```
Playlist: "Kitchen Renovation" — 12 videos
Default model: [Gemini ▾]          ← tap to cycle gemini/groq/claude

 1. How to plan a galley kitchen        [default]
 2. Cheap worktops compared             [Claude]    ← tapped twice
 3. Vlog: my week                       [skip]      ← tapped three times
 4. Tiling a splashback                 [default]
 ...
              [ Start ]   [ Cancel ]
```

Each video is one button; tapping cycles
`default → gemini → groq → claude → skip → default`. Touch only the two or
three that matter, hit **Start**, the rest run on the default.

This is *less* code than a popup-per-video: one keyboard, one callback handler
mutating an in-memory `{video_id: choice}` dict, one `editMessageReplyMarkup`
per tap, and **one** blocking wait that ends on Start — rather than N waits each
needing its own timeout.

Constraints, both already satisfied: Telegram caps inline keyboards at 100
buttons, and `channel_crawl.max_videos` is 25. That cap now doubles as the
manifest size limit. The topic filter runs *before* the manifest, so junk never
reaches the screen — and the manifest doubles as a sanity check on the filter.

Timeout (cron run, phone in a pocket) → proceed on the default model.

### Telegram layer (`src/telegram_queue.py`)

- `send_buttons(chat_id, text, keyboard)` — inline keyboard
- `edit_message_reply_markup(...)` — redraw after each tap
- `answer_callback_query(id)` — required, or the client spins
- handle `callback_query` updates in `get_pending_messages` (currently dropped
  on the floor entirely)
- `wait_for_callback(chat_id, message_id, timeout)` — polls `getUpdates` inline
  and **buffers non-callback messages** so a button tap does not swallow other
  messages you sent. Needed once per crawl, not once per video.

### Model switching

`AIConfig` already carries all three providers' keys and models. Add
`key_for(provider)` / `model_for(provider)`, plus a cached
`Processor._summarizer_for(provider)`. No new config, no factory.

Ceiling: the wait is a **blocking in-process wait**, valid because this is a
single user running one crawl at a time. Process dies mid-crawl → resume
re-asks. A persistent pending-decision store would fix that and is not worth it
yet.

## Phase 3 — Instagram reels, with real video understanding

New file `src/scrapers/instagram.py`. `can_handle` on `/reel/`, `/reels/`,
`/p/`. Registered in `Processor.scrapers`, so **forwarding a reel link to the
bot works immediately** through the existing URL pipeline.

Captions alone make thin notes, so the scraper reads the actual video.

**No Whisper. No frame extraction. No new dependency.** Gemini takes video
directly, `google-genai` is already installed, and Gemini is already the default
provider. One call does speech transcription *and* visual understanding on the
same file.

Inside `scrape()`:

1. yt-dlp (already a dep) downloads the reel mp4 to a temp file, using
   `instagram.cookies_file`.
2. Upload to the Gemini Files API; poll until `ACTIVE` (seconds, for a reel).
3. One prompt: *"Transcribe all speech verbatim, then describe what is shown on
   screen — on-screen text, locations, dishes, rooms, products."* →
   `{transcript, visual}`.
4. Delete temp file and uploaded file. `ScrapedContent.content` = transcript +
   visual description; `content_type="instagram"`.

Downstream — summarizer, linker, tag resolution, note generator — runs
**completely unchanged**. It just sees richer content. That is the entire reason
this lives in the scraper and not as a new pipeline stage.

Decisions this pins down:

- **Video understanding is Gemini-only, even if the summarizer is not.**
  Anthropic will not take raw video (frames would have to be extracted); Groq is
  audio-only. So the reel scraper reads `ai.gemini_api_key` directly rather than
  `ai.api_key`. Gemini unset + another provider chosen → degrade to
  caption-only with a warning, never crash.
- **`max_duration_seconds` skip**, so a stray 20-minute IG video does not
  quietly become a 400k-token call.
- **Same agent later fixes YouTube Shorts** (they usually have no transcript,
  which is why their notes are thin today). Natural fallback when
  `_get_transcript()` returns empty. Structure for it; do not build it now.

New config block: `instagram.cookies_file`, `instagram.video_model`,
`instagram.max_duration_seconds`. New output folder:
`output_folders.instagram: "02 Sources/Reels"`.

## Phase 4 — Backfill and the interest filter

### Generalise the engine

Rename `Processor.run_channel_crawl` → `run_crawl(stubs, ...)`, taking a
pre-built stub list. `_process_channel_crawl` builds stubs from
`ChannelEnumerator`; backfill builds them from an export file. **One engine, one
state store, one progress bar, one dedupe path.**

### Enumerators

- **Instagram export** — request "Download your information" from Instagram, drop
  the JSON in a folder; a ~20-line reader pulls hrefs out of `saved_posts.json`
  and `liked_posts.json`. No login, no new dependency, no ban risk. Manual
  re-export for future backfills.
- **YouTube Liked / Watch Later** — zero new code: playlist URLs `?list=LL` and
  `?list=WL` with cookies (see Phase 1).

### Interests

```yaml
interests:
  include: ["food & restaurants", "apartments & interiors", "life advice", "coding & software"]
  exclude: ["personal / friends", "comedy & memes", "random funny clips"]
```

**Not a new agent.** This renders to a topic string and goes through the
existing `TopicFilterAgent`. Backfill defaults `topic` to it when none is passed.

### The ordering that matters

Instagram's export gives a URL and a timestamp — **no caption**. So unlike
YouTube there is no free title-level pre-filter; each post needs a cheap
metadata-only scrape before the filter can judge it.

Order is therefore fixed and non-negotiable:

> **metadata → interest filter → (survivors only) download + Gemini video call**

Never transcribe before filtering. A 60s reel is ~18k video tokens; a thousand
liked posts transcribed blind is ~18M tokens for content that is mostly memes
you asked to exclude.

Instagram also rate-limits hard. Mitigation is the existing `sleep_seconds`, a
`max_items` cap, and the resumable state store — likes get backfilled in chunks
across several runs, not one heroic pass that gets throttled. With thousands of
likes, expect days of background runs. The filter is what earns its keep here.

## Phase 5 — Books and magazines as entity notes

Independent of everything above; can land first if a quick win is wanted.

Extend the summarizer's existing JSON contract with one field:

```json
"mentioned_sources": [{"title": "...", "type": "book|magazine|paper|podcast", "author": "..."}]
```

It already returns structured JSON (tags, key concepts, review questions), so
this is one field and one prompt line.

Then `VaultWriter.ensure_source_note(title, type, author)`:

- creates `02 Sources/Books/<Title>.md` if absent
- appends `- [[<the note that mentioned it>]]` under a `## Referenced by` section
- the source note gets a `Mentioned sources: [[X]]` line back

Each book then accumulates everything that referenced it. Reuses
`NoteLinkEngine._build_index` for lookup and `_make_wikilink` for formatting.

New config: `output_folders.books: "02 Sources/Books"`.

Ceiling: exact title matching. *Sapiens* and *Sapiens: A Brief History* make two
notes until fuzzy matching earns itself.

---

## Sequencing

- **Phase 0 blocks everything.**
- Phases 1 and 3 are cheap and independent.
- Phase 2 is the bulk of the effort.
- Phase 4 depends on 0 + 3.
- Phase 5 is fully independent.

## Deliberately skipped

| Skipped | Add when |
|---|---|
| A `/playlist` command | Never — `/crawl` covers it |
| A `/discuss` command | Never — Phase 5 backlinks cover it |
| Whisper / faster-whisper | Gemini video is unavailable or too costly |
| Frame extraction for Anthropic | You switch off Gemini and still want reels |
| Live Instagram login (instaloader) | The DYI export cadence becomes intolerable |
| Persistent popup-decision store | Crawls start dying mid-run often enough to hurt |
| Video understanding as Shorts fallback | Shorts notes stay thin and it annoys you |
| Fuzzy title match for book entities | Duplicate book notes actually accumulate |
