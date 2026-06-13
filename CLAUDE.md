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
│   │   └── generic.py             # Playwright + readability-lxml; cookie banner auto-click
│   ├── media/
│   │   ├── downloader.py          # download_media() → {media_root}/{platform}/{author}/{slug}/
│   │   │                          # abs_to_obsidian_embed() returns BARE relative path (no ![[]])
│   │   ├── transcriber.py         # mode=remote: POST to 192.168.1.90:5000; mode=local: faster-whisper
│   │   └── vision.py              # Images → base64; videos → ffmpeg frames at 10/33/57/80%
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

ai:
  model: "claude-opus-4-8"               # Main analysis model

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
| `web_recipe` | Generic | Summary, Ingredients, Instructions, Hero image, Metadata |
| `web_travel` | Web | Summary, Key Details, Images, Metadata |
| `web_article` | Web | Summary, Takeaways, Original Content, Hero image, Metadata |
| `web_generic` | Web | Summary, Takeaways, Original Content, Metadata |

All types include YAML frontmatter: title, source_url, platform, saved_date, author, tags, type: save.

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

**Whisper transcription:** Runs on the Windows workstation (Ryzen 9 7950X, 64GB RAM).
Start with: `python scripts\whisper_server.py --model large-v3-turbo`
The NAS Docker container POSTs audio files to it via HTTP.

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

## Current State (as of this session)

**Code complete and compiles.** All 40 Python files pass `python -m compileall`.

**Bugs fixed in this session (not yet end-to-end tested):**
1. `facebook.py` — missing `import asyncio` (crash on every Facebook URL)
2. `downloader.py` — `abs_to_obsidian_embed` was wrapping in `![[]]`; formatter also
   wraps → double-wrapped embeds. Fixed: function now returns bare relative path.
3. `processor.py` + `bot.py` — `body_text` and `captions` were missing from
   `content_summary`, so approved notes had empty "Original Content" / "Caption" sections.
4. `bot.py` + `main.py` — `state.mark_done()` was never called; state stayed `"pending"`
   forever. Fixed: `state` passed into `SAVESBot`, called in `_finalize()`.
5. `main.py` — `logging.basicConfig` opened log files before `os.makedirs("logs")`
   ran → crash if `logs/` didn't exist. Fixed: makedirs moved to module level.
6. `cookie_checker.py` — dead `reddit_cookies` entry (Reddit no longer uses cookies).

**Not yet done / not yet tested:**
- `with_retry()` in `src/utils/retry.py` is defined but never called anywhere.
  Should be wired into extractor/download calls for resilience.
- `config.yaml` has several unused keys: `processing.concurrent_downloads`,
  `processing.retry_attempts`, `media.download_video`, `media.download_images`,
  `notes.tags_min/max`, `transcription.skip_if_captions_available`.
  These are either dead config or features not yet wired in — leave them for now.
- No end-to-end test has been run yet. `scripts/process_one.py <url>` is the first
  real test — run this before anything else.

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
   — prints the formatted note, no Discord, no file writes
7. Run full pipeline: `python src\main.py`, paste a URL into `00 - FILE.md`,
   watch Discord, approve, verify note appears in vault
8. Deploy to NAS: `docker-compose up --build` from `docker/`

---

## Git Workflow (Important)

This repo is developed via Claude Code on the web (ultraplan session). Claude cannot
push to GitHub directly — the session has no credentials. After Claude commits changes:

**You run from `C:\DEV\Apps\SAVES\SAVES_app`:**
```bash
git am patches\<patch-file>    # patches are delivered to the patches\ folder
git push origin main
```

**Patch filename convention:** use underscores only, NO dashes. The file-delivery
download strips dashes from filenames, so `saves-foo-patch.patch` arrives as
`savesfoopatch.patch` and breaks the `git am` command. Name patches like
`saves_foo_patch.patch`.

Or if Claude has already committed to a connected session:
```bash
git pull origin main
git push origin main
```

Pushing is always your manual step. Claude commits; you push.
