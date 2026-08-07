# SAVES — Smart Archival & Vault Entry System

## What This Is

SAVES is a personal content archiving pipeline. It watches a single Obsidian file
(`0 - INBOX/SAVES/SAVES.md`) for URLs, extracts content from social/web platforms, downloads media
to NAS, transcribes audio via remote Whisper, reads on-screen text via Claude vision,
sends the result to a Discord bot for approval, then writes a structured Obsidian note
to a Synology NAS vault.

**Owner:** Bora (Oodaloop80)
**Runtime:** Docker on Synology NAS (python:3.11-slim)
**Dev machine:** Windows workstation. Repo at `C:\DEV\Apps\SAVES\SAVES_app`
**Git remote:** self-hosted **Forgejo** on the NAS — `https://192.168.1.201:3443/<user>/SAVES.git` (GitHub retired 2026-08-05; see "Git Workflow" and `docs/FORGEJO.md`)
**Workstation IP (Whisper server):** `192.168.1.90`

---

## Hard Constraints — Never Violate

1. **Zero delete calls.** No `os.remove`, `os.unlink`, `shutil.rmtree`, or `Path.unlink`
   anywhere in the codebase. Atomic writes use `tempfile + os.replace()`. Cross-volume
   moves rename source to `.bak`. Orphaned tmp files are left in place on error.
   Verify with: `grep -rn "os.remove\|os.unlink\|shutil.rmtree\|\.unlink(" src/ scripts/`

2. **Single asyncio event loop.** The watchdog thread bridges to it via
   `call_soon_threadsafe`. Never create a second event loop or use
   `asyncio.run()` inside an already-running loop.

3. **Full self-containment — a save must survive the source going away (Bora, 2026-07-29).**
   Everything a note shows MUST be stored locally: the note NEVER links to a remote asset for
   display. Every image/video/icon/attachment is captured locally so the note renders completely
   even after the original site/post/reel/video is deleted. Two mechanisms: (a) block-level media
   (videos, hero/article photos) → the external `media://` store via `download_media()` /
   `localize_article_images()`, embedded with `EmbedRelativeTo` fences (block only — the
   `media://` plugin cannot render inline); (b) small assets that must render INLINE next to text
   (e.g. per-ingredient icons) → **base64 data URIs embedded directly in the note** via
   `prepare_ingredient_icon_data_uris()` (`![|24](data:image/webp;base64,…)`), so the bytes live
   inside the note — no external file, no vault folder. If a new feature surfaces any source
   asset, add a local-capture step for it — no remote `![](http…)` / `<img src="http…">` in a
   written note, ever.

---

## Repository Structure

```
SAVES/
├── src/
│   ├── main.py                    # Entry point — starts watcher, processor, Discord bot
│   ├── config.py                  # yaml.safe_load; get_config() returns cached dict
│   ├── credentials.py             # Loads .env, validates required keys from config
│   ├── processor.py               # Core pipeline: extract→download→transcribe→AI→Discord
│   ├── watcher.py                 # Watchdog Observer, 3s debounce, bridges to asyncio queue
│   ├── queue_manager.py           # ProcessingState (JSON); QueueManager: enqueue_from_file(),
│   │                              # persistent serial queue (queue_state.json: waiting/active/
│   │                              # counters) + approval gate + skip/resolve for one-at-a-time
│   ├── extractors/
│   │   ├── base.py                # ExtractedContent dataclass, BaseExtractor ABC
│   │   ├── __init__.py            # get_extractor(url, config) — routes to correct extractor
│   │   ├── reddit.py              # Reddit JSON API (no credentials needed — public API)
│   │   ├── youtube.py             # yt-dlp --write-info-json --write-auto-sub
│   │   ├── instagram.py           # yt-dlp + gallery-dl; cookie support
│   │   ├── tiktok.py              # yt-dlp --write-info-json; cookie support
│   │   ├── facebook.py            # yt-dlp + cookies; detects embedded articles
│   │   └── generic.py             # Playwright → trafilatura Markdown (headings/links/images);
│   │                              # lazy-image resolve, <picture>/discard-class fix, feature
│   │                              # image, markdown normalize; readability-lxml fallback.
│   │                              # AUTH: prefers a persistent profile cookies/<host>_profile/
│   │                              # (login-gated/Firebase-IndexedDB sites, e.g. provecho.co),
│   │                              # else <host>_session.json, else <host>.txt cookies
│   ├── crawlers/                  # /crawl: one index/creator page → many content URLs
│   │   ├── base.py                # SiteCrawler ABC: shared partition() + enqueue_discovered()
│   │   ├── __init__.py            # get_crawler(url, config) — routes to a site crawler or None
│   │   └── provecho.py            # ProvechoCrawler — ONE creator's recipes (per-creator scoped,
│   │                              # scrolls the SPA grid; needs provecho.co_profile auth)
│   ├── media/
│   │   ├── downloader.py          # download_media() → {media_root}/{platform}/{author}/{slug}/
│   │   │                          # abs_to_obsidian_embed() returns BARE relative path (no ![[]])
│   │   │                          # localize_article_images() downloads inline article images
│   │   ├── transcriber.py         # mode=remote: POST to 192.168.1.90:5000; mode=local: faster-whisper
│   │   └── vision.py              # Images → base64; videos → scene-change frames → 2×2 montage
│   ├── ai/
│   │   ├── prompts.py             # SYSTEM_PROMPT, build_user_prompt(), fact-check/travel/NL-edit prompts
│   │   ├── claude_client.py       # analyze_content(), fact_check(), nl_edit()
│   │   └── verifier.py            # check_travel_location() — called only when travel in topics
│   ├── discord_bot/
│   │   ├── approval.py            # PendingApproval dataclass, PendingApprovalsStore (JSON)
│   │   ├── notifications.py       # send_approval_request(), send_log(), send_alert(),
│   │   │                          # duplicate notice + DuplicateNoticeView (🔁 Re-save/✖ Dismiss)
│   │   └── bot.py                 # SAVESBot; ApprovalView (7 buttons: +⏭️ Skip, conditional ⚠️);
│   │                              # TagRemoveView (✖ per tag, ↩ Undo All); _finalize() writes note;
│   │                              # on_message: #SAVES-inbox paste/webhook → enqueue (or crawl)
│   ├── notes/
│   │   ├── formatter.py           # format_note() dispatches to 13 per-type renderers
│   │   └── file_manager.py        # write_note() atomic; move_note() with SHA256 verify
│   └── utils/
│       ├── url_parser.py          # extract_urls(), normalize_url(), detect_platform()
│       ├── file_io.py             # read_inbox(), remove_url_from_inbox() (atomic)
│       ├── preferences.py         # PreferencesStore — learned folder routing per source
│       ├── cookie_checker.py      # Checks instagram/tiktok/facebook cookie file mtimes
│       ├── tag_index.py           # Vault tag index (frontmatter + inline #tags) — /tag add
│       │                          # autocomplete, near-dup check, prompt taxonomy hint
│       └── retry.py               # with_retry() decorator — defined but not yet wired in
├── scripts/
│   ├── process_one.py             # CLI test: run full pipeline for one URL, print note
│   ├── test_connection.py         # Smoke test: Anthropic API, Discord bot, Reddit JSON API
│   ├── whisper_server.py          # Flask server (runs on WORKSTATION, not NAS)
│   ├── refresh_cookies.py         # Instructions for exporting browser cookies
│   ├── capture_session.py         # Login-once persistent profile → cookies/<host>_profile/
│   │                              # (captures IndexedDB Firebase auth that .txt/JSON can't)
│   ├── crawl_creator.py           # CLI: crawl one creator; dry-run list or --to-inbox feed
│   └── preflight_nas.sh           # POSIX pre-deploy check (mounts/secrets/cookies/Whisper) — run on NAS
├── docker/
│   ├── Dockerfile                 # python:3.11-slim + ffmpeg + chromium + playwright
│   └── docker-compose.yml         # 8 volumes: vault, media, cookies, config, logs, state files
├── .dockerignore                  # Trims build context + keeps secrets/state out of image layers
├── config.yaml                    # All configuration — canonical container paths only
├── config.local.yaml.example      # Template for bare-metal dev overrides (gitignored copy)
├── .env.example                   # Template: ANTHROPIC_API_KEY, DISCORD_BOT_TOKEN
└── cookies/                       # instagram.txt, tiktok.txt, facebook.txt (gitignored)
```

---

## Data Flow

```
0 - INBOX/SAVES/SAVES.md ──(watchdog, 3s debounce)──┐
#SAVES-inbox paste / Discord webhook ─────────┤  (bot on_message → enqueue_url;
    (Android/Tasker POST; creator URL → /crawl)│   creator URL → crawl confirm)
                                               ▼
                                          asyncio.Queue
    │  (processor.py — serial, one URL at a time)
    ▼
1. extractor.extract(url)           → ExtractedContent
2. prefs.hint(source_key)           → preferences_hint for Claude
3. download_media()                 → list of absolute paths
4. transcribe()                     → transcript str | None
5. prepare_images_for_claude()      → vision image blocks
6. analyze_content()                → ai_result dict (note_type, folder, tags, etc.)
7. fact_check() + check_travel()    → parallel, non-fatal
8. new_pending() + send_for_approval() → Discord message with approval buttons
   → mark_carded(url): this card becomes the "active" gate holder
   → SERIAL GATE: processor WAITS (does not process the next URL) until this card is
     approved/skipped/forgotten (processing.serial_approval, default on)

[Hours/days later — Discord button click]
    ▼
bot._finalize()  (✅ Approve)
    │
    ├── format_note()               → Markdown string (per-type template)
    ├── write_note()                → Obsidian vault file (atomic)
    ├── prefs.set(source_key, path) → learned preference saved
    ├── state.mark_done(url, path)  → processing_state.json updated
    ├── remove_url_from_inbox()     → URL removed from the inbox file
    └── queue_manager.resolve(url)  → releases the gate → next URL processes now
                                      (analyzed AFTER this approval, so it reuses the
                                       folder pref + tags just set — progressive easing)
```

---

## Key Configuration Values (`config.yaml`)

```yaml
paths:                       # CANONICAL CONTAINER PATHS — identical everywhere, never edit
  vault_root: "/vault"       #   per-machine. Docker maps host dirs onto them via docker/.env
  saves_root: "/vault/SAVES" #   (VAULT_HOST/MEDIA_HOST/STATE_HOST); bare-metal dev overrides
  inbox_file: "/vault/0 - INBOX/SAVES/SAVES.md"   # them in gitignored config.local.yaml.
  media_root: "/media"       # State JSONs live in /app/state/ (one mounted directory).

transcription:
  mode: "remote"                          # POSTs audio to workstation
  remote_url: "http://192.168.1.90:5000/transcribe"
  model: "large-v3-turbo"

vision:
  enabled: true
  max_images: 20                          # Instagram carousel max
  ocr_model: "claude-haiku-4-5"          # Stage 1: Haiku reads images → text; Stage 2: Opus analyzes text-only
  max_video_frames: 8                     # Scene-detected frames before montaging
  frame_scene_threshold: 0.3             # ffmpeg scene-change sensitivity (0–1, lower = more frames)
  frame_grid: 2                          # Tile grid size: 2 = 2×2 montage (4 frames per image block)

ai:
  model: "claude-sonnet-4-6"             # Main analysis model (A/B verdict: Sonnet ≈ Opus at ~½ cost)

fact_checking:
  model: "claude-sonnet-4-6"             # Cheaper model for fact-checking
  include_images: false                  # OCR already captured image content; raw pixels would double-bill
  web_search_topics: ["health", "finance"]  # Only web-search for these; recipes skip even if health triggered

processing:
  serial_approval: true   # ONE approval card at a time — the next URL isn't processed until the
                          # current card is approved/skipped. Persisted to queue_state.json
                          # (survives restarts). Keeps Discord calm on a /crawl/batch and lets
                          # each save reuse the folder+tags just approved. false = old all-at-once.

discord:
  channel_inbox: "SAVES-inbox"     # OPTIONAL paste-to-save channel (+ Android/Tasker webhook).
                                   # Unset to disable. Not in validation's required-channels.
  auto_approve_on_timeout: false   # DECISION (Bora, 2026-07-04): stays false — approvals are
  auto_approve_timeout_hours: 48   # reviewed fresh, never auto'd. See ROADMAP "Decisions locked".

credentials:
  keys: [ANTHROPIC_API_KEY, DISCORD_BOT_TOKEN]   # Reddit needs NO credentials
```

---

## Note Types (13 total — Claude picks one)

| note_type | Platform | Key sections |
|---|---|---|
| `youtube_video` | YouTube | embed, Chapters, Transcript↕, Summary, Takeaways, Metadata |
| `reddit_text` | Reddit | Summary, Takeaways, Original Content (blockquote), Comments, Metadata |
| `reddit_gallery` | Reddit | Image embeds, Summary, Original Content, Comments, Metadata |
| `reddit_video` | Reddit | Video embed, Summary, Original Content, Comments, Metadata |
| `instagram_reel` | Instagram | Video embed, Transcript↕, Caption, Summary, Metadata |
| `instagram_post` | Instagram | Image embeds, Caption, Summary, Metadata |
| `tiktok_video` | TikTok | Video embed, Transcript↕, Caption, Summary, Metadata |
| `facebook_video` | Facebook | Video embed, Transcript↕, Caption, Summary, Metadata |
| `facebook_post` | Facebook | Summary, Original Content, Metadata |
| `web_recipe` | Generic | Media (embedded video), Summary, 📸 Photos, Recipe, Caption, Text from Images, Transcript, Sources & Metadata |
| `web_travel` | Web | Summary, Key Details, Images, Metadata |
| `web_article` | Web | Summary, Takeaways, Article body (Markdown with inline images), Metadata |
| `web_generic` | Web | Summary, Takeaways, Article body (Markdown), Metadata |

All types include YAML frontmatter: title, source_url, platform, saved_date, author, tags, type: save.

**Recipe injection (all platforms):** When Claude extracts recipe fields (`recipe_ingredients`,
`recipe_instructions`, etc.) from *any* note type (not just `web_recipe`), a `## Recipe`
section is automatically injected before the `---` separator. This handles Instagram Reels,
TikTok videos, or Reddit posts that contain recipes.

**Recipe enhancements (in `_recipe_section` / `formatter.py`):**
- **Nutrition label.** Claude estimates per-serving macros/micros into a `nutrition` JSON
  field; `_nutrition_label()` renders an FDA-style HTML Nutrition Facts panel (Obsidian
  renders the HTML). %DV is computed *deterministically* in the formatter from FDA reference
  Daily Values (`_DAILY_VALUES`), not by the model.
  **Source nutrition is a floor, not a replacement.** When the recipe page publishes its own
  per-serving nutrition (schema.org `NutritionInformation`), `recipe_data._parse_nutrition()`
  extracts it, `format_recipe_data_for_prompt()` hands it to Claude as authoritative, and
  `apply_structured_recipe()` then re-asserts those exact numbers over Claude's estimate while
  KEEPING the nutrients the source omitted (omega-3/6, added sugars, potassium, vitamins —
  schema.org carries none of those). The overridden keys are recorded in the private
  `_source_keys`, which flips the disclaimer from "🤖 AI estimate" to "📋 published by the
  source, supplemented with 🤖 AI estimates". Nutrition merges regardless of source language
  (grams are grams), unlike the English-only ingredient/instruction override.
- **Unit conversion.** `src/utils/units.py` `convert_measurements()` annotates recipe text with
  imperial/Fahrenheit equivalents in parentheses (°C→°F, g→oz, kg→lb, ml→tsp/tbsp/cups,
  L→cups, cm/mm→in). Deterministic + idempotent; never replaces the original. Conservative:
  bare "C" is never Celsius (US "1 C" = a cup), bare lowercase "l" is never litres. Claude is
  instructed to keep original units so the math isn't doubled.
- **English translation block.** `translation` / `source_language` JSON fields render as an
  expanded-by-default `> [!info]+ 🌐 English Translation` callout when the source isn't English.
  Caption-bearing renderers (Instagram reel/post, TikTok, Facebook video) place it directly
  **above the Caption** box, with the **Full Transcript** box moved directly **below** the
  Caption; every other note type gets the translation at the note top (a `not in body` guard in
  `format_note` prevents a duplicate). The translation **mirrors the original's line structure**
  (the prompt forbids reflowing a stacked list into a paragraph; the renderer keeps every line
  break) so it reads line-for-line beside the caption. Recipe fields stay in English so they're
  usable.

**Off-site recipe following (`src/extractors/profile_recipe.py`, processor step 1c):** food
posts that say "recipe in bio" / "full recipe on my profile" carry no recipe in the caption.
Best-effort (behind `processing.follow_profile_recipes`, default on, own timeout, non-fatal):
detects the off-site pointer, resolves the poster's bio link (Playwright + platform cookies,
or a URL pasted in the caption), unwraps `l.instagram.com`/`l.facebook.com` redirects, scores
Linktree/Beacons/Stan-style aggregator links against the dish keywords, follows the best match
through the generic extractor, feeds it to Claude, and records a **Recipe source** link in the
note's metadata. Deterministic core is unit-tested; the Playwright fetch needs live iteration.

**Duplicate detection (`queue_manager.py` + `main.py`):** `enqueue_from_file()` returns
already-saved URLs (matched via normalized-URL `ProcessingState.is_done`) instead of silently
skipping; `main.py`'s `scan_inbox()` posts a `send_duplicate_notice()` to `#SAVES-approvals`
(it carries the Re-save/Dismiss decision buttons, so it sits with the normal approval cards)
and clears the line from the inbox — so no tokens are spent reprocessing. Behind
`processing.skip_duplicates` (default on). **The authority is `processing_state.json`, NOT
the vault** — deleting a note in Obsidian does not clear it.

Two ways to deliberately re-save a URL:
- **🔁 Re-save button on the duplicate notice** (`DuplicateNoticeView`, notifications.py):
  forgets the URL (state + session dedup), **retires the old note to `<name>.md.bak`**
  (`retire_note_to_bak` in file_manager.py — rename, never delete; timestamped suffix on
  collision) so the new save takes the original filename, and requeues directly via
  `QueueManager.enqueue_url()` — no re-paste needed. The view is in-process only: buttons on
  notices from before a bot restart go dead (fallback: `/forget`).
- **`/forget` slash command** (autocompletes over saved history, done + permanently-failed
  entries): drops the state entry (`ProcessingState.forget`) and clears the queue manager's
  session dedup sets, then re-pasting the URL reprocesses it. Does not touch the old note.

User-facing walkthrough of both paths: `docs/USER_GUIDE.md`.

---

## Discord Bot Buttons

Every approval message has (in this order — Bora, 2026-07-05):
- **✅ Approve** — writes note, saves learned preference, removes URL from inbox
- **🏷️ Add Tags** — modal; just type tags (space/comma separated, no prefix). Add-only —
  removal lives on the next button. Typed tags are checked against the vault tag index and
  near-duplicates (airfryer vs air-fryer) get a one-tap "Use existing" swap button.
  (custom_id stays `edit_tags` so pre-rename approval cards keep working.)
  **All tags are forced lowercase** by `clean_tags()` (tag_index.py) at every entry point —
  AI generation (processor), this modal, `/tag add`, NL edit, swap — plus a write-time
  backstop in `_finalize()`; the lowercase mirror of `clean_folder_path()` for paths.
- **🗑️ Remove Tags** — ephemeral view with one ✖ button per tag; tap to remove instantly.
  **↩ Undo All** restores the open-time snapshot; header names the save + jump-link to its
  card (ephemeral messages always land at channel bottom — Discord limitation)
- **📁 Change Path** — modal **prepopulated with the current path** (tweak, don't retype);
  input is force-normalized by `clean_folder_path()` (vault_scanner) → ALL CAPS, forward
  slashes — applied at every entry point (AI generation, this modal, NL edit) so case-variant
  duplicate folders can't happen
- **✏️ NL Edit** — natural language edit via a second Claude call. Multi-action per
  instruction; the note's summary/takeaways are in the prompt so content-referencing
  instructions ("tag the coffee types in the summary") work. Lenient JSON parse
  (`_loads_lenient`) — a parse failure is an error+retry, never a fake "cancelled"
- **⏭️ Skip** — defer this save: retract the card + `queue_manager.skip(url)` re-queues the URL
  to the BACK and releases the serial gate so the next save shows now. Reprocessed when it comes
  back (unapproved edits not carried over)
- **🔍 Deep fact-check** — on-demand web-searched claim verification

**Every mutation re-renders the original approval card** (`SAVESBot._refresh_card`: fetches
the card by `discord_message_id`, re-edits embed + buttons). Add/Remove/swap tags, `/tag add`,
Change Path, and NL Edit all call it — so the card the ✅ Approve button sits on always shows
exactly what will be written. (Before 2026-07-05, edits updated only the store; the stale card
made approvals look unedited.) The embed lists ALL tags — no preview cap, and chunked across
multiple "Tags" fields (`_chunk_tags`) so a long recipe tag list (every ingredient ×2 + identity
tags) is shown in full rather than truncated at Discord's 1024-char field limit.

Discord modals **cannot autocomplete** (platform limitation) — search-as-you-type for tags
exists only on the `/tag add` slash command below.

If fact-check or location dispute was found:
- **⚠️ Approve + Include Warning** — adds `> [!warning]` callout to the written note

**`/tag add` slash command** — search-as-you-type autocomplete over the vault tag index
(`src/utils/tag_index.py`: frontmatter `tags:`/`tag:` **plus inline body `#tags`** — code
blocks/URL anchors/numeric-only excluded — 5-min TTL, incremental bump on note write, 256KB
per-note read cap). Manually-typed Obsidian tags count the same as SAVES-written ones.
Suggestions show usage counts; optional `item` param picks which pending save (default
newest). The same index feeds an existing-tags hint into the analysis prompt so Claude
reuses the established taxonomy instead of inventing near-duplicate tags.

**`/forget` slash command** — drops a URL from `processing_state.json` (+ session dedup sets)
so it can be saved again; see Duplicate detection above.

**`/crawl <creator-url>` slash command** — crawl ONE creator's recipes and queue the new ones
(`src/crawlers/`; `CrawlConfirmView` in `bot.py`). Discovers every recipe on the creator page, dedups
against `processing_state.json`, and posts a confirm card (Found/Already-saved/New) with
**✅ Queue** / **📋 List** (ephemeral dry-run list) / **✖ Cancel**. Queue enqueues each URL through
the normal pipeline in the background (one approval card per recipe; paced by
`crawl.rate_limit_seconds`). Per-creator scoped — never traverses to other creators. Needs the
authenticated `cookies/provecho.co_profile/`. CLI equivalent: `scripts/crawl_creator.py`. Also
triggered by **pasting a creator URL into `#SAVES-inbox`** (shared `_crawl_core`; see
"Discord-native saving").

**`/queue` slash command** — reports the serial review queue (see below): the save currently up
for approval + how many wait behind it. Reads `QueueManager.status()`. Ephemeral.

---

## Discord-native saving (`#SAVES-inbox`, `discord.channel_inbox`, optional)

A second way in besides the Obsidian inbox file: paste a URL into `#SAVES-inbox` (or POST via a
Discord **webhook** from Android/Tasker — same thing, a webhook is just a message). `SAVESBot.
on_message` → `_is_inbox_channel` → `_handle_inbox_message`:
- Extracts URLs (`extract_urls`); non-URL chatter is ignored.
- A provecho **creator** URL (`get_crawler` matches) → `_crawl_from_message` runs the crawl
  confirm flow (shared `_crawl_core` with `/crawl`).
- Otherwise → `queue_manager.enqueue_url(raw)`; an already-saved URL posts the normal
  `send_duplicate_notice` (with 🔁 Re-save) to `#SAVES-approvals`.
- Feedback is a **reaction** on the message: ✅ queued · 🔁 duplicate · 🕸️ crawl · 🤔 no URL/nothing new.

`on_message` skips only the bot's OWN messages (`self.user`), so **webhook posts are processed**
(they have `author.bot=True`) — that's what makes the Android/Tasker → Discord-webhook path work
with no new server/port on the NAS. The channel is a paste log; messages aren't deleted (the
reaction is the ack). Disabled unless `channel_inbox` is set; not a validation-required channel.
Mobile setup (Obsidian Advanced URI **and** the webhook/Tasker one-tap share): `docs/MOBILE_SHORTCUTS.md`.

---

## Serial approval queue (`queue_manager.py`, `processing.serial_approval`, default on)

One approval card at a time. After a card is sent, `mark_carded(url)` makes it the **active** gate
holder; `run_processor` calls `_await_active_clear()` and **does not process the next URL** until
the active card is resolved. Resolution paths, each releasing the gate (`_gate.set()`):
- **✅ Approve** → `_finalize` → `queue_manager.resolve(url)` (advances the "X of N" counter).
- **⏭️ Skip** → retract card + `queue_manager.skip(url)` (re-queues the URL to the BACK).
- **`/forget`** on the active URL → `queue_manager.forget(url)` clears active + releases.

**Why:** keeps Discord calm on a `/crawl`/batch, and because the next save is analyzed only AFTER
you approve the current one, it reuses the folder preference (`prefs.set` on approval) AND the tags
(the analysis prompt is fed the vault's existing tags, which update on note write) — so a batch from
one source gets progressively easier to approve.

**Persistence + resilience:** `queue_state.json` holds `{waiting, active, streak_total, streak_done}`,
written atomically on every mutation. On startup `restore_runtime()` re-queues the `waiting` URLs;
`_await_active_clear` clears a stale `active` (already-done, or no card — the crash window between
carding and sending) instead of deadlocking. A long/indefinite approval delay just waits — nothing
is ever auto-approved (`auto_approve_on_timeout` stays false). Status line on each card: "Save X of N
· M still waiting" (snapshot via `QueueManager.snapshot()`, stored on `PendingApproval.queue_status`).
`serial_approval: false` restores the old all-at-once behavior.

---

## Learned Folder Preferences (`preferences.json`)

Source keys:
- Reddit: `reddit:r/{subreddit}`
- YouTube: `youtube:{channel_name}`
- Instagram/TikTok/Facebook: `{platform}:{handle}`
- Generic web: `domain:{hostname}`

On new item: checks preferences.json → injects hint into Claude's prompt.
On approval: saves final folder_path back to preferences.json automatically.

---

## Platforms — Key Notes

**Reddit:** Uses public JSON API (`<url>.json`) — no API key, no PRAW, no credentials.
Private/quarantined subreddits raise `PermissionError` with a descriptive message → alert.

**YouTube:** No video downloaded by default (`download_video: false`). Gets subtitles/
auto-captions. Vision is skipped for YouTube (only thumbnail available).

**Instagram/TikTok/Facebook:** Require cookie files in `cookies/` folder.
Cookie expiry is monitored — alerts sent to `#SAVES-alerts` when approaching expiry.
Export cookies from browser using "Get cookies.txt LOCALLY" extension.

**TikTok caption (verbatim, line-preserved):** yt-dlp's `description` — and the flat
`itemStruct.desc` in TikTok's page JSON — is the creator's caption with **every hard line break
stripped**: the title, `Ingredients`, each `For the …` section header, and every bullet get glued
onto one run of text (some breaks survive only as no-break spaces `U+00A0`). The blank lines and
per-line structure you see in the app's expanded "…more" overlay are simply not in that string,
so they can't be reconstructed from it reliably. **They are, however, in the page's rehydration
JSON.** `__UNIVERSAL_DATA_FOR_REHYDRATION__ → __DEFAULT_SCOPE__ → webapp.video-detail → itemInfo
→ itemStruct → contents[]` is the caption split into the exact lines the app renders — one array
element `{"desc": "<line>", "textExtra": […]}` per line, with **empty `desc` entries standing in
for the blank lines** the author typed between sections. So `tiktok.fetch_contents_caption(url)`
GETs the video page (a plain cookies request — no signed anti-bot tokens, no headless browser;
the blob is server-rendered into the HTML), pulls `contents[]`, and `caption_from_contents()`
joins it (trimming each line's stray trailing space, dropping only leading/trailing blank lines)
into `body_text` — line-for-line and paragraph-for-paragraph identical to the app. `_extract_sync`
prefers this. Behind `platforms.tiktok.use_rich_caption` (default on; legacy name
`use_tdk_caption` still honoured).

> Do **not** use TikTok's `customtdk/item` `itemCustomTDK.article` for the caption. It *looks*
> nicely formatted but is a machine-**reworded SEO rewrite** — it invents a marketing intro,
> rephrases the section headers (`INGREDIENTS:` → `**Ingredients for the Seafood:**`), and is an
> empty string for many videos. `contents[]` is the literal caption and is present whenever the
> video-detail page loads. (An earlier revision used the TDK endpoint; it was replaced.)

`restore_caption_linebreaks()` is the **fallback** (used only when the rehydration blob is
unavailable and we fall back to the flattened yt-dlp `description`). It restores the three ways
TikTok flattens an author's hard newlines, but only when the description has no real newlines of
its own: (1) **runs of 2+ ordinary spaces** (the common case); (2) **no-break spaces `U+00A0`** —
any nbsp-bearing whitespace run becomes a newline; (3) **dash-bullet lists** — a `- item` list
flattened onto one line, where each `" -<char>"` starts a bullet (only fired when ≥3 such markers
prove it's really a list, so an incidental prose dash is left alone; internal hyphens like
`smoke-point`/`Center-cut` have no preceding space and are never split). Idempotent; a break
TikTok collapsed all the way to a single ordinary space with no dash after it (e.g. after a
colon-header like `INGREDIENTS:`) is indistinguishable from a word gap and stays glued — which is
exactly why `contents[]` (not this fallback) is the primary source.

**Generic web (articles):** Uses trafilatura (not readability) to extract structured Markdown
with headings, links, and inline images. All inline images are downloaded locally via
`localize_article_images()` and rewritten to `EmbedRelativeTo` blocks so notes survive the
source being taken down. Playwright scrolls the full page before capture to trigger lazy-
loaded images; image-wrapper CSS classes are stripped so trafilatura's discard rules don't
prune them. The og:image feature/hero image is prepended to the article body and also goes
through the localizer. Vision/OCR is skipped for `generic` platform — body text is already
extracted as structured Markdown.

**Embedded video + thumbnail stripping (generic):** `_extract_video_urls()` collects any
`<video>`/`<source>` src (skipping blob:/data: MSE streams) and prepends it to `media_urls`, so
a recipe/article that embeds a direct video (e.g. provecho's BunnyCDN `.mp4`) gets downloaded +
Whisper-transcribed + embedded like any other media save — no separate video pipeline.
`_strip_thumbnail_images()` drops inline images whose Cloudinary transform declares a small
render size (< 200 px) — the ingredient-icon thumbnails SPA recipe pages put next to each
ingredient — keeping the hero and real step photos. Because `web_recipe` renders the structured
Recipe callout instead of the article body, `formatter._article_photo_embeds()` surfaces the
surviving localized photos in a `## 📸 Photos` section.

**Platform labeling + identity tags + caption suppression (generic):** `_platform_for_url()`
maps recognized generic hosts to a real platform name (`provecho.co` → `provecho`) via
`_GENERIC_PLATFORM_HOSTS`, so the note's `platform:` and metadata say `provecho` not `generic`
(routing/vision/download still key off `detect_platform()`, which stays `generic`). Author comes
from og:author / name="author" (read in one DOM pass — robust on a busy SPA) with a provecho
fallback that parses the handle from og:description (`"<handle>'s <title>."`). `formatter.
_merge_identity_tags()` (via `augment_tags()`) adds the platform + slugged author handle as tags
on EVERY note (skipping the `generic`/`unknown` catch-alls), merged upstream so they show on the card. `_render_web_recipe` shows the raw-text "Caption" section
ONLY when it isn't redundant with the Recipe callout: `_caption_is_recipe_redundant()` compares
the body's meaningful words against the recipe's ingredients+instructions+notes and suppresses
the Caption only when the recipe covers ~all of it AND few unique words remain (dual guard).
A structured recipe page (provecho) whose body is just the recipe re-dumped is dropped; a post
that carries extra content (description, story, tips) keeps the Caption so nothing is lost —
content-based, not hardcoded per platform.

**Ingredient tags (any recipe — Bora, 2026-07-29):** EVERY ingredient becomes a tag, in BOTH a
detailed and a simplified form (e.g. "shredded whole milk mozzarella" → `shredded-whole-milk-mozzarella`
AND `mozzarella`). The model emits these in a dedicated `ingredient_tags` field (the simplified
core needs semantic understanding — a last-word rule breaks on "boneless skinless chicken breast"
→ should be `chicken` not `breast`); the analysis prompt requires it whenever `recipe_ingredients`
is non-null, IN ADDITION to the curated 10–20 `tags`. `formatter._recipe_ingredient_tags()` supplies
them (with a deterministic DETAILED-only fallback — strip leading quantity/unit + parentheticals +
trailing prep note — when the model omits the field). They're merged into `ai_result['tags']` by
`formatter.augment_tags()`, called in the **processor / process_one right after analysis, BEFORE
the approval card is built** — so the card shows exactly what will be written and Add/Remove Tags
act on the full set (Bora, 2026-07-29). `augment_tags` is idempotent (`_frontmatter` calls it again
as a write-time safety net).

**Ingredient icons (provecho):** the site shows a small thumbnail beside each ingredient.
`_extract_ingredient_icons()` captures the (text, icon-url) pairs; `downloader.prepare_ingredient_icon_data_uris()`
downloads each, **downscales it to ~28 px (Pillow → WEBP)**, and stores a base64 `data_uri` on the
pair (deduped by URL within the post); `formatter._ingredients_md()` matches each Recipe-callout
ingredient to its icon by word overlap and prefixes the line with an inline
`![|24](data:image/webp;base64,…)`. **Data URIs, not files:** the icon bytes live INSIDE the note —
no external file, no vault folder, nothing to lose (the strongest form of Hard Constraint #3), and
they render inline (the `media://` plugin can only block-embed external files — its inline feature
is clickable links, not images). Downscaling keeps it to a few KB per note. An icon that fails to
download/encode shows no icon (text only), never a remote URL. Wired into `processor.py` (step 3d)
and `process_one.py`.

**Authenticated generic sites (login-gated):** `GenericExtractor` resolves a per-domain login,
in precedence order: a persistent browser profile `cookies/<host>_profile/` (PREFERRED —
carries IndexedDB, so it's the only thing that works for Firebase-auth SPAs like `provecho.co`
whose token lives in IndexedDB), then a portable `cookies/<host>_session.json` (cookies +
localStorage + sessionStorage, seeded after navigation), then a Netscape `cookies/<host>.txt`.
Match is by bare hostname (covers all paths on the domain) or a URL path segment. Capture a
profile with `python scripts\capture_session.py <login-url> <host>` (log in once). Profiles and
`_session.json` are gitignored (they hold auth tokens) and are machine-specific — the NAS needs
its own one-time capture. `_profile_dir_for_url()` / `_load_session_for_url()` / the `.txt`
loader all live in `generic.py`.

**Whisper transcription:** Runs on the Windows workstation (Ryzen 9 7950X, 64GB RAM).
Start with: `python scripts\whisper_server.py --model large-v3-turbo`
The NAS Docker container POSTs audio files to it via HTTP.

---

## Video Frame Extraction (vision.py)

Scene-change detection is the primary strategy for video frames:
- ffmpeg `select='eq(n,0)+gt(scene,{threshold})'` grabs a frame whenever on-screen content
  changes significantly — each new caption card = scene change, so rolling text is captured
  line-by-line rather than being missed between fixed-interval samples.
- Frames are tiled into a `frame_grid × frame_grid` montage (default 2×2). A vertical reel
  frame already hits Anthropic's image-size cap (~1600 tokens), so a 2×2 tile of 4 frames
  costs the same tokens but covers 4× as much content.
- Falls back to evenly-spaced frames when scene detection finds too few distinct frames
  (e.g. a talking-head with no caption changes).

Config knobs: `vision.max_video_frames`, `vision.frame_scene_threshold`, `vision.frame_grid`.

---

## AI Model Temperature Caching

`claude-opus-4-8` and other newer models reject the `temperature` parameter. The module-level
`_MODELS_REJECTING_TEMPERATURE` set in `claude_client.py` records which models have 400'd on
temperature this process run. On first rejection, the model is added to the set and the call
is transparently retried without temperature. Subsequent calls to the same model skip sending
temperature entirely (no failed request, no log noise). Logged at DEBUG level only.

---

## Fact-Check Behavior

Web-search fact-checking is controlled by `fact_checking.web_search_topics`. Only topics in
that list trigger the slow multi-round web-search pass. Topics not listed still run a quick
local fact-check pass (no web search). The progress of each web-search round is logged at
INFO so the CLI doesn't look frozen during the 1–3 minute health/finance checks.

**Recipe content:** Even if `cooking` or `health` topics are detected, recipe/food content
skips the web-search loop entirely (nutritional macro claims like "52g protein" trigger health
but web-searching them is low-value). Detected via: `note_type` in (`web_recipe`, `recipe`),
or presence of `recipe_ingredients`/`recipe_instructions` fields, or `cooking` in topics.
The local (no-search) fact-check still runs so genuine safety issues (undercooked meat, unsafe
substitutions) can surface.

---

## Environment & Deployment — DEV vs PROD (read this before touching paths)

The system is **portable by design**: `config.yaml` holds only canonical container paths
(`/vault`, `/media`, `/app/state`) that are the same on every deployment. Machine-specific
reality is supplied per-host, never committed:

| | DEV (today) | PROD (target) |
|---|---|---|
| Pipeline app | Workstation, **bare Python** (`python src\main.py`) | NAS, **Docker** (`docker-compose up`) |
| Path mapping | `config.local.yaml` (gitignored overlay, deep-merged by `src/config.py`) | `docker/.env` → compose `${VAULT_HOST}`/`${MEDIA_HOST}`/`${STATE_HOST}` mounts |
| Vault | Local **test vault** `C:/DEV/Apps/SAVES/OBSIDIAN` | Real vault `/volume1/APPS/OBSIDIAN/Remote Vault` |
| Media | `C:/DEV/Apps/SAVES/MEDIA` | `/volume1/MEDIA/SAVES` |
| State JSONs | Repo root | `/volume1/docker/saves/state` (one mounted dir — never single-file binds) |
| Whisper server | Workstation `192.168.1.90:5000` | Workstation (same box; possibly a dedicated server later — one config line, `transcription.remote_url`) |

Rules that keep it portable:
- **Never put machine paths in `config.yaml`** — new host = new `docker/.env` (Docker) or
  new `config.local.yaml` (bare metal), nothing else changes.
- **Never run DEV and PROD simultaneously against the same vault/state** — two writers on
  one inbox/state file will fight. (Distinct DEV vault avoids this today.)
- Repo-root `.env` = secrets only (ANTHROPIC_API_KEY, DISCORD_BOT_TOKEN);
  `docker/.env` = host paths + TZ only. Both gitignored, both have `.example` templates.

**Workstation (Windows, `C:\DEV\Apps\SAVES\SAVES_app`):** git repo, development, and the
Whisper server (`python scripts\whisper_server.py --model large-v3-turbo`). `N:\` is the SMB
view of the NAS when needed.

**NAS (Synology, Docker):** `cp docker/.env.example docker/.env` (adjust paths), create the
state dir, then `docker-compose up --build -d` from `docker/`. **Guided rollout (the current one):
`docs/PROD_ROLLOUT.md`** — plan + full step-by-step + acceptance-test matrix (core, serial queue,
`#SAVES-inbox`, webhook, `/crawl`) + rollback; decisions locked (direct one-token cutover,
Compose-only, full scope, fresh dedup + keep prefs). `docs/DEPLOY_NAS.md` is the terse core-install
reference it supersedes. Run `sh scripts/preflight_nas.sh` from the repo root first (mounts /
secrets / cookies-writable + provecho profile / unreachable Whisper / RAM headroom + compose
sanity). **Cookies mount is `:rw`** — the provecho browser profile is a live Chromium
user-data-dir Playwright writes to.

**Container identity — NON-ROOT (Bora, 2026-08-06).** `saves_app` runs as the DSM service
account **`sa_saves`** (`user: "${SAVES_UID}:${SAVES_GID}"` in compose, plus a matching
in-image account created from the same build args). Root-in-container wrote `root:root` notes
into the bind-mounted vault that **Obsidian and SMB could not edit or delete** — that is the
bug this fixes. Consequences to respect when touching deploy files:
- **NAS-wide SOP: `docs/NAS_SERVICE_ACCOUNTS.md`** — one service account per app/container,
  named `sa_<appname>`, grouped by tree (`/volume1/APPS` → 65537 `app_service_accounts`,
  `/volume1/docker` → 65536 `docker_service_accounts`). Confirmed UIDs: `sa_forgejo` 1030,
  **`sa_saves` 1031**, `sa_obsidian` 1032. That doc is the authority on identity; the
  SAVES-specific ownership map is `docs/PROD_ROLLOUT.md` **§1.6**.
- **⚠️ Never grant the `users` group (GID 100) anything, anywhere** — DSM force-adds every
  account to it, so it is the everyone-group (**SOP Rule 1**). Never add the container to a
  broad group, and never mint a new group, to solve a permission problem. Grant the
  **account** with a DSM ACL entry on exactly the folder it needs; an ACL `user:` ACE matches
  on **UID**, so group membership is irrelevant. Compose therefore carries **no `group_add`**.
- **The APPS tree is governed by DSM ACLs, not POSIX groups.** `docker_service_accounts` is
  denied across `/volume1/APPS`; `sa_saves` is a **named exception** via an explicitly-set
  (`level:0`) ACE that must sort *before* that inherited deny. Three separate grants, least
  verb each: **read-only** on the vault root (`tag_index` walks all of it), **read+write** on
  `0 - INBOX/SAVES/` (inbox rewrite is tempfile+`os.replace` — needs create *and* delete-child)
  and on `SAVES/` (`write_note` calls `os.makedirs`). Ownership stays with `OodaAdmin` /
  `administrators` — never a service account.
- **Credentials and state use no shared group.** `.env` and `cookies/` are `sa_saves:root`
  600/700; state and logs are `sa_saves:administrators` 2750. **Not**
  `docker_service_accounts` — `sa_forgejo` is in it and must not read SAVES's API key or
  session cookies. Same tree ≠ same trust.
- `preflight_nas.sh` `[7]` checks the ACL ordering, rejects a `users` grant, and verifies
  POSIX ownership; the container write test in **SOP §5.1** is the only proof that an ACL
  actually binds a container.
- **Never hardcode a UID without checking.** `id sa_saves` is the authority.
- `src/main.py` sets `os.umask(0o027)` (files 640 / dirs 750) — **not** what grants access
  (the ACL is), only a guarantee that nothing SAVES writes is world-readable. Not `002`.
- Playwright browsers live at `PLAYWRIGHT_BROWSERS_PATH=/opt/playwright` (world-readable),
  **not** `~/.cache` — a non-root user can't read root's cache, and Playwright fails with
  "Executable doesn't exist". The instaloader/whisper volumes moved to `/home/saves/…`.

**Documentation rule that came with it:** any runbook step that creates, copies, or moves a
file **states its owner and mode and gives the `chown`/`chmod`**. Bora works from an admin
account, so nothing inherits the right owner by default.

**NAS co-tenancy (since 2026-08-05): the NAS also runs the Forgejo forge.** `192.168.1.201`,
`/volume1/docker/forgejo`, host port `3443`, ~3 GB of memory limits (Forgejo 2 GB + Postgres
1 GB). Two consequences for SAVES:
- **`saves_app` now declares `mem_limit`** (`SAVES_MEM_LIMIT`, default `3g`, set in
  `docker/.env`). Chromium + ffmpeg were previously uncapped; on a shared box that risks
  OOM-killing the forge. Size it against actual NAS RAM — preflight `[6]` reports both.
- **Never add a `cpus:` key to `docker/docker-compose.yml`.** Synology kernels are built
  without CFS bandwidth control, so *any* CPU quota is a **hard deploy failure**
  (`NanoCPUs can not be set…`), not a warning. Memory limits work; CPU limits do not.
  Discovered during the Forgejo build — full writeup in `docs/FORGEJO.md` §6.
  The same finding is why `docker-compose.yml` has **no top-level `version:` key**: it makes
  Compose V2 reject the v2-style `mem_limit`.

**Mobile capture:** iOS + Android share a URL into the local vault's `0 - INBOX/SAVES/SAVES.md` via
the Obsidian **Advanced URI** plugin (`mode=append`); Obsidian Sync bridges it to the NAS copy
the container watches, and syncs the finished note back. No SMB/VPN. Setup:
`docs/MOBILE_SHORTCUTS.md`. (Depends on an Obsidian client keeping the NAS vault synced.)

**Discord server:** "Bora's AI Ops"
Required channels: `#SAVES-approvals`, `#SAVES-logs`, `#SAVES-alerts`.
Optional: `#SAVES-inbox` (`discord.channel_inbox`) — paste a URL there, or POST via a Discord
webhook from Android/Tasker, to queue a save without touching the Obsidian inbox file; a
provecho **creator** URL pasted there triggers `/crawl`. See "Discord-native saving" below and
`docs/MOBILE_SHORTCUTS.md`.

---

## Current State

**Operating decision — everything is IMMEDIATE (Bora, 2026-07-04).** Extraction, AI analysis,
and Discord approval cards all fire the moment a URL arrives; nothing is batched or deferred,
and `auto_approve_on_timeout` stays `false`. Rationale: instant bug/quality feedback during
tuning, and approvals are handled while the content is fresh in mind. The Batch API is a
gated future phase (ROADMAP Phase 6) — do not pull deferral of any kind forward without a new
explicit decision. Full rationale: `docs/ROADMAP.md` → "Decisions locked".

**Refinement — serial approval gating (Bora, 2026-07-29).** The IMMEDIATE decision is about not
batching/deferring the *current* item: a URL is still processed and its card fired the instant it
is that URL's turn. `processing.serial_approval` (default on) serializes the QUEUE ORDER — the
next URL waits until the current card is approved/skipped — it does NOT defer or batch the active
item, and still never auto-approves. This is compatible with IMMEDIATE (one card is always live);
it just prevents a `/crawl`/batch from firing all cards at once, and lets each save reuse the
folder/tags just approved. See "Serial approval queue" above.

**Decision notation discipline:** when a decision constrains future behavior, record it at the
point of use (code comment) *and* in ROADMAP "Decisions locked", in the same commit.

**Documentation discipline (MANDATORY):** docs are part of the change, not a follow-up. Any
commit that adds a feature, alters behavior, or changes architecture MUST update the relevant
docs **in the same commit** — never "later". The doc surfaces and what lives where:
- `CLAUDE.md` (this file) — the canonical map: repo tree, data flow, note types, button/slash
  behavior, platform notes, hard constraints, config. If a new file, note type, button, slash
  command, config key, or flow appears (or one is renamed/removed), fix it here.
- `docs/COMMANDS.md` — the quick-reference cheat sheet: a one-line-per-entry table of EVERY
  slash command, Discord button, and CLI script. Whenever a command/button/script is added,
  renamed, or removed, update its row here in the same commit (this is the terse index;
  `USER_GUIDE.md` holds the detailed behavior).
- `docs/USER_GUIDE.md` — the user-facing nuances: every slash command, button, surprising
  default, and recovery path, written for the person *using* the bot. Any new or changed
  user-visible behavior (a command, a button, a gotcha like "deleting the note doesn't
  clear the dedup") gets its entry here in the same commit.
- `docs/ARCHITECTURE.md` — how the pieces fit + the §11 scaling analysis. Update when a
  component's responsibility, threading model, or a scaling characteristic changes.
- `docs/ROADMAP.md` — tick items `[x]` when shipped; add new phases/items when scope grows;
  record constraining decisions under "Decisions locked".
- `docs/NAS_SERVICE_ACCOUNTS.md` — **NAS-wide SOP**: one service account per app/container,
  naming (`sa_<appname>`), group-by-tree, the account registry with confirmed UIDs, the
  and **the permission scheme (§5)**: `users` is granted nothing anywhere; only
  `OodaAdmin`/`administrators` hold Full Control; cross-app access is a named ACL exception
  on the narrowest folder — never a group.
  Infrastructure, not application. Update it when an account is added or a convention
  changes; anything SAVES-specific belongs in `PROD_ROLLOUT.md` §1.6 instead.
- `docs/FORGEJO.md` — the self-hosted **git forge** this repo lives on (Forgejo 15 LTS +
  PostgreSQL 17, hardened non-root, on the same NAS). Infrastructure, not application: update
  it when the forge's version pins, identity model, TLS/cert path, firewall, or backup
  procedure changes. SAVES-side consequences (remote URL, PAT auth, CA trust) belong in
  this file's "Git Workflow" section.
- Module/function docstrings + code comments — the point-of-use record for any non-obvious
  contract (e.g. the tag-index threading contract, the zero-delete rule).
Rule of thumb before committing: *if someone read only the docs, would they now describe the
system correctly?* If not, the doc edit belongs in this commit. When unsure a doc claim is
still true, verify against the code before writing it — stale docs are worse than none.

**Actively in use.** `process_one.py` has been run end-to-end against real Instagram,
YouTube, Reddit, and web article URLs. Notes write to the Obsidian vault. Discord approval
flow is the next stage to test in full.

**Model routing (as configured):**
- Stage 1 (vision): `claude-haiku-4-5` reads all image slides / video frames → OCR text
- Stage 2 (analysis): `claude-sonnet-4-6` analyzes OCR text (no images → cheaper)
- Stage 3 (fact-check, health/finance posts): `claude-sonnet-4-6` with web search

**Vision skip:** `generic` platform (web articles) and `youtube` are skipped for vision.
Article text is already extracted as structured Markdown; video frames are not worthwhile.

**Cost profile (typical 10-slide health Instagram post with fact-check):**
~$0.30–0.50 with current model routing and prompt caching.
Main driver is the Sonnet web-search loop (up to 5 searches). Adjust
`fact_checking.max_searches` in config to trade coverage for cost.

**Known gaps (not yet wired in):**
- `with_retry()` in `src/utils/retry.py` is now wired into the remote-transcription POST
  (`transcriber._transcribe_remote`) using `processing.retry_attempts`/`retry_delay_seconds`.
  Extractor and downloader calls are still NOT retry-wrapped — that needs a transient-vs-
  permanent error split first so it doesn't retry deleted-URL 404s. (Claude API resilience is
  handled by the Anthropic SDK's own `max_retries`, set via `ai.max_retries`.)
- Several `config.yaml` keys are defined but unused: `processing.concurrent_downloads`,
  `media.download_video`, `media.download_images`, `notes.tags_min/max`,
  `transcription.skip_if_captions_available`.

**Prompt caching is active** on system prompts (`_call`) and the fact-check web-search
loop's first user message. Back-to-back posts and JSON retries benefit automatically.

---

## First Run Checklist

1. Fill in `C:\DEV\Apps\SAVES\SAVES_app\.env` — only 2 keys needed:
   ```
   ANTHROPIC_API_KEY=sk-ant-...
   DISCORD_BOT_TOKEN=...
   ```
2. Create Discord server "Bora's AI Ops" with channels:
   `#SAVES-approvals`, `#SAVES-logs`, `#SAVES-alerts`
3. Create Discord bot at discord.com/developers → Bot → copy token
4. Start Whisper server on workstation:
   `python scripts\whisper_server.py --model large-v3-turbo`
5. Run smoke test: `python scripts\test_connection.py`
6. Run first real test: `python scripts\process_one.py "https://reddit.com/r/..."`
   — runs the full pipeline and **writes the note to vault_root**. Add `--dry-run` to
   print only without writing.
7. Run full pipeline: `python src\main.py`, paste a URL into `0 - INBOX/SAVES/SAVES.md`,
   watch Discord, approve, verify note appears in vault
8. Deploy to NAS: `cp docker/.env.example docker/.env` (host paths), create the state dir,
   then `docker-compose up --build -d` from `docker/`

---

## Git Workflow (Important)

**The remote is self-hosted Forgejo on the NAS. GitHub is retired (Bora, 2026-08-05.)**

```
origin  https://192.168.1.201:3443/<user>/SAVES.git   (Forgejo 15 LTS, LAN-only)
```

Build, hardening rationale, cert/CA model, PAT creation, and backup/upgrade procedures:
**`docs/FORGEJO.md`** (v3, verified against the real hardware 2026-08-05). That doc is the
authority on the forge itself; this section covers only how *this repo* uses it.

### The loop

Claude Code runs **locally on the Windows workstation** with direct filesystem and `git`
access, so it edits files in place — no patch files, no tarballs, no clone-staleness class
of bug. After each session:

```powershell
git status                      # review what Claude changed
git log --oneline -3            # Claude has already committed
git push origin main            # ← YOUR step
```

**Pushing is always your manual step. Claude commits; you push.** (Unchanged — this is a
working preference, not a transport limitation.)

### Things that are different now that the forge is LAN-only

1. **Auth is HTTPS + a Personal Access Token.** SSH is disabled on the forge by design
   (`DISABLE_SSH=true`, no port published — `FORGEJO.md` Locked Decision 6). Username = your
   Forgejo username, password = **the token**, cached by `credential.helper manager` on
   Windows. Use a **dedicated, repository-scoped PAT for Claude Code** so it can be revoked
   without rotating your primary credential (`FORGEJO.md` §13).

2. **Clients must trust the step-ca root**, not the intermediate. Without it every `git`
   operation fails TLS verification. Installed per `FORGEJO.md` Phase 11 — and on Windows,
   Git needs the *extra* step there (Git for Windows ships its own OpenSSL CA bundle and does
   not read the Windows store unless told to). **Never "fix" a TLS error with
   `http.sslVerify=false`** — that silently disables the protection the whole build exists to
   provide; fix the trust store instead.

3. **`gh` CLI and GitHub-specific commands do not work.** No `gh pr`, no GitHub Actions
   integration. Forgejo's API is Gitea/GitHub-*shaped* but is not a drop-in. If a workflow
   needs a PR, use the Forgejo web UI at `https://192.168.1.201:3443/`.

4. **Claude Code on the web can no longer reach this repo.** The forge is LAN-only with no
   port-forward (`FORGEJO.md` Locked Decision 11), so cloud sessions have no remote to pull
   from. **Local Claude Code is now the only development path.** The old patch-delivery and
   Anti-Stale Protocol that this section used to describe existed solely to work around the
   web sandbox's lack of push credentials — both are **obsolete and have been removed**. If
   `patches/` still exists, it is historical.

5. **⚠️ The repo's only copies are the NAS forge and your local clones.** Retiring GitHub
   removed the off-site copy. `/volume1/docker/forgejo` (repo tree + a `pg_dump` of the
   database) **must** be in your backup set — see `FORGEJO.md` → Backups. A NAS loss without
   that backup loses the history; the working tree on the workstation would be all that
   survives.

### Deploy interaction

The NAS pulls SAVES from the *same NAS's* Forgejo (`git pull` in
`/volume1/docker/saves/app`). Forgejo must therefore be up to update SAVES — it is
`restart: unless-stopped`, so this only bites during a forge outage or upgrade. It does
**not** affect a running SAVES container, which needs no git access at all.
