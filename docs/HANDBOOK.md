# SAVES — Handbook (Recreate & Maintain)

The single source of truth for **what SAVES is, how it works, how to rebuild it from nothing,
and how to keep it running.** If the app were lost tomorrow, this doc plus the repo should be
enough to stand it back up. Update it in the same commit as any change that affects setup,
dependencies, architecture, or operations.

- Quick orientation for a coding session: `CLAUDE.md` (auto-loaded).
- Strategy & phased plan: `docs/PLAN.md`.
- Live to-production checklist: `docs/ROADMAP.md`.

---

## 1. What SAVES Is

A personal content-archiving pipeline. You share a URL (from a phone or desktop); it gets
appended to one Obsidian inbox file (`00 - FILE.md`). SAVES watches that file, extracts the
content (social post / video / article), downloads the media, transcribes any audio, reads any
on-screen text, asks Claude to organize + tag + summarize it, sends you a Discord approval card,
and — once you approve — writes a structured Obsidian note into your vault and removes the URL
from the inbox.

- **Owner:** Bora (Oodaloop80)
- **Runtime:** Docker on a Synology NAS (`python:3.11-slim`)
- **Dev machine:** Windows workstation; repo at `C:\DEV\Apps\SAVES\SAVES_app`
- **Whisper host:** the workstation, `192.168.1.90:5000`

---

## 2. Hard Constraints (never violate)

1. **Zero delete calls.** No `os.remove`, `os.unlink`, `shutil.rmtree`, or `Path.unlink` anywhere.
   Atomic writes use `tempfile + os.replace()`. Cross-volume moves rename the source to `.bak`.
   Orphaned temp files are left in place on error. Verify:
   `grep -rn "os.remove\|os.unlink\|shutil.rmtree\|\.unlink(" src/ scripts/`
2. **Single asyncio event loop.** The watchdog thread bridges to it via `call_soon_threadsafe`.
   Never create a second loop or call `asyncio.run()` inside the running loop. Sync/blocking libs
   run via `asyncio.to_thread`.

---

## 3. Architecture & Data Flow

```
00 - FILE.md
   │  watchdog (3s debounce) → call_soon_threadsafe
   ▼
asyncio.Queue ──► processor (serial, one URL at a time)
   │
   ├─ 1. get_extractor(url).extract()        → ExtractedContent
   ├─ 2. enrich_embedded_media()             → cross-platform embeds (e.g. YT in a Reddit post)
   ├─ 3. prefs.hint(source_key)              → folder hint for Claude
   ├─ 4. download_media()                    → local media files
   ├─ 4b localize_article_images()           → inline web-article images downloaded + embedded
   ├─ 5. transcribe()                        → transcript (remote Whisper) | captions | None
   ├─ 6. prepare_images_for_claude()         → vision blocks (skipped for youtube + generic)
   ├─ 7. analyze_content()                   → ai_result (note_type, folder, tags, summary, …)
   ├─ 8. fact_check() + check_travel()       → parallel, non-fatal
   └─ 9. new_pending() + send_for_approval() → Discord card; processor moves to next URL
                                               │
                          (hours/days later)  ▼  Discord button click
                          bot._finalize():
                             format_note() → write_note() (atomic)
                             prefs.set(source_key, path)
                             state.mark_done(url, path)
                             remove_url_from_inbox()
```

The processor fires the Discord card and immediately moves on; the **bot's button handler is the
only thing that writes a note.** Pending approvals persist to JSON and re-send on bot restart.

---

## 4. Repository Map

```
src/
  main.py            entry — starts watcher + processor + Discord bot on one loop
  config.py          yaml.safe_load; cached dict
  credentials.py     loads .env, validates required keys
  processor.py       the pipeline orchestrator (above)
  watcher.py         watchdog Observer, 3s debounce, threadsafe bridge
  queue_manager.py   URL parse + dedup vs processing_state.json; ProcessingState
  extractors/
    base.py          ExtractedContent dataclass + BaseExtractor ABC
    __init__.py      get_extractor(url, config) router
    reddit.py        public Reddit JSON API (no creds); top comments; gallery/video
    youtube.py       yt-dlp metadata + captions + chapters (no video download)
    instagram.py     yt-dlp + gallery-dl; cookies
    tiktok.py        yt-dlp --write-info-json; cookies
    facebook.py      yt-dlp + cookies; detects shared articles → generic
    generic.py       Playwright → trafilatura Markdown; lazy-image + discard-class fixes
    enrich.py        pulls embedded cross-platform media
  media/
    downloader.py    yt-dlp / gallery-dl / direct; HEIC→JPG; localize_article_images()
    transcriber.py   mode=remote (HTTP POST to Whisper) | local (faster-whisper)
    vision.py        images→base64; video→scene-change frames→2×2 montage
  ai/
    prompts.py       SYSTEM_PROMPT, OCR/fact-check/NL-edit prompts, builders
    claude_client.py analyze_content(), fact_check(), nl_edit(); two-stage OCR; temp cache
    verifier.py      check_travel_location()
  discord_bot/
    bot.py           SAVESBot; ApprovalView buttons; _finalize() writes the note
    approval.py      PendingApproval + PendingApprovalsStore (atomic JSON)
    notifications.py send_approval_request / send_log / send_alert
  notes/
    formatter.py     format_note() → per-note_type templates
    file_manager.py  write_note() atomic; move_note() SHA256-verified (no deletes)
  utils/
    url_parser.py    extract_urls / normalize_url / detect_platform / get_source_key
    file_io.py       read_inbox / remove_url_from_inbox (atomic)
    preferences.py   PreferencesStore (learned folder routing)
    cookie_checker.py cookie mtime/expiry checks
    vault_scanner.py scan_saves_folders (existing folders → Claude context)
    retry.py         with_retry() decorator (defined; NOT yet wired in)
scripts/
  process_one.py     run the full pipeline for ONE url; print note (--dry-run to skip write)
  test_connection.py smoke test: Anthropic API, Discord, Reddit JSON, paths
  whisper_server.py  Flask server — runs on the WORKSTATION, not the NAS
  refresh_cookies.py browser cookie export instructions
docker/
  Dockerfile         python:3.11-slim + ffmpeg + chromium + playwright (+ optional whisper target)
  docker-compose.yml volumes: vault, media, cookies, config, logs, state files
config.yaml          all configuration
.env.example         ANTHROPIC_API_KEY, DISCORD_BOT_TOKEN
cookies/             instagram.txt / tiktok.txt / facebook.txt (gitignored)
docs/                PLAN.md, ROADMAP.md, HANDBOOK.md
CLAUDE.md            session orientation (auto-loaded by Claude Code)
```

---

## 5. Dependencies

**Python (`requirements.txt`) — purpose:**
| Package | Why |
|---|---|
| `anthropic` | Claude API (analysis, OCR, fact-check, NL edit) |
| `discord.py` | approval bot + notifications |
| `yt-dlp` | universal video/audio download (YouTube/TikTok/IG/FB/Reddit video) |
| `gallery-dl` | Instagram carousels / galleries |
| `instaloader` | Instagram metadata fallback |
| `playwright` | headless Chromium for web articles + lazy-image scroll |
| `trafilatura` | primary web-article → Markdown extractor (headings/links/images) |
| `readability-lxml` | fallback article parser |
| `watchdog` | watches `00 - FILE.md` for new URLs |
| `pyyaml` | config parsing |
| `python-dotenv` | `.env` loading |
| `aiofiles` | async file I/O |
| `requests` | HTTP (Discord, Whisper, Reddit JSON) |
| `flask` | the Whisper server (`scripts/whisper_server.py`) |
| `pillow-heif` | convert iPhone HEIC/HEIF images to JPG |

**Whisper host (`requirements-whisper.txt`):** `faster-whisper` (CTranslate2; fast on CPU).

**External (not pip):** `ffmpeg` (frame extraction, HEIC convert, muxing), `chromium`
(Playwright; `playwright install chromium`), the Whisper HTTP server on the workstation, and SMB
access to the NAS vault + media shares.

---

## 6. Build From Scratch

### Local dev (Windows workstation)
```bash
git clone <repo> C:\DEV\Apps\SAVES\SAVES_app
cd C:\DEV\Apps\SAVES\SAVES_app
python -m venv .venv && .venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium
copy .env.example .env          # fill ANTHROPIC_API_KEY, DISCORD_BOT_TOKEN
# edit config.yaml paths for Windows (N:/ SMB mounts) if needed
```
Cookies: export with the "Get cookies.txt LOCALLY" browser extension into
`cookies/instagram.txt`, `cookies/tiktok.txt`, `cookies/facebook.txt`.

Whisper server (separate terminal, stays running while you process videos):
```bash
python scripts\whisper_server.py --model large-v3-turbo
```

Smoke test, then a single URL, then the full watcher:
```bash
python scripts\test_connection.py
python scripts\process_one.py "https://www.reddit.com/r/..."   # add --dry-run to not write
python -m src.main
```

### Production (Synology NAS, Docker)
```bash
cd docker
docker-compose up --build
```
`.env` must exist with `ANTHROPIC_API_KEY` + `DISCORD_BOT_TOKEN`. Vault and media are
volume-mounted. The container reaches the workstation Whisper server over the LAN/Tailscale.

---

## 7. How Key Subsystems Work

- **Two-stage AI (cost saver).** Stage 1: a cheap vision model (`vision.ocr_model`,
  Haiku) reads all image slides/frames → text. Stage 2: the capable model (`ai.model`, Opus)
  analyzes **text only** (no images → far cheaper). If `ocr_model` is unset, falls back to one
  combined call. (`ai/claude_client.py`)
- **Vision frames.** Videos use ffmpeg scene-change detection (a frame per content change, so
  rolling burned-in captions are captured), then tile frames into a 2×2 **montage** — a vertical
  reel frame already maxes the image-token cap, so 4 frames per block cost the same as 1.
  (`media/vision.py`; knobs `max_video_frames`, `frame_scene_threshold`, `frame_grid`)
- **Web articles.** Playwright scrolls the page to trigger lazy images, strips image-wrapper CSS
  classes (so trafilatura's discard rules don't prune them), extracts clean Markdown, normalizes
  spurious indentation, prepends the og:image hero, and `localize_article_images()` downloads
  every inline image locally and rewrites links to `EmbedRelativeTo` blocks so the note survives
  the source going down. Vision/OCR is skipped for `generic` (text already structured).
- **Recipes (any platform).** When Claude extracts recipe fields from any note type, a `## Recipe`
  section is injected. (`notes/formatter.py`)
- **Fact-check.** Health/finance topics trigger a Sonnet pass with server-side web search (up to
  N rounds, progress-logged). Recipes skip the web-search loop (macro claims are low value) but
  still get a quick local safety pass. (`ai/claude_client.py`)
- **Prompt caching.** `cache_control: ephemeral` on system prompts + the fact-check first message
  so retries and back-to-back saves read the prefix ~90% cheaper.
- **Learned routing.** On approval, the final folder is saved in `preferences.json` keyed by
  source (`reddit:r/x`, `youtube:Channel`, `domain:cnbc.com`, …) and proposed next time.
- **Temperature cache.** Newer models reject `temperature`; the first 400 records the model in a
  set so it's never sent again that run. (`ai/claude_client.py`)

---

## 8. Configuration Reference (`config.yaml` highlights)

```yaml
paths:        { vault_root, saves_root, inbox_file: ".../00 - FILE.md", media_root }
transcription:{ mode: remote, remote_url: "http://192.168.1.90:5000/transcribe", model: large-v3-turbo }
vision:       { ocr_model: claude-haiku-4-5, max_images: 20, max_video_frames: 8,
                frame_scene_threshold: 0.3, frame_grid: 2 }
ai:           { model: claude-opus-4-8, max_tokens: 4096 }
fact_checking:{ model: claude-sonnet-4-6, include_images: false, web_search_topics: [health, finance] }
discord:      { auto_approve_on_timeout: false, auto_approve_timeout_hours: 48 }
credentials:  { keys: [ANTHROPIC_API_KEY, DISCORD_BOT_TOKEN] }
```

**Model routing:** OCR = Haiku, analysis = Opus, fact-check = Sonnet. Use the exact model IDs in
config (e.g. `claude-opus-4-8`); do not append date suffixes.

---

## 9. Operations / Runbook

- **Start a processing session:** ensure the Whisper server is up, then `python -m src.main`
  (or deploy via Docker on the NAS).
- **Test one URL fast:** `python scripts/process_one.py "<url>"` (`--dry-run` to skip writing).
- **Refresh cookies (~every 3–4 weeks):** re-export via the browser extension; the daily cookie
  check alerts to `#SAVES-alerts` ahead of expiry.
- **Discord:** server "Bora's AI Ops"; channels `#SAVES-approvals`, `#SAVES-logs`, `#SAVES-alerts`.
- **Logs:** `logs/` (append-only; no rotation deletes — watch disk).

---

## 10. Troubleshooting

| Symptom | Likely cause / fix |
|---|---|
| Note has no media | download failed (cookies expired? URL private?) — check `#SAVES-alerts` |
| Web article is a wall of text | trafilatura missed; readability fallback used — inspect the page DOM |
| Web article missing images | lazy-load/discard-class issue — see `generic.py` scroll + class-strip |
| Transcript missing on a video | Whisper server down/unreachable from the container |
| Repeated "model rejects temperature" | stale build before the temp-cache fix |
| Fact-check looks frozen | normal: web-search rounds are slow; progress is logged |
| `git` 403 in the web sandbox | proxy token expired — use the GitHub API/tarball method (see CLAUDE.md) |

---

## 11. Fill-In Section (lives in the owner's head — complete these)

> Capturing these is what makes this doc a true recreate guide. Replace each TODO.

- **NAS:** model = _TODO_; SMB hostname = _TODO_; volumes = `/volume1/NAS/OBSIDIAN`,
  `/volume1/NAS/MEDIA/SAVES`; app dir = _TODO_.
- **Vault layout:** SAVES folder tree under `Remote Vault/SAVES/` = _TODO_; inbox = `00 - FILE.md`.
- **Discord:** server = "Bora's AI Ops"; channel IDs = _TODO_; bot invite scopes/permissions = _TODO_.
- **Cookies:** which account backs each of instagram/tiktok/facebook = _TODO_; refresh cadence = ~3–4 wks.
- **Whisper:** host = `192.168.1.90:5000`; start cmd = `whisper_server.py --model large-v3-turbo`;
  firewall rule NAS→workstation:5000 = _TODO_; restart procedure = _TODO_.
- **Anthropic:** org/account = _TODO_; monthly budget/limit = _TODO_ (raise at claude.ai/settings/usage).
- **Mobile:** iOS Shortcut config = _TODO_; Android HTTP Shortcuts config = _TODO_.
- **Performance baselines:** typical URL→card time per platform = _TODO_.

---

## 12. Changelog (how we got here)

- Built the full pipeline from the skeleton per `docs/PLAN.md` (extractors, media, AI, Discord,
  notes, watcher/queue/processor).
- Switched web extraction from readability to **trafilatura**; added local image archival,
  lazy-load scroll, discard-class fix, Markdown normalize, hero-image restore.
- Added **recipe extraction across all platforms**; reordered the recipe note template.
- Reworked video frames to **scene-change detection + 2×2 montage** (4× caption coverage, same cost).
- Added **temperature-rejection cache**, **fact-check progress logging**, and a **recipe
  web-search skip**.
- Audited the system (Jun 2026): confirmed the core loop is feature-complete; defined the
  path-to-production phases now tracked in `docs/ROADMAP.md`.
