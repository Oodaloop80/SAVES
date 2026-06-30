# SAVES — Roadmap to Production

> Live checklist. Update boxes as items land; this is the "where are we" anchor across chats.
> Full reasoning lives in `docs/PLAN.md`. Orientation lives in `CLAUDE.md`. Recreate/maintain
> details live in `docs/HANDBOOK.md`.

**Current phase:** Phase 1 (CLI setup in progress).

**Status in one line:** Core pipeline is feature-complete and in active dev use. Remaining work
is hardening, deployment, mobile sharing, runtime cost tuning, and a frictionless save loop.

---

## Decisions locked (change anytime)

- **Dev surface:** Claude Code **CLI** on the desktop (not web).
- **Runtime cost:** **night-time Batch API** for the two pre-approval Claude calls; daytime real-time.
- **Docs system:** `CLAUDE.md` (auto-loaded orientation) + `docs/HANDBOOK.md` (recreate/maintain)
  + `docs/ROADMAP.md` (this file). Update the relevant doc in the same commit as each change.

---

## Phase 0 — Finalize docs & plan  *(completed)*
- [x] Update `CLAUDE.md` with all recent work
- [x] Write `docs/PLAN.md` (strategy + phased plan)
- [x] Write `docs/HANDBOOK.md` (recreate & maintain)
- [x] Write `docs/ROADMAP.md` (this file)
- [x] Commit all four docs to the repo (commit `9e7ffb6`)

## Phase 1 — CLI setup & frictionless loop  *(first desktop session)*
- [x] `git pull`; confirm remote + clean tree on the desktop
- [x] `.claude/settings.json`: SessionStart hook (sync + print current ROADMAP phase) + Bash
      allowlist (`python`, `git`, `ffmpeg`, `yt-dlp`) to cut permission prompts
- [x] `.claude/commands/save.md`: the `/save <url>` loop (process → auto-QA → commit/push)
- [x] Minimal `pyproject.toml` (ruff) + one smoke test for lint/test feedback
- [x] Re-run review cheaply: single-pass `/code-review` over the working tree (replaces the
      multi-agent review that got cut off by the spend limit); log fixes below
- [ ] Verify: `scripts/test_connection.py` green; `process_one.py` good on one URL per platform

## Phase 2 — Harden the existing pipeline
- [ ] Startup config validation (fail fast on missing paths/keys/channels)
- [ ] Graceful missing-inbox handling in `FileWatcher`
- [ ] Extraction timeout (`asyncio.timeout` around `extractor.extract()`)
- [ ] Claude API backoff/circuit-breaker (wire the unused `utils/retry.py`)
- [ ] Crash-safe `_finalize` ordering; dedup uses `processing_state.json` as source of truth
- [ ] **Restart orphans approval buttons** (review Finding 2): `setup_hook` registers
      persistent views with `pending_id="__placeholder__"` (`bot.py:200-201`), so after a
      restart every already-sent approval routes to the placeholder → `get_by_id` returns
      None → "already processed" and the item is stuck. `_restore_pending` only re-sends
      items with `discord_message_id is None`. Fix: encode the real pending ID in each
      button's `custom_id` (dynamic items) instead of a shared placeholder.
- [ ] (optional) persist NL-edit sessions across bot restart

## Phase 3 — Runtime token efficiency
- [ ] Cache the travel-location check (`verifier.py`) — zero-risk quick win
- [ ] Right-size `max_tokens` (analysis 8192→4096, OCR 8192→6000) with truncation watch
- [ ] Narrow `web_search_topics` — drop `finance`, keep `health`
- [ ] Trim `SYSTEM_PROMPT` folder examples; make recipe/travel rules conditional (A/B on ~50 saves)
- [ ] Conditional OCR — skip Haiku OCR for pure-photo posts
- [ ] Batch API (night-time): route OCR + analysis through Message Batches; "pending batch" state

## Phase 4 — Deploy, mobile, live-test
- [ ] End-to-end live Discord run (paste → approve → note written) for every button
- [ ] Docker deploy to NAS (`docker-compose up --build`); verify mounts + vault write + Whisper reach
- [ ] iOS share shortcut (Obsidian Actions URI) → `00 - FILE.md`
- [ ] Android share (HTTP Shortcuts → SMB append via Tailscale) → `00 - FILE.md`
- [ ] Whisper runbook into HANDBOOK (start cmd, host/port, firewall, restart)

## Phase 5 — Ongoing tuning
- [ ] Feed real URLs via `/save`; refine quality + routing; let `preferences.json` learn
- [ ] Keep docs current per change; targeted single-agent reviews on risky edits only

---

## Verified review fixes (fill in as Phase 1 review runs)
| # | File:line | Issue | Severity | Status |
|---|-----------|-------|----------|--------|
| 1 | `queue_manager.py:77` | Dedup keyed on raw inbox URL, but `ProcessingState` is keyed on the normalized URL (tracking params stripped) → social links re-enqueue after restart → duplicate notes | High | ✅ Fixed (normalize in `enqueue_from_file`) |
| 2 | `bot.py:200-201` | Persistent views registered with `pending_id="__placeholder__"` → after restart, already-sent approvals route to placeholder and become unapprovable | High | ⏳ Deferred to Phase 2 (see item above) |
| 3 | `file_io.py:16` | `remove_url_from_inbox` matches by substring → a URL that's a prefix of another inbox URL removes both | Low | Noted |
| 4 | `file_manager.py:110` | `move_note` same-volume `os.rename` overwrites an existing destination (no conflict resolution) | Low | Noted |
