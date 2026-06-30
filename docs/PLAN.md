# SAVES — Path-to-Production Plan & Operating Model

## Context

SAVES (the URL → Obsidian-note archiving pipeline) is no longer greenfield. Three parallel
audits this session established that the **core loop is feature-complete**: watcher → queue →
processor → extract/download/transcribe/analyze/fact-check → Discord approval with 4 buttons →
`_finalize()` writes the note, saves the learned preference, removes the URL from the inbox, and
marks state done; pending approvals persist and re-send on restart; the cookie-expiry loop and
error alerts exist; everything runs on a single asyncio loop.

So "getting to production" is now **harden + deploy + wire mobile + live-test + tune** — not
"build features." At the same time the user is hitting two pain points this plan resolves:

1. **Development friction.** Working on Claude Code *web* means a stale-clone + manual-patch +
   git-proxy-403 dance every change. The session also just hit the **org monthly spend limit**
   during one ultracode review (~794K tokens, 25 agents) — concrete proof that expensive
   multi-agent orchestration is the wrong default for an iterate-and-tweak project.
2. **Runtime cost at scale.** The user makes 200+ saves and growing; per-save cost matters.

This plan covers: the **web → CLI move**, a **durable doc/memory system** that survives
compaction, the **phased roadmap to production**, **runtime token-efficiency levers**, and a
**low-friction "give Claude a save" loop** so the user does less and Claude does more.

> **Note on the spend limit:** the verify phase of the code review died on
> "hit your org's monthly spend limit." It is an account limit, not a bug — raise it at
> claude.ai/settings/usage, or wait for reset. Until cleared, do not spawn agents/workflows
> (they fail). All Phase-0 work below is local file edits + one patch, which do not need agents.

---

## Open Decisions (defaults chosen; flip any with one word)

| # | Decision | Default (recommended) | Why / tradeoff |
|---|----------|----------------------|----------------|
| 1 | Dev surface | **Move to CLI** | CLI sees local files, runs `process_one.py` against the real Whisper/cookies/vault, verifies output, and commits/pushes directly — kills the patch dance and is far cheaper per change. |
| 2 | Batch API | **Night-time batching** | Saves submitted overnight use the Batch API (~50% off OCR+analysis), preview ready by morning; daytime saves stay real-time (~2 min). Build in Phase 3. |
| 3 | This web session scope | **Finalize docs + plan only** | Capture roadmap + docs + review-fix list as one final patch; do all code on the CLI. Minimizes spend against the capped budget. |

---

## Recommended Order (the "what first" the user asked for)

1. **Phase 0 — finish here, in web (this session).** Write the durable docs + plan, ship ONE
   final patch. This is the last patch-apply you do.
2. **Phase 1 — move to CLI and remove all friction.** Apply the patch, wire git + `.claude/`,
   add the `/save` loop, push. From here Claude drives.
3. **Phase 2 — harden** the already-built pipeline (startup validation, timeouts, crash-safety).
4. **Phase 3 — runtime efficiency** (quick wins → prompt/OCR/topic trims → Batch API).
5. **Phase 4 — deploy + mobile + live-test** (Docker to NAS, iOS/Android share shortcuts,
   end-to-end Discord run).
6. **Phase 5 — ongoing tuning** via the `/save` loop with real URLs; docs stay current.

---

## Durable Memory: surviving compaction & new chats

**Principle:** anything that must survive lives in committed repo files, not in chat context.
Three docs, each with a job:

- **`CLAUDE.md`** — auto-loaded by Claude Code every session. Keep it lean (~250 lines):
  what/why, the two hard constraints, key commands, current phase, and links to the two docs
  below. (Already updated this session; ship it.)
- **`docs/HANDBOOK.md`** *(new)* — the comprehensive "recreate & maintain" doc the user wants:
  architecture, full dependency list + purpose, exact end-to-end setup (NAS + Windows + Discord
  + cookies + Whisper + mobile), how each subsystem works, a **"lives in your head" fill-in
  section** (NAS paths, Discord channel IDs, cookie accounts, vault folder layout, Whisper host),
  troubleshooting, and a short changelog of how we got here. Fold the two uploaded docs into this.
- **`docs/ROADMAP.md`** *(new)* — the phased to-production checklist below, as live checkboxes.
  Updated as items complete; this is the "where are we" anchor across chats.

**Enforcement (CLI):** a `SessionStart` hook (via the `session-start-hook` skill) prints the
current ROADMAP phase at the top of each session, and the `/save` + change-shipping habit updates
the relevant doc in the **same commit** as the change. Result: a new chat or a compaction reloads
full project state from disk in seconds.

---

## Phase 0 — Finalize docs + plan (this web session, no agents)

- [x] Update `CLAUDE.md` (done — trafilatura extraction, recipe-across-platforms, scene/montage
      frames, temperature cache, fact-check web-search skip, `localize_article_images`, vision
      skip for generic, GitHub-API anti-stale protocol).
- [ ] Write `docs/HANDBOOK.md` (fold in `iamneedingyourwarmsloth.md` + `Plan.md`; add the
      fill-in section and dependency inventory captured below).
- [ ] Write `docs/ROADMAP.md` from the phases in this plan.
- [ ] Deliver **one** patch `saves_docs__base_<sha>.patch` (built against live HEAD via the
      GitHub-API tarball method). User applies, commits, pushes. **Last manual patch.**

---

## Phase 1 — CLI setup (first desktop session)

Goal: turn the repo into a Claude-Code-ready project so the loop is one command.

- [ ] Confirm `git remote` + working tree on the desktop; `git pull` latest.
- [ ] Add `.claude/settings.json`: `SessionStart` hook (sync + print ROADMAP phase), a Bash
      allowlist for `python`, `git`, `ffmpeg`, `yt-dlp` to cut permission prompts.
- [ ] Add `.claude/commands/save.md` — the `/save <url>` loop (design below).
- [ ] Add a minimal `pyproject.toml` (ruff, line-length 100, py311) and a tiny smoke test so
      Claude gets lint/test feedback. No big test suite yet.
- [ ] Verify `python scripts/test_connection.py` and one `process_one.py <url>` per platform.

**`/save <url>` loop (path of least resistance):**
1. Run `python scripts/process_one.py <url>` (add a `--json` summary mode if helpful).
2. Auto-QA the generated note: media embedded? transcript present when expected? recipe section
   when food? article images localized? no "unavailable/failed" callouts? title/folder sane?
3. Print a tight PASS/ISSUES summary.
4. On PASS → commit (`Archive: <title>`) and push. On ISSUES → surface the specific gap, fix, re-run.
5. If the save exposed a real bug, fix it + update `docs/HANDBOOK.md`/`ROADMAP.md` in the same commit.

This keeps the loop **single-agent and cheap** — no multi-agent orchestration in the normal path;
escalate only when a save fails in a novel way.

---

## Phase 2 — Harden the existing pipeline

(From the wiring audit; all are real gaps in already-built code.)

- [ ] **Startup config validation** — fail fast if required paths/keys/channels are missing.
- [ ] **Graceful missing-inbox** — `FileWatcher` should warn + no-op instead of erroring.
- [ ] **Extraction timeout** — wrap `extractor.extract()` in `asyncio.timeout()` so one hung
      page can't wedge the queue.
- [ ] **Claude API backoff/circuit-breaker** — bounded exponential backoff; stop hammering on
      repeated failures (wire the existing `utils/retry.py`, currently unused).
- [ ] **Crash-safe `_finalize`** — order writes so a mid-finalize crash can't both lose the URL
      and skip the note; make state the source of truth for dedup (in-memory `_queued` is lost on
      restart).
- [ ] *(optional)* persist NL-edit sessions so a bot restart mid-edit doesn't drop them.

---

## Phase 3 — Runtime token efficiency

(From the efficiency audit. Caching, Haiku-OCR, and frame montaging are already optimal — these
are the remaining levers. Quick wins first; test each on a handful of real saves before keeping.)

- [ ] **Cache the travel-location check** — add `cache_control: ephemeral` in `verifier.py`
      (only uncached call; zero risk).
- [ ] **Right-size `max_tokens`** in `config.yaml` (analysis 8192→4096; OCR 8192→6000), with a
      truncation watch — easy to test/revert.
- [ ] **Narrow `web_search_topics`** — drop `finance` (social finance is mostly opinion; web
      search rarely changes the verdict). Health stays.
- [ ] **Trim `SYSTEM_PROMPT`** folder examples / make recipe+travel rules conditional — A/B on
      ~50 saves to confirm no routing regression.
- [ ] **Conditional OCR** — skip the Haiku OCR stage for pure-photo posts (text-density check).
- [ ] **Batch API (decision #2: night-time batching)** — route OCR + analysis through Message
      Batches for overnight saves; keep daytime real-time. Add a "pending batch" Discord state.

---

## Phase 4 — Deploy, mobile, live-test

(Mostly setup, not code — the bot/cookie/fact-check/preferences code already exists.)

- [ ] **End-to-end Discord run** — the flow has never been exercised live start-to-finish; do a
      full paste → approve → note-written pass for each button.
- [ ] **Docker deploy to NAS** — `docker-compose up --build`; verify all volume mounts + vault
      writability; confirm container can reach the Whisper host.
- [ ] **iOS share shortcut** (Obsidian Actions URI) + **Android** (HTTP Shortcuts → SMB append
      via Tailscale) writing to `00 - FILE.md`.
- [ ] **Whisper runbook** — start command, host/port, firewall, restart procedure (into HANDBOOK).

---

## Phase 5 — Ongoing tuning

- [ ] Feed real URLs via `/save`; refine quality and routing; keep `preferences.json` learning.
- [ ] Keep `docs/*` current as part of each change. Re-run **targeted** (single-agent) reviews on
      the CLI when touching risky areas — cheap, unlike the full ultracode sweep.

---

## Deferred: the code review

The ultracode review's find phase produced **18 candidate findings** across core-pipeline,
extraction/formatting, no-delete/atomicity, concurrency, resilience, and AI-cost dimensions, but
the **verify phase never ran** (spend limit) — so none are confirmed and they were not surfaced.
Do **not** trust them as-is. Re-run review **cheaply on the CLI** once moved: a single
`/code-review` pass over the working tree (or per-subsystem) gives verified findings without the
794K-token multi-agent cost. Track resulting fixes in `ROADMAP.md`.

---

## Reference: captured inputs (for the HANDBOOK)

**Dependencies (pip):** anthropic (Claude), discord.py (bot), instaloader + yt-dlp + gallery-dl
(social extraction), playwright + readability-lxml + trafilatura (web articles), watchdog (inbox
watch), pyyaml, python-dotenv, aiofiles, requests, flask (whisper server), pillow-heif (HEIC).
**Whisper server:** faster-whisper. **External:** ffmpeg, chromium (Playwright), Whisper host
(e.g. 192.168.1.90:5000), SMB to NAS.

**Run from scratch:** venv → `pip install -r requirements.txt` → `playwright install chromium` →
`.env` (ANTHROPIC_API_KEY, DISCORD_BOT_TOKEN) → cookies → start Whisper server →
`scripts/test_connection.py` → `scripts/process_one.py <url>` → `python -m src.main` → Docker.

**"Lives in your head" (HANDBOOK fill-in):** NAS model/SMB hostnames + volume layout; exact vault
inbox path (`00 - FILE.md`) and SAVES folder tree; Discord server + channel IDs + bot invite
scopes; which social accounts back each cookie file + refresh cadence; Whisper host details +
firewall; Anthropic org/key + budget; mobile shortcut configs; per-platform performance baselines.

**Inaccuracies to ignore from the audits:** the wiring agent wrongly reported extractors/AI/media/
notes as "missing" (they exist and are substantial); the dev-loop agent invented some specifics
(`config.local.yaml.example`, an `init_windows` script, and a wrong inbox filename) — real inbox is
`00 - FILE.md`. Dependency inventory and run steps from it are reliable.

---

## Verification

- **Phase 1:** `test_connection.py` green; `process_one.py` produces a correct note per platform;
  `/save` commits + pushes; `git` works without the proxy dance.
- **Phase 2:** simulate failures — missing inbox, malformed URL, forced API error, kill mid-
  finalize — pipeline degrades gracefully and never double-loses a URL.
- **Phase 3:** per lever, compare note quality on ~10–50 real saves pre/post; watch for truncation
  and routing regressions; confirm batch results land by morning.
- **Phase 4:** mobile share → URL in `00 - FILE.md` < 30s → Discord approval → approve → note in
  vault; Docker container healthy on NAS with all mounts writable.
