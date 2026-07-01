# SAVES — Smart Archival & Vault Entry System

## What This Is

SAVES is a personal content archiving pipeline. It watches a single Obsidian file
(`00 - FILE.md`) for URLs, extracts content from social/web platforms, downloads media
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
│   │                              # image, markdown normalize; readability-lxml fallback
│   ├── media/
│   │   ├── downloader.py          # download_media() → {media_root}/{platform}/{author}/{slug}/
│   │   │                          # abs_to_obsidian_embed() returns BARE relative path (no ![[]])
│   │   │                          # localize_article_images() downloads inline article images
│   │   ├── transcriber.py         # mode=remote: POST to 192.168.1.90:5000; mode=local: faster-whisper
│   │   └── vision.py              # Images → base64; videos → scene-change frames → 2×2 montage
│   ├── ai/
│   │   ├── prompts.py             # SYSTEM_PROMPT, build_user_prompt(), fact-check/travel prompts
│   │   ├── claude_client.py       # analyze_content(), fact_check(), nl_edit()
│   │   └── verifier.py            # check_travel_location() — called only when travel in topics
│   ├── discord_bot/
│   │   ├── approval.py            # PendingApproval dataclass, PendingApprovalsStore (JSON)
│   │   ├── notifications.py       # send_approval_request(), send_log(), send_alert()
│   │   └── bot.py                 # SAVESBot; ApprovalView (4 buttons); _finalize() writes note
│   ├── notes/
│   │   ├── formatter.py           # format_note() dispatches to 13 per-type renderers
│   │   └── file_manager.py        # write_note() atomic; move_note() with SHA256 verify
│   └── utils/
│       ├── url_parser.py          # extract_urls(), normalize_url(), detect_platform()
│       ├── file_io.py             # read_inbox(), remove_url_from_inbox() (atomic)
│       ├── preferences.py         # PreferencesStore — learned folder routing per source
│       ├── cookie_checker.py      # Checks instagram/tiktok/facebook cookie file mtimes
│       └── retry.py               # with_retry() decorator — defined but not yet wired in
├── scripts/
│   ├── process_one.py             # CLI test: run full pipeline for one URL, print note
│   ├── test_connection.py         # Smoke test: Anthropic API, Discord bot, Reddit JSON API
│   ├── whisper_server.py          # Flask server (runs on WORKSTATION, not NAS)
│   └── refresh_cookies.py         # Instructions for exporting browser cookies
├── docker/
│   ├── Dockerfile                 # python:3.11-slim + ffmpeg + chromium + playwright
│   └── docker-compose.yml         # 8 volumes: vault, media, cookies, config, logs, state files
├── config.yaml                    # All configuration (see key values below)
├── .env.example                   # Template: ANTHROPIC_API_KEY, DISCORD_BOT_TOKEN
└── cookies/                       # instagram.txt, tiktok.txt, facebook.txt (gitignored)
```

---

## Data Flow

```
00 - FILE.md
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
    └── remove_url_from_inbox()     → URL removed from 00 - FILE.md
```

---

## Key Configuration Values (`config.yaml`)

```yaml
paths:
  vault_root: "/volume1/NAS/OBSIDIAN/Remote Vault"
  saves_root: "/volume1/NAS/OBSIDIAN/Remote Vault/SAVES"
  inbox_file: "/volume1/NAS/OBSIDIAN/Remote Vault/SAVES/00 - FILE.md"
  media_root: "/volume1/NAS/MEDIA/SAVES"

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
  model: "claude-opus-4-8"               # Main analysis model (text-only when ocr_model set)

fact_checking:
  model: "claude-sonnet-4-6"             # Cheaper model for fact-checking
  include_images: false                  # OCR already captured image content; raw pixels would double-bill
  web_search_topics: ["health", "finance"]  # Only web-search for these; recipes skip even if health triggered

discord:
  auto_approve_on_timeout: false
  auto_approve_timeout_hours: 48

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
| `web_recipe` | Generic | Media, Summary, Recipe, Caption, Text from Images, Transcript, Sources & Metadata |
| `web_travel` | Web | Summary, Key Details, Images, Metadata |
| `web_article` | Web | Summary, Takeaways, Article body (Markdown with inline images), Metadata |
| `web_generic` | Web | Summary, Takeaways, Article body (Markdown), Metadata |

All types include YAML frontmatter: title, source_url, platform, saved_date, author, tags, type: save.

**Recipe injection (all platforms):** When Claude extracts recipe fields (`recipe_ingredients`,
`recipe_instructions`, etc.) from *any* note type (not just `web_recipe`), a `## Recipe`
section is automatically injected before the `---` separator. This handles Instagram Reels,
TikTok videos, or Reddit posts that contain recipes.

---

## Discord Bot Buttons

Every approval message has:
- **✅ Approve** — writes note, saves learned preference, removes URL from inbox
- **📁 Change Path** — modal prompt → updates folder_path → saves preference
- **🏷️ Edit Tags** — modal prompt with +add / -remove syntax
- **✏️ NL Edit** — natural language edit via a second Claude call

If fact-check or location dispute was found:
- **⚠️ Approve + Include Warning** — adds `> [!warning]` callout to the written note

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

**Generic web (articles):** Uses trafilatura (not readability) to extract structured Markdown
with headings, links, and inline images. All inline images are downloaded locally via
`localize_article_images()` and rewritten to `EmbedRelativeTo` blocks so notes survive the
source being taken down. Playwright scrolls the full page before capture to trigger lazy-
loaded images; image-wrapper CSS classes are stripped so trafilatura's discard rules don't
prune them. The og:image feature/hero image is prepended to the article body and also goes
through the localizer. Vision/OCR is skipped for `generic` platform — body text is already
extracted as structured Markdown.

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

## Environment & Deployment

**Workstation (Windows, `C:\DEV\Apps\SAVES\SAVES_app`):**
- Git repo, development
- Runs `whisper_server.py` when transcription is needed
- `N:\` mapped to `\\NAS-hostname\NAS` (SMB)
- Obsidian vault at `N:\NAS\OBSIDIAN\Remote Vault`

**NAS (Synology, Docker):**
- `docker-compose up --build` from `docker/` directory
- `.env` must exist with ANTHROPIC_API_KEY and DISCORD_BOT_TOKEN
- Vault and media paths are volume-mounted

**Discord server:** "Bora's AI Ops"
Required channels: `#SAVES-approvals`, `#SAVES-logs`, `#SAVES-alerts`

---

## Current State

**Actively in use.** `process_one.py` has been run end-to-end against real Instagram,
YouTube, Reddit, and web article URLs. Notes write to the Obsidian vault. Discord approval
flow is the next stage to test in full.

**Model routing (as configured):**
- Stage 1 (vision): `claude-haiku-4-5` reads all image slides / video frames → OCR text
- Stage 2 (analysis): `claude-opus-4-8` analyzes OCR text (no images → cheaper)
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
7. Run full pipeline: `python src\main.py`, paste a URL into `00 - FILE.md`,
   watch Discord, approve, verify note appears in vault
8. Deploy to NAS: `docker-compose up --build` from `docker/`

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
