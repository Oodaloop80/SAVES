# SAVES — Roadmap to Production

> Live checklist. Update boxes as items land; this is the "where are we" anchor across chats.
> Full reasoning lives in `docs/PLAN.md`. Orientation lives in `CLAUDE.md`. Recreate/maintain
> details live in `docs/HANDBOOK.md`.

**Current phase:** Phase 3 (runtime token efficiency). Phases 1–2 complete (one optional
Phase 2 item — persist NL-edit sessions across restart — deferred).

**Status in one line:** Core pipeline is feature-complete and in active dev use. Remaining work
is hardening, deployment, mobile sharing, runtime cost tuning, and a frictionless save loop.

---

## Decisions locked (change anytime)

- **Dev surface:** Claude Code **CLI** on the desktop (not web).
- **Runtime cost:** cut cost with **real-time** levers first (model routing, `effort`, prompt
  caching, fact-check gating) so results stay instant during tuning. The **Batch API** (50%
  off but async — no instant results) is **deferred to a final phase**, adopted only once
  save quality is dialed in and instant feedback is no longer needed.
- **Docs system:** `CLAUDE.md` (auto-loaded orientation) + `docs/HANDBOOK.md` (recreate/maintain)
  + `docs/ROADMAP.md` (this file). Update the relevant doc in the same commit as each change.

---

## Phase 0 — Finalize docs & plan  *(completed)*
- [x] Update `CLAUDE.md` with all recent work
- [x] Write `docs/PLAN.md` (strategy + phased plan)
- [x] Write `docs/HANDBOOK.md` (recreate & maintain)
- [x] Write `docs/ROADMAP.md` (this file)
- [x] Commit all four docs to the repo (commit `9e7ffb6`)

## Phase 1 — CLI setup & frictionless loop  *(completed)*
- [x] `git pull`; confirm remote + clean tree on the desktop
- [x] `.claude/settings.json`: SessionStart hook (sync + print current ROADMAP phase) + Bash
      allowlist (`python`, `git`, `ffmpeg`, `yt-dlp`) to cut permission prompts
- [x] `.claude/commands/save.md`: the `/save <url>` loop (process → auto-QA → commit/push)
- [x] Minimal `pyproject.toml` (ruff) + one smoke test for lint/test feedback
- [x] Re-run review cheaply: single-pass `/code-review` over the working tree (replaces the
      multi-agent review that got cut off by the spend limit); log fixes below
- [x] Verify: `scripts/test_connection.py` green; `process_one.py` good on one URL per platform

## Phase 2 — Harden the existing pipeline  *(completed — one optional item deferred)*
- [x] Startup config validation (fail fast on missing paths/channels — `utils/validation.py`,
      called from `main`; keys stay with `load_credentials`). Dir existence is a soft warning.
- [x] Graceful missing-inbox handling in `FileWatcher` (skip + warn if the watch dir is
      absent instead of crashing at startup)
- [x] Extraction timeout (`asyncio.timeout(processing.extract_timeout_seconds)` around
      `extractor.extract()`; timeouts mark_failed + alert, queue moves on)
- [x] Claude API backoff + wire `utils/retry.py`. Claude backoff = Anthropic SDK
      `max_retries` (ai.max_retries, honors Retry-After — better than a fixed-delay wrapper
      for HTTP). `utils/retry.py` is wired into the remote-transcription POST (Whisper server
      warmup), which the SDK doesn't cover; this also puts `processing.retry_attempts/
      retry_delay_seconds` to use. (Extractor/download retry deferred — needs transient-vs-
      permanent classification so it doesn't retry deleted-URL 404s.)
- [x] Crash-safe `_finalize` ordering; dedup uses `processing_state.json` as source of truth.
      Idempotency guard at the top of `_finalize` short-circuits when the URL is already
      `done` (double-click / restored-message re-approval → no duplicate note); `mark_done`
      is recorded immediately after the note hits disk, before the slower cleanup.
- [x] **Restart orphans approval buttons** (review Finding 2): fixed. `setup_hook` now
      re-registers a persistent view per already-sent item, bound to its real
      `discord_message_id` (via `add_view(view, message_id=…)`), so button clicks after a
      restart carry the item's real pending ID. Placeholder views removed.
- [ ] (optional) persist NL-edit sessions across bot restart

## Phase 3 — Runtime token efficiency  *(real-time only — instant results preserved)*
> Strategy under review with Bora (2026-07-01). Batch API intentionally excluded here — see
> Phase 6. Attack cost in priority order of where the money actually goes:
> **fact-check web-search loop ≫ Opus analysis ≫ Haiku OCR.**
- [ ] Cache the travel-location check (`verifier.py`) — zero-risk quick win
- [ ] Right-size `max_tokens` (analysis 8192→4096, OCR 8192→6000) with truncation watch
- [ ] Narrow `web_search_topics` — drop `finance`, keep `health`
- [ ] Trim `SYSTEM_PROMPT` folder examples; make recipe/travel rules conditional (A/B on ~50 saves)
- [ ] Conditional OCR — skip Haiku OCR for pure-photo posts
- [ ] (candidate) `effort: medium` on Opus analysis + Sonnet fact-check — untapped, big lever
- [ ] (candidate) Cap `max_searches` 5→3 and/or gate fact-check to on-demand (Discord button)
- [ ] (candidate) A/B Opus→Sonnet for the analysis stage (40% cheaper, quality test needed)

## Phase 4 — Deploy, mobile, live-test
- [ ] End-to-end live Discord run (paste → approve → note written) for every button
- [ ] Docker deploy to NAS (`docker-compose up --build`); verify mounts + vault write + Whisper reach
- [ ] iOS share shortcut (Obsidian Actions URI) → `00 - FILE.md`
- [ ] Android share (HTTP Shortcuts → SMB append via Tailscale) → `00 - FILE.md`
- [ ] Whisper runbook into HANDBOOK (start cmd, host/port, firewall, restart)

## Phase 5 — Ongoing tuning
- [ ] Feed real URLs via `/save`; refine quality + routing; let `preferences.json` learn
- [ ] Keep docs current per change; targeted single-agent reviews on risky edits only

## Phase 6 — Cost optimization (post-stabilization)  *(gated: only once quality is dialed in)*
> Deferred here on purpose (Bora, 2026-07-01): batching removes instant results, which would
> cripple the tweaking/testing loop. Do NOT start until saves are consistently high-quality
> and instant feedback is no longer needed.
- [ ] Batch API (night-time): route the two pre-approval Claude calls (OCR + analysis) through
      Message Batches for 50% off; add a "pending batch" state to `processing_state.json` and a
      poller that resumes when the batch completes. Fact-check can batch too once stable.

---

## Verified review fixes (fill in as Phase 1 review runs)
| # | File:line | Issue | Severity | Status |
|---|-----------|-------|----------|--------|
| 1 | `queue_manager.py:77` | Dedup keyed on raw inbox URL, but `ProcessingState` is keyed on the normalized URL (tracking params stripped) → social links re-enqueue after restart → duplicate notes | High | ✅ Fixed (normalize in `enqueue_from_file`) |
| 2 | `bot.py:200-201` | Persistent views registered with `pending_id="__placeholder__"` → after restart, already-sent approvals route to placeholder and become unapprovable | High | ✅ Fixed (per-message `add_view(view, message_id=…)` in `setup_hook` carries the real pending id) |
| 3 | `file_io.py:16` | `remove_url_from_inbox` matches by substring → a URL that's a prefix of another inbox URL removes both | Low | Noted |
| 4 | `file_manager.py:110` | `move_note` same-volume `os.rename` overwrites an existing destination (no conflict resolution) | Low | Noted |
| 5 | `test_connection.py` / `process_one.py` | Emoji/Unicode `print()` crashes on Windows (cp1252) — both CLI scripts unrunnable on the dev workstation | High | ✅ Fixed (force UTF-8 on stdout/stderr) |
| 6 | `test_connection.py:61` | Reddit check used a bot UA + no cookies → Cloudflare 403 false negative while the real extractor (browser UA + reddit.txt) succeeds | Med | ✅ Fixed (test via extractor session) |
