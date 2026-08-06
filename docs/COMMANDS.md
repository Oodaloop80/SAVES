# SAVES — Command Quick Reference

Every command SAVES exposes, in one scannable place. Full behavior, nuances, and recovery
paths live in `docs/USER_GUIDE.md` — this is just the cheat sheet.

> **Keep this current (MANDATORY):** any new slash command, button, or CLI script gets a row
> here in the **same commit** that adds it. See CLAUDE.md → Documentation discipline.

---

## Paste-to-save channel

| Surface | What it does |
|---|---|
| **`#SAVES-inbox`** (paste a URL, or POST via a Discord webhook from Android/Tasker) | Queues the URL like an inbox-file line — a provecho **creator** URL triggers `/crawl` instead. Bot reacts: ✅ queued · 🔁 duplicate · 🕸️ crawl · 🤔 no URL. Optional channel (`discord.channel_inbox`). Mobile one-tap setup: `docs/MOBILE_SHORTCUTS.md`. |

## Discord slash commands

Typed in the message box of any channel in the "Bora's AI Ops" server — type `/`, then pick the
command from the popup (sending it as plain text does nothing). If they don't appear, restart the
bot (commands register at startup).

| Command | What it does |
|---|---|
| `/tag add <tag> [item]` | Add a tag to a pending save. Autocompletes over your existing vault tags (usage counts, most-used first). `item` picks which pending save (default: newest). |
| `/forget <url>` | Drop a URL from `processing_state.json` (+ session dedup) so it can be saved again, remove any stale approval card for it, and re-scan the inbox so a URL still sitting there re-queues immediately. Autocompletes over saved history (`done`, `[failed]`, `[pending]`). Does **not** touch the vault. |
| `/crawl <creator-url>` | Discover + bulk-queue one provecho creator's recipes. URL must be a creator page (`…/platform/creator/<handle>`). **Per-creator scoped** — never crawls other creators. Posts a confirm card before queuing. Needs a captured login profile first (see `capture_session.py`). |
| `/queue` | Show the serial-review queue: which save is up now and how many are waiting behind it. Ephemeral. |

---

## Discord buttons

**Approval card** — on every pending save:

| Button | What it does |
|---|---|
| ✅ Approve | Write the note, save the learned folder preference, remove the URL from the inbox. |
| 🏷️ Add Tags | Modal — type tags (space/comma separated, no `#`). Add-only; near-duplicate check with a one-tap "Use existing" swap. |
| 🗑️ Remove Tags | Ephemeral view with one ✖ per tag (tap to remove). **↩ Undo All** restores the open-time list. |
| 📁 Change Path | Modal prepopulated with the current folder; force-normalized to ALL CAPS / forward slashes. |
| ✏️ NL Edit | Natural-language edit via a second Claude call (multi-action per instruction). |
| ⏭️ Skip | Defer this save: retract the card and re-queue the URL to the back so the next one shows now. Releases the serial gate. The skipped URL is reprocessed when it comes back around. |
| 🔍 Deep fact-check | On-demand web-searched claim verification. Shown only for posts with checkable topics not already deep-checked. |
| ⚠️ Approve + Include Warning | *Appears only when a fact-check or location flag exists* — approves and adds a `> [!warning]` callout to the note. |

**Duplicate notice** — in `#SAVES-approvals` when you paste an already-saved URL:

| Button | What it does |
|---|---|
| 🔁 Re-save | Forget the URL, retire the old note to `<name>.md.bak`, and requeue it (new note takes the original filename). |
| ✖ Dismiss | Keep the existing note; nothing changes. |

**Crawl confirmation** — the `/crawl` result card:

| Button | What it does |
|---|---|
| ✅ Queue | Enqueue the new recipes into the normal pipeline (paced by `crawl.rate_limit_seconds`); each gets its own approval card. |
| 📋 List | Show the new URLs without queuing anything (ephemeral — only you see it). |
| ✖ Cancel | Queue nothing. |

> **In-memory-only cards** (duplicate notice, crawl confirmation) go **dead after a bot restart** —
> re-trigger them (re-paste the URL, or run `/crawl` again).

---

## Command-line (run on the workstation)

**Operate:**

| Command | Purpose |
|---|---|
| `python src\main.py` | Start the whole system — inbox watcher, processor, and Discord bot. |
| `python scripts\whisper_server.py --model large-v3-turbo` | Start the local transcription server (the NAS container POSTs audio to it). |
| `python scripts\process_one.py "<url>" [--dry-run]` | Run the full pipeline for one URL and write the note. `--dry-run` prints only, no write. `--deep` runs the web-searched fact-check. |
| `python scripts\crawl_creator.py <creator-url> [--to-inbox]` | Crawl one creator: lists discovered/new/saved URLs (a **free, zero-token dry run** — stage 1 of `PROD_ROLLOUT.md` Part 4); `--to-inbox` appends new URLs to the inbox for a running pipeline. |
| `python scripts\capture_session.py <login-url> <name>` | Log in once to a gated site → saves a persistent browser profile (e.g. `… /platform/login provecho.co`). |
| `python scripts\test_connection.py` | Smoke-test connections: Anthropic API, Discord bot, Reddit JSON API. |
| `python scripts\refresh_cookies.py` | Guided instructions for re-exporting Instagram/TikTok/Facebook cookies. |
| `sh scripts/preflight_nas.sh` | NAS pre-deploy check (mounts / secrets / cookies / Whisper) — run on the NAS before `docker-compose up`. |

**Deploy (NAS, from `docker/`):** `cp docker/.env.example docker/.env` (set host paths) → create the
state dir → `docker-compose up --build -d`. Full runbook: `docs/DEPLOY_NAS.md`.

**Dev / regression tests** (standalone, no network / API / Discord):

- `python scripts\test_<name>.py` — each is an independent regression check (e.g. `test_caption_layout`,
  `test_tiktok_caption`, `test_tiktok_photo`, `test_nutrition_merge`, `test_media_paths`,
  `test_offsite_detection`, `test_offsite_recipe_extraction`, `test_html_to_markdown`,
  `test_bio_recipe_section`, `test_phase2`). Run them after touching the corresponding module.
- `python scripts\ab_compare.py` — A/B-compare the analysis stage across two models on one URL.
- `python scripts\diag_imgs.py` — one-off diagnostic dumping how a site structures in-article images.

---

## First-run / setup order

1. Fill `.env` (`ANTHROPIC_API_KEY`, `DISCORD_BOT_TOKEN`).
2. `python scripts\whisper_server.py --model large-v3-turbo` (transcription).
3. `python scripts\test_connection.py` (smoke test).
4. `python scripts\process_one.py "<url>" --dry-run` (first real run, no write).
5. `python src\main.py` (full system) → paste a URL into the inbox → approve in Discord.

Full checklist and deployment: CLAUDE.md → "First Run Checklist" and `docs/DEPLOY_NAS.md`.
