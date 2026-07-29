# SAVES — Smart Archival & Vault Entry System

## What This Is

SAVES is a personal content archiving pipeline. It watches a single Obsidian file
(`0 - INBOX/SAVES.md`) for URLs, extracts content from social/web platforms, downloads media
to NAS, transcribes audio via remote Whisper, reads on-screen text via Claude vision,
sends the result to a Discord bot for approval, then writes a structured Obsidian note
to a Synology NAS vault.

**Owner:** Bora (Oodaloop80)
**Runtime:** Docker on Synology NAS (python:3.11-slim)
**Dev machine:** Windows workstation. Repo at `C:\DEV\Apps\SAVES\SAVES_app`; patch files go in `C:\DEV\Apps\SAVES\SAVES_app\patches`
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
│   ├── queue_manager.py           # ProcessingState (JSON), QueueManager.enqueue_from_file()
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
│   │   └── bot.py                 # SAVESBot; ApprovalView (6 buttons + conditional ⚠️);
│   │                              # TagRemoveView (✖ per tag, ↩ Undo All); _finalize() writes note
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
0 - INBOX/SAVES.md
    │  (watchdog, 3s debounce)
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
   (processor returns; picks next URL)

[Hours/days later — Discord button click]
    ▼
bot._finalize()
    │
    ├── format_note()               → Markdown string (per-type template)
    ├── write_note()                → Obsidian vault file (atomic)
    ├── prefs.set(source_key, path) → learned preference saved
    ├── state.mark_done(url, path)  → processing_state.json updated
    └── remove_url_from_inbox()     → URL removed from the inbox file
```

---

## Key Configuration Values (`config.yaml`)

```yaml
paths:                       # CANONICAL CONTAINER PATHS — identical everywhere, never edit
  vault_root: "/vault"       #   per-machine. Docker maps host dirs onto them via docker/.env
  saves_root: "/vault/SAVES" #   (VAULT_HOST/MEDIA_HOST/STATE_HOST); bare-metal dev overrides
  inbox_file: "/vault/0 - INBOX/SAVES.md"   # them in gitignored config.local.yaml.
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

discord:
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
- **🔍 Deep fact-check** — on-demand web-searched claim verification

**Every mutation re-renders the original approval card** (`SAVESBot._refresh_card`: fetches
the card by `discord_message_id`, re-edits embed + buttons). Add/Remove/swap tags, `/tag add`,
Change Path, and NL Edit all call it — so the card the ✅ Approve button sits on always shows
exactly what will be written. (Before 2026-07-05, edits updated only the store; the stale card
made approvals look unedited.) The embed lists ALL tags (no 8-tag preview cap).

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
authenticated `cookies/provecho.co_profile/`. CLI equivalent: `scripts/crawl_creator.py`.

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
_merge_identity_tags()` adds the platform + slugged author handle as tags on EVERY note (skipping
the `generic`/`unknown` catch-alls). For `provecho`, `_render_web_recipe` suppresses the "Caption"
section — the page's raw text there is just the recipe re-dumped (redundant with the Recipe
callout); blog recipes with a real story keep it.

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
| Vault | Local **test vault** `C:/DEV/Apps/SAVES/OBSIDIAN` | Real vault `/volume1/NAS/OBSIDIAN/Remote Vault` |
| Media | `C:/DEV/Apps/SAVES/MEDIA` | `/volume1/NAS/MEDIA/SAVES` |
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
state dir, then `docker-compose up --build -d` from `docker/`. **Full runbook:
`docs/DEPLOY_NAS.md`** (SSH, cookies, firewall, the DEV/PROD single-Discord-token conflict, live
test); run `sh scripts/preflight_nas.sh` from the repo root first to catch a bad mount / missing
secret / unreachable Whisper before the build.

**Mobile capture:** iOS + Android share a URL into the local vault's `0 - INBOX/SAVES.md` via
the Obsidian **Advanced URI** plugin (`mode=append`); Obsidian Sync bridges it to the NAS copy
the container watches, and syncs the finished note back. No SMB/VPN. Setup:
`docs/MOBILE_SHORTCUTS.md`. (Depends on an Obsidian client keeping the NAS vault synced.)

**Discord server:** "Bora's AI Ops"
Required channels: `#SAVES-approvals`, `#SAVES-logs`, `#SAVES-alerts`

---

## Current State

**Operating decision — everything is IMMEDIATE (Bora, 2026-07-04).** Extraction, AI analysis,
and Discord approval cards all fire the moment a URL arrives; nothing is batched or deferred,
and `auto_approve_on_timeout` stays `false`. Rationale: instant bug/quality feedback during
tuning, and approvals are handled while the content is fresh in mind. The Batch API is a
gated future phase (ROADMAP Phase 6) — do not pull deferral of any kind forward without a new
explicit decision. Full rationale: `docs/ROADMAP.md` → "Decisions locked".

**Decision notation discipline:** when a decision constrains future behavior, record it at the
point of use (code comment) *and* in ROADMAP "Decisions locked", in the same commit.

**Documentation discipline (MANDATORY):** docs are part of the change, not a follow-up. Any
commit that adds a feature, alters behavior, or changes architecture MUST update the relevant
docs **in the same commit** — never "later". The doc surfaces and what lives where:
- `CLAUDE.md` (this file) — the canonical map: repo tree, data flow, note types, button/slash
  behavior, platform notes, hard constraints, config. If a new file, note type, button, slash
  command, config key, or flow appears (or one is renamed/removed), fix it here.
- `docs/USER_GUIDE.md` — the user-facing nuances: every slash command, button, surprising
  default, and recovery path, written for the person *using* the bot. Any new or changed
  user-visible behavior (a command, a button, a gotcha like "deleting the note doesn't
  clear the dedup") gets its entry here in the same commit.
- `docs/ARCHITECTURE.md` — how the pieces fit + the §11 scaling analysis. Update when a
  component's responsibility, threading model, or a scaling characteristic changes.
- `docs/ROADMAP.md` — tick items `[x]` when shipped; add new phases/items when scope grows;
  record constraining decisions under "Decisions locked".
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
7. Run full pipeline: `python src\main.py`, paste a URL into `0 - INBOX/SAVES.md`,
   watch Discord, approve, verify note appears in vault
8. Deploy to NAS: `cp docker/.env.example docker/.env` (host paths), create the state dir,
   then `docker-compose up --build -d` from `docker/`

---

## Git Workflow (Important)

This repo is developed via Claude Code on the web. Claude cannot push to GitHub
directly — the container has no credentials. After each session:

**Preferred: patch delivery**
```bash
git apply patches\<patch-file>   # USE git apply, NOT git am
git add -A
git commit -m "..."
git push origin main
```

`git apply` is used (not `git am`) because `git am` additionally requires the committer
email to match the patch header, which causes failures in this environment.

**Patch filename convention:** underscores only, NO dashes. File delivery strips
dashes — `saves-foo.patch` arrives as `savesfoo.patch` and breaks the command.
Name patches `saves_<topic>__base_<shortsha>.patch`, where `<shortsha>` is the commit
the patch was built against (see Anti-Stale Protocol below).

**If the patch fails** (context mismatch from local edits): use the full-file deliveries
instead — Claude delivers the complete file built from a fresh GitHub clone, safe to
overwrite directly.

### Anti-Stale Protocol (MANDATORY for Claude — both failures it prevents happened)

The container's clone goes stale the instant the user pushes from their machine. Two
patch failures were caused by Claude building patches against a clone from earlier in
the session instead of the live remote. To prevent recurrence, Claude MUST:

1. **Verify live HEAD before EVERY patch or file delivery.** The git proxy in this
   environment is unreliable (token expires mid-session). Use the GitHub REST API instead:
   ```bash
   curl -sS "https://api.github.com/repos/Oodaloop80/SAVES/commits/main" | python3 -c \
     "import sys,json; d=json.load(sys.stdin); print(d['sha'][:7])"
   ```
   Then download the live tarball:
   ```bash
   curl -sL "https://api.github.com/repos/Oodaloop80/SAVES/tarball/main" -o /tmp/saves.tar.gz
   mkdir -p /tmp/SAVES-fresh && tar -xzf /tmp/saves.tar.gz -C /tmp/SAVES-fresh --strip-components=1
   ```
   Build all patches and file deliveries from `/tmp/SAVES-fresh`. Never reuse a clone from
   earlier in the session.

2. **Stamp the base SHA in the patch filename:** `saves_<topic>__base_<shortsha>.patch`.

3. **User's pre-apply check:** before `git apply`, confirm `git rev-parse --short HEAD`
   matches the `base_<shortsha>` in the filename. If they differ, the patch is stale —
   tell Claude your current HEAD and ask for a rebuild rather than forcing it.

4. **One patch per delivery.** Don't leave multiple patches in `patches\`; running an old
   one first wastes a cycle (the atomic failure is harmless but confusing).

Note: a SessionStart hook only syncs at session *start*, so it does NOT fix mid-session
staleness (which is what bit us). The verify-before-deliver rule above is the real fix.

Pushing is always your manual step. Claude commits; you push.
