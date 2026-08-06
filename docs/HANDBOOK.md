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
appended to one Obsidian inbox file (`0 - INBOX/SAVES.md`). SAVES watches that file, extracts the
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

> Full architecture reference with rendered diagrams (deployment, components, sequence,
> URL state machine), plus command + config cheat sheets: **`docs/ARCHITECTURE.md`**.

```
0 - INBOX/SAVES.md
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
| `watchdog` | watches the inbox file (`0 - INBOX/SAVES.md`) for new URLs |
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
# Remote = self-hosted Forgejo on the NAS (LAN-only). Requires the step-ca root CA
# trusted by Git-for-Windows and a PAT as the password — see docs/FORGEJO.md Phase 11-12.
git clone https://192.168.1.201:3443/<user>/SAVES.git C:\DEV\Apps\SAVES\SAVES_app
cd C:\DEV\Apps\SAVES\SAVES_app
python -m venv .venv && .venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium
copy .env.example .env          # fill ANTHROPIC_API_KEY, DISCORD_BOT_TOKEN
copy config.local.yaml.example config.local.yaml   # then edit: YOUR Windows paths
```
**Never edit the paths in `config.yaml` itself** — it holds canonical container paths
(`/vault`, `/media`, `/app/state`) shared by every deployment. Bare-metal machines override
them in the gitignored `config.local.yaml` (deep-merged over `config.yaml` at load;
`src/config.py`). The current DEV workstation uses a fully local test vault
(`C:/DEV/Apps/SAVES/OBSIDIAN`) and local media dir (`C:/DEV/Apps/SAVES/MEDIA`), with state
JSONs at the repo root.
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

### Production (Synology NAS, Docker) — or ANY other Docker host
```bash
cd docker
cp .env.example .env            # host paths: VAULT_HOST, MEDIA_HOST, STATE_HOST, TZ
mkdir -p /volume1/docker/saves/state   # the STATE_HOST dir must exist before first up
docker-compose up --build -d
```
Two `.env` files, different jobs: repo-root `.env` = secrets (`ANTHROPIC_API_KEY`,
`DISCORD_BOT_TOKEN`, injected into the container); `docker/.env` = **host paths only**
(compose reads it for `${VAR}` substitution). `config.yaml` never changes between hosts —
moving PROD to a new machine means filling in a new `docker/.env`, nothing else.
The container reaches the workstation Whisper server over the LAN/Tailscale
(`transcription.remote_url`).

---

## 7. How Key Subsystems Work

- **Two-stage AI (cost saver).** Stage 1: a cheap vision model (`vision.ocr_model`,
  Haiku) reads all image slides/frames → text. Stage 2: the capable model (`ai.model`, Sonnet 4.6)
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

## 8. Configuration Reference (`config.yaml`)

`config.yaml` is the single source of truth for runtime behavior; `.env` holds only the two
secrets. The blocks below are the **current production values** with the *why* for the
non-obvious ones. Use exact model IDs (e.g. `claude-sonnet-4-6`) — never append date suffixes.

**paths** — where SAVES reads and writes. **Canonical container paths — identical on every
deployment; never machine-specific.** Docker maps the host's real layout onto them via
`docker/.env` (`VAULT_HOST`/`MEDIA_HOST`/`STATE_HOST`); bare-metal dev overrides them in
`config.local.yaml` (gitignored, deep-merged at load).
```yaml
vault_root:   "/vault"
saves_root:   "/vault/SAVES"
inbox_file:   "/vault/0 - INBOX/SAVES.md"   # the watched file
media_root:   "/media"
cookies_dir:  "cookies"   ·   logs_dir: "logs"
state_file:  "/app/state/processing_state.json"          # dedup / done-URLs
pending_approvals_file: "/app/state/pending_approvals.json"   # restart-safe Discord approvals
# preferences.file (its own block) also lives in /app/state/
```
PROD host values (NAS) live in `docker/.env`: vault `/volume1/NAS/OBSIDIAN/Remote Vault`,
media `/volume1/NAS/MEDIA/SAVES`, state `/volume1/docker/saves/state`. DEV workstation values
live in `config.local.yaml`: local test vault `C:/DEV/Apps/SAVES/OBSIDIAN`, media
`C:/DEV/Apps/SAVES/MEDIA`, state JSONs at the repo root.
> The inbox moved from `SAVES/00 - FILE.md` to `0 - INBOX/SAVES.md` (docs updated 2026-07-04).

**watcher / processing** — the serial pipeline.
```yaml
watcher.debounce_seconds: 3         # coalesce rapid saves before enqueue
processing:
  concurrent_downloads: 1           # one URL at a time
  retry_attempts: 3                 # remote-transcription retry (utils/retry.py)
  retry_delay_seconds: 30           # exponential backoff base: 30s, 60s, …
  skip_duplicates: true             # already-saved URL → posts a notice + clears line, no reprocess
  follow_profile_recipes: true      # "recipe in bio" → follow the bio link (non-fatal)
  extract_timeout_seconds: 180      # hard cap on one extractor.extract()
```

**media**
```yaml
download_video: true   ·   download_images: true   ·   max_video_size_mb: 500
video_quality: "bestvideo[height<=1080]+bestaudio/bestvideo+bestaudio/best"   # TikTok overrides this
```

**transcription** — remote Whisper (full runbook in §9.1).
```yaml
enabled: true
mode: "remote"                      # POST audio to the workstation
remote_url: "http://192.168.1.90:5000/transcribe"
model: "large-v3-turbo"   ·   language: "en"
skip_if_captions_available: true    ·   max_duration_minutes: 30
```

**vision** — Stage-1 OCR.
```yaml
enabled: true
max_images: 20                      # IG carousels cap at 20 slides
ocr_model: "claude-haiku-4-5"       # cheap model reads slides/frames → text
max_video_frames: 8                 # frames extracted per video (strategy in §7)
skip_platforms: []                  # youtube always skipped automatically
```

**ai** — Stage-2 analysis (Phase 3 tuned).
```yaml
model: "claude-sonnet-4-6"          # A/B verdict 2026-07-01: Sonnet ≈ Opus at ~½ cost
max_tokens: 8192                    # room for full multi-slide OCR + all JSON fields — kept high on
                                    #   purpose: not billed unless filled; lowering risks truncating
                                    #   a long multi-recipe carousel's JSON
temperature: 0.3                    # Sonnet accepts it (Opus 4.8 rejected it + was auto-stripped)
effort: "medium"                    # thinking depth for analysis + local fact-check; auto-skipped for
                                    #   Haiku OCR (rejects effort), not sent to the web-search loop
max_content_chars: 20000
max_retries: 4                      # Anthropic SDK transient-error retries (honors Retry-After)
```

**platforms** — per-platform knobs.
- **reddit** — `top_comments_count: 5`, includes OP's top-level comments; public JSON API, no creds.
- **instagram** — `delay_seconds: 4`; cookies, `cookie_expiry_days: 21`, warn 7 days ahead.
- **tiktok** — `use_rich_caption: true` (verbatim caption from rehydration `contents[]`; legacy
  `use_tdk_caption` still honoured); `no_watermark: true`; `video_quality` targets muxed **H.264**
  (`best[vcodec=h264]/…`) — Obsidian/Electron can't decode H.265, and TikTok is portrait 720p.
- **facebook** — `delay_seconds: 7`; cookies, `cookie_expiry_days: 30`.
- **youtube** — `download_video: false` (captions/subtitles only), `subtitle_language: "en"`.
- **generic** — `playwright_timeout_seconds: 30`, `wait_for_network_idle`, auto-click cookie banners.

**discord**
```yaml
channel_approvals: "SAVES-approvals"   ·   channel_log: "SAVES-logs"   ·   channel_alerts: "SAVES-alerts"
auto_approve_on_timeout: false   ·   auto_approve_timeout_hours: 48
```

**notes**
```yaml
include_metadata_section: true   ·   collapse_transcript: true   # long transcripts → collapsed callout
date_format: "%Y-%m-%d"   ·   filename_max_length: 60   ·   tags_min: 10   ·   tags_max: 20
```

**fact_checking** — Phase 3 gated (see §7).
```yaml
enabled: true
model: "claude-sonnet-4-6"          # falls back to ai.model if omitted
topics: [health, political, finance]
web_search: true
web_search_topics: [health, finance, political]   # eligible for web search — but ONLY via the
                                                   #   on-demand "Deep fact-check" button; the
                                                   #   on-arrival pass is LOCAL only
max_searches: 5
max_searches_by_topic: { health: 6, finance: 3, political: 1 }   # highest applicable cap wins;
                                                                 #   recipes/food always local-only
max_tokens: 6000
include_images: false               # OCR already captured image text; raw pixels would double-bill
jurisdiction: "Charlotte, NC (Mecklenburg County, North Carolina, USA)"   # tax/legal claim validity
```

**travel_verification / credentials**
```yaml
travel_verification.enabled: true   # verifier.py location check; fires only on travel posts
credentials.keys: [ANTHROPIC_API_KEY, DISCORD_BOT_TOKEN]   # validated at startup; Reddit needs none
```

**Model routing at a glance:** OCR = **Haiku 4.5** → analysis = **Sonnet 4.6** → fact-check =
**Sonnet 4.6**. (`ai.model` was Opus 4.8 pre-Phase-3; the A/B verdict switched it to Sonnet.)

---

## 9. Operations / Runbook

- **Start a processing session:** ensure the Whisper server is up, then `python -m src.main`
  (or deploy via Docker on the NAS).
- **Test one URL fast:** `python scripts/process_one.py "<url>"` (`--dry-run` to skip writing).
- **Refresh cookies (~every 3–4 weeks):** re-export via the browser extension; the daily cookie
  check alerts to `#SAVES-alerts` ahead of expiry.
- **Discord:** server "Bora's AI Ops"; channels `#SAVES-approvals`, `#SAVES-logs`, `#SAVES-alerts`.
- **Logs:** `logs/` (append-only; no rotation deletes — watch disk).

### 9.1 Whisper transcription server (workstation)

Transcription is offloaded to the workstation (Ryzen 9 7950X) because the NAS is too weak for
it. With `transcription.mode: remote`, the NAS container POSTs each audio/video file to the
workstation over the LAN; the workstation runs faster-whisper and returns the text. The NAS
never transcribes locally.

- **Host / endpoints:** `192.168.1.90:5000` — `POST /transcribe` (multipart field `audio`,
  optional form field `language`) and `GET /health` (liveness). Binds `0.0.0.0:5000`.
- **Model:** `large-v3-turbo` (int8 on CPU). Loaded once at startup, so the *first* request is
  not slow; startup is where the model download/load happens.
- **Serial by design:** the server runs `threaded=False`, so it transcribes one file at a time —
  which matches the processor (one URL at a time). Don't expect concurrency.

**Install (one-time, on the workstation):**
```bash
pip install -r requirements-whisper.txt   # faster-whisper (CTranslate2)
# ffmpeg must be on PATH (already required for the main app)
```

**Start (foreground terminal that must stay open while processing videos):**
```bash
python scripts\whisper_server.py --model large-v3-turbo
# defaults: --host 0.0.0.0  --port 5000  --device cpu  --compute-type int8
```

**Verify it's up (from the workstation, or from the NAS to prove reachability):**
```bash
curl http://192.168.1.90:5000/health
# → {"status":"ok","model":"large-v3-turbo","device":"cpu","compute_type":"int8"}
```

**Config that must agree (`config.yaml`):**
```yaml
transcription:
  mode: "remote"
  remote_url: "http://192.168.1.90:5000/transcribe"
  model: "large-v3-turbo"
```

**Client behavior (what the NAS does):** `transcriber._transcribe_remote()` POSTs with a **300 s**
timeout and is **retry-wrapped** — `processing.retry_attempts` (default 3) attempts with
`processing.retry_delay_seconds` (default 30 s) backoff, re-opening the file fresh each attempt.
If all attempts fail the note is still written, just **without a transcript** (non-fatal).

**Firewall (NAS → workstation:5000).** Windows blocks inbound 5000 by default. Add a LAN-scoped
inbound rule once, in an **elevated** shell (documented here; run it yourself — this session does
not execute it):
```powershell
netsh advfirewall firewall add rule name="SAVES Whisper 5000" ^
  dir=in action=allow protocol=TCP localport=5000 remoteip=192.168.1.0/24
```
Scope `remoteip` to the LAN (or the NAS's exact IP) rather than `any`.

**Restart / keep-alive.** Today it's a **manual foreground process** — if the terminal closes or
the workstation reboots, re-run the start command. To make it survive reboots, pick one (owner's
choice, see §11): (a) **Task Scheduler** task "At log on" running the start command, or
(b) **NSSM** (`nssm install SAVES-Whisper`) to run it as a real Windows service.

**IP stability.** `remote_url` hard-codes `192.168.1.90`. If the workstation's DHCP lease changes
the IP, transcription silently starts failing (retries, then no transcript) — give the
workstation a **static/reserved IP** on the router, or update `remote_url` when it moves.

**When it's down:** videos archive with no transcript; the symptom + fix is in §10
("Transcript missing on a video").

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
| `git` TLS error against the forge | step-ca root not in the trust store — `FORGEJO.md` Phase 11. **Never** `http.sslVerify=false` |
| `git` 401 against the forge | PAT expired/wrong scope — regenerate (`FORGEJO.md` §12.1); password = the token, not your account password |
| Can't reach the forge at all | LAN-only, no port-forward by design — you must be on `192.168.1.0/24` |

---

## 11. Fill-In Section (lives in the owner's head — complete these)

> Capturing these is what makes this doc a true recreate guide. Replace each TODO.

- **NAS:** model = **DS1621+**; LAN IP = **192.168.1.201**; DSM 7.3.2-86009 U4;
  Container Manager 24.0.2-1606; SMB hostname = _TODO_; **RAM = 32 GB** (measured
  2026-08-06: 32071 MB total / ~29.8 GB available → `SAVES_MEM_LIMIT=4g`);
  `/volume1` = 37 TB, 24 TB free; SAVES app dir = `/volume1/docker/saves/app`.
- **SAVES service account:** `sa_saves` — **UID = _TODO_** (read it from `id sa_saves`; do
  **not** assume 1031), GID 65536 (`service accounts`). This UID must appear in
  `docker/.env` as `SAVES_UID` and own every directory SAVES writes. Ownership map +
  rationale: `PROD_ROLLOUT.md` §1.6.
- **Vault + media paths:** `VAULT_HOST` = _TODO — confirm the real path_,
  `MEDIA_HOST` = _TODO_. ⚠️ As of 2026-08-06 neither `/volume1/NAS/OBSIDIAN/Remote Vault`
  nor `/volume1/NAS/MEDIA/SAVES` existed on the NAS — they are created in rollout step 0c
  with owner `sa_saves:users` and mode `2775`.
- **Git forge (Forgejo, on the same NAS):** `https://192.168.1.201:3443/`; project dir
  `/volume1/docker/forgejo`; runs as UID 1030 (`sa_forgejo`) : GID 65536. Full build =
  `docs/FORGEJO.md`. Owner TODOs: Forgejo username = _TODO_; **step-ca cert expiry date +
  renewal mechanism** = _TODO_ (a 24 h default would expire the forge daily — `FORGEJO.md`
  §3A.1/3A.3) — **✅ resolved: issued 2026-07-31, expires 2027-09-01 (~397 days, just under
  the 398-day Apple ceiling). Set a renewal reminder for ~Aug 2027.** Root CA file =
  `vineyard-root-ca.crt`; PAT names in use (workstation / NAS-deploy / Claude Code) = _TODO_;
  **is `/volume1/docker/forgejo` in the backup set** = _TODO_ (⚠️ now the only copy of the
  repo — GitHub retired).
- **Vault layout:** SAVES folder tree under `Remote Vault/SAVES/` = _TODO_; inbox = `0 - INBOX/SAVES.md`.
- **Discord:** server = "Bora's AI Ops"; channel IDs = _TODO_; bot invite scopes/permissions = _TODO_.
- **Cookies:** which account backs each of instagram/tiktok/facebook = _TODO_; refresh cadence = ~3–4 wks.
- **Whisper:** full runbook now in **§9.1** (host/port, start, verify, config, firewall, restart, IP).
  Two owner decisions remain: is the LAN-scoped inbound firewall rule actually in place = _TODO_;
  which restart mechanism — manual / Task Scheduler / NSSM service = _TODO_.
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
