# SAVES — Roadmap to Production

> Live checklist. Update boxes as items land; this is the "where are we" anchor across chats.
> Full reasoning lives in `docs/PLAN.md`. Orientation lives in `CLAUDE.md`. Recreate/maintain
> details live in `docs/HANDBOOK.md`. System design, diagrams, and the command/config cheat
> sheet live in `docs/ARCHITECTURE.md`.

**Current phase:** Phase 4 (deploy, mobile, live-test). Phases 1–3 complete (one optional
Phase 2 item — persist NL-edit sessions across restart — deferred).

**Status in one line:** Core pipeline is feature-complete and in active dev use. Remaining work
is hardening, deployment, mobile sharing, runtime cost tuning, and a frictionless save loop.

---

## Decisions locked (change anytime)

- **Dev surface:** Claude Code **CLI** on the desktop (not web).
- **Immediate processing — PROD launches fully real-time (Bora, 2026-07-04).** Every URL is
  extracted, analyzed, and sent to Discord **the moment it arrives**; nothing is queued for
  later, batched, or deferred. Two reasons: (1) during tuning, bugs/quality issues must be
  visible and fixable immediately, not discovered in bulk later; (2) Bora prefers to handle
  approval cards right away while the saved content is fresh in mind, rather than recalling
  context on stale cards. Corollaries: `discord.auto_approve_on_timeout` stays `false`;
  never add notification batching/deferral without a new explicit decision. Batching *might*
  come later — that is Phase 6, and only Phase 6.
- **Serial approval gating — refinement of IMMEDIATE (Bora, 2026-07-29).** `processing.serial_approval`
  (default **on**) shows **one approval card at a time**: the next URL isn't processed until the
  current card is approved/skipped/forgotten. This does NOT contradict IMMEDIATE — the active item
  is still processed + carded the instant it's its turn, and nothing is auto-approved; it only
  serializes QUEUE ORDER so a `/crawl`/batch doesn't fire all cards at once, and so each save reuses
  the folder pref + tags just approved (progressive easing). Persisted to `queue_state.json`
  (survives restarts). Controls: ⏭️ Skip button, `/queue` status, per-card "Save X of N" footer.
  `serial_approval: false` = old all-at-once. (Shipped.)
- **Runtime cost:** cut cost with **real-time** levers first (model routing, `effort`, prompt
  caching, fact-check gating) so results stay instant during tuning. The **Batch API** (50%
  off but async — no instant results) is **deferred to a final phase**, adopted only once
  save quality is dialed in and instant feedback is no longer needed.
- **Decision notation discipline (Bora, 2026-07-04):** decisions like the above must be
  written down where they'll be seen later — a short code comment at the point someone would
  be tempted to change the behavior, plus the rationale here in "Decisions locked" (or
  `docs/PLAN.md`), in the **same commit** as the related change. Chat-only decisions get lost.
- **Docs system:** `CLAUDE.md` (auto-loaded orientation) + `docs/HANDBOOK.md` (recreate/maintain)
  + `docs/ROADMAP.md` (this file). Update the relevant doc in the same commit as each change.
- **Tags are all-lowercase (Bora, 2026-07-05).** Every tag — AI-generated or user-typed — is
  normalized by `clean_tags()` (src/utils/tag_index.py) at every entry point (processor,
  Add-Tags modal, `/tag add`, NL edit, near-dup swap) plus a write-time backstop in
  `bot._finalize()`. The lowercase mirror of the ALL-CAPS `clean_folder_path()` convention
  for folders. Don't add a tag entry point without routing it through `clean_tags()`.
- **Mobile capture is Obsidian-Sync-mediated (Bora, 2026-07-06).** Both iOS and Android append
  the shared URL to the *local* vault's `0 - INBOX/SAVES.md` via the Obsidian **Advanced URI**
  plugin; Obsidian Sync propagates to the NAS copy the container watches and syncs the finished
  note back. This **replaces** the earlier SMB-append-over-Tailscale plan — it works off the
  home network with no VPN and is identical on both OSes. Dependency: an Obsidian client must
  keep the NAS vault synced (the Sync↔NAS bridge). Runbook: `docs/MOBILE_SHORTCUTS.md`.
- **Git lives on self-hosted Forgejo; GitHub is retired (Bora, 2026-08-05).** `origin` is
  `https://192.168.1.201:3443/<user>/SAVES.git` — Forgejo 15 LTS + PostgreSQL 17, hardened
  non-root, on the same NAS SAVES deploys to. Build + locked infra decisions: `docs/FORGEJO.md`.
  Consequences that constrain future work:
  (a) **HTTPS + PAT only** — SSH is disabled on the forge; clients must trust the step-ca
      **root**. Never paper over a TLS failure with `http.sslVerify=false`.
  (b) **The forge is LAN-only with no port-forward**, so **Claude Code on the web can no longer
      reach this repo** — local Claude Code is the only development path. The patch-delivery
      and Anti-Stale Protocol are obsolete and have been removed from `CLAUDE.md`.
  (c) **No `gh` CLI / GitHub Actions integration.** Forgejo's API is Gitea/GitHub-*shaped*,
      not a drop-in. CI runners are deferred to the future Proxmox host, and if ever run on the
      NAS must use a **DinD sidecar — never the host Docker socket**.
  (d) ⚠️ **There is no longer an off-site copy of the repo.** `/volume1/docker/forgejo` (repo
      tree + a `pg_dump`) must be in the backup set, or a NAS loss loses the history.
- **SAVES runs NON-ROOT as `sa_saves`, and every filesystem step states owner + mode
  (Bora, 2026-08-06).** Two linked decisions:
  (a) **Container identity.** `saves_app` previously ran as root in-container, which meant
      every note it wrote into the bind-mounted vault was `root:root` — **uneditable and
      undeletable by Obsidian and over SMB**. It now runs as a DSM service account
      (`user:` + a matching in-image account via build args). The vault and media dirs are
      `sa_saves:users` mode **2775** (setgid, so notes stay in a group the human can write);
      state and cookies are `sa_saves:docker_service_accounts` (2770/2700 — the latter holds
      credentials). `src/main.py` sets `os.umask(0o002)` so new files are 664/775 rather
      than 644/755, which is what makes the setgid bit actually useful.
  (b) **Runbook discipline.** *Every* step that creates, copies or moves a file MUST state
      its owner and mode and give the `chown`/`chmod`. Bora works from an admin account over
      SSH, so everything he touches is created owned by the wrong account. This is not a
      style preference — it blocked the rollout: he would not create the vault directories
      because no doc said what to give them. `PROD_ROLLOUT.md` §1.6 is the ownership map;
      preflight `[7]` enforces UID/owner agreement and warns on a missing setgid bit.
  Corollary: **never assume the UID.** `id sa_saves` is the authority; `1031` is a
  placeholder default in `docker/.env.example`, not a fact.
- **NAS resource limits: `mem_limit` yes, `cpus:` never (Bora, 2026-08-05).** Discovered during
  the Forgejo build: Synology kernels lack CFS bandwidth control, so *any* CPU quota is a **hard
  deploy failure** (`NanoCPUs can not be set…`), not a warning. `docker/docker-compose.yml`
  therefore has no `cpus:` and — because the key would otherwise be discarded by Compose V2 —
  **no top-level `version:` key** either. `saves_app` declares `SAVES_MEM_LIMIT` (default `3g`)
  now that it shares the NAS with the forge. `preflight_nas.sh` `[6]` enforces all three.

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
> Strategy locked with Bora (2026-07-01). Batch API excluded (Phase 6). Priority order of
> where the money goes: **fact-check web-search loop ≫ Opus analysis ≫ Haiku OCR.**
- [x] Per-topic web-search caps: **health 6, finance 3, political 1** (finance stays on web
      search; recipes/food stay local-only). Rounds scale with the cap so a 6-search health
      check isn't cut off.
- [x] **On-demand fact-check**: the automatic on-arrival pass is LOCAL only (cheap flags —
      conflict-of-interest, media authenticity, dosage/safety); the web-search loop runs via a
      Discord "🔍 Deep fact-check" button (health 6 / finance 3 / political 1). Code + view
      logic done and unit-verified; live Discord button click still to be tested in Phase 4.
- [x] **A/B Opus vs Sonnet** analysis: `scripts/ab_compare.py` writes two labeled notes to the
      DEV vault for review. **Verdict (2026-07-01): Sonnet wins** — across many test saves Sonnet
      matched Opus on routing/quality at ~half the cost. `ai.model` set to `claude-sonnet-4-6`.
      Sonnet accepts `temperature`, so the param stays (no more auto-stripped 400 on each call).
- [x] `effort: medium` on analysis + fact-check (`output_config.effort`); self-learning
      `_MODELS_REJECTING_EFFORT` guard silently drops it for models that 400 (e.g. Haiku OCR)
      so the shared call path doesn't need branching. `temperature` non-issue: Sonnet accepts it.
- [x] Travel-location check (`verifier.py`) reviewed → **kept as-is**: NOT prompt-cached, but it's
      a single `max_tokens: 1024` call that only fires on travel posts — cost is in the noise.
- [x] `max_tokens` reviewed (2026-07-04) → **kept as-is**: analysis `8192`, fact-check `6000`, OCR
      `8192`, verifier `1024`. A high ceiling isn't billed unless output fills it, and lowering it
      risks truncating a long multi-recipe carousel's JSON. No real saving; truncation risk is real.
- [x] `SYSTEM_PROMPT` (~16K chars) reviewed (2026-07-04) → **kept as-is**: prompt caching already
      bills the static prefix at ~10% after the first call, so a trim saves little and risks dropping
      a routing/recipe/travel rule. Left intact by decision.
- [x] Conditional OCR — photo posts (`is_photo_post`) handled by vision naturally; TikTok photo
      fix (2026-07-04, `a5264c6`) drops the background mp3 so nothing bogus hits Whisper

## Phase 4 — Deploy, mobile, live-test
- [x] Pre-Phase-4 deep code review (2026-07-04) → **`docs/CODE_REVIEW_2026-07-04.md`** —
      34 findings: **2 deploy blockers** (compose mounts ≠ config paths; single-file state
      binds break `os.replace` + preferences.json unmounted), 6 High, 13 Medium, 13 Low +
      unused-config-key table. Fix blockers BEFORE `docker-compose up`.
- [x] Fix review blockers #1–2 (2026-07-04): portable path design — canonical container paths
      in `config.yaml`; host mapping via `docker/.env` (Docker) / `config.local.yaml` (bare
      metal); single state-dir mount incl. `preferences.json`; TZ + log rotation. PROD go-live
      = **fresh implementation** (carry cookies + optionally preferences.json only) —
      plan in ARCHITECTURE.md §1b.
- [x] Fix review High findings #3–7 (2026-07-04, commit `16a7bf3`): crash-orphan reconciliation;
      _finalize error handling + alert; watcher path-normcase fix + debounce wired; inbox
      exact-match removal; article_markdown prompt leak + metadata value cap.
- [x] Fix review Medium findings #9, #11, #19 (2026-07-04): Reddit short-url resolve moved into
      the worker thread (was blocking the event loop / Discord heartbeats); remote transcription
      now enforces `max_duration_minutes` (duration cap centralized in `transcribe()` so an
      oversized file can't stall the queue ~17 min on the remote POST + retries); formatter
      injects recipe/fact-check/location sections before the *last* `---` (rsplit) so they land
      above Metadata instead of mid-article after the article's own horizontal rules.
- [x] Fix review findings #8 (High) + #10 (Medium) (2026-07-04): Facebook video posts that link
      a source article in the caption are now archived AS the video (link kept in metadata as
      `related_article_url`) instead of being rerouted to the article extractor and losing the
      video — reroute now gated on `not has_video`. Reddit deleted/removed posts (empty
      `children`) raise a descriptive `ValueError` (routed to permanent-fail) instead of a bare
      `IndexError`.
- [x] Fix review Medium findings #12–#18 (2026-07-04) — all Medium findings now closed:
      per-item tolerance loading pending approvals (one malformed/legacy record no longer wipes
      the rest); startup inbox scan gated on `bot.wait_until_ready()` as a background task
      (duplicate notices deferred, never dropped); `write_note`/`move_note` sandbox
      `folder_path` inside `realpath(vault_root)` (hallucinated absolute or `../` paths raise);
      NL-edit API failures reply with the error and keep the session open for retry;
      YouTube/Facebook thin extraction raises (YouTube bot-check worded to route to auth-retry)
      instead of producing junk approval cards; `content[0].text` replaced with first-text-block
      extraction (thinking blocks / refusal-empty content handled cleanly); downloader
      newest-file fallback filtered to media extensions. All verified by no-token tests.
- [x] Tag-editing UX batch (2026-07-05, Bora request): 🗑️ Remove Tags is now one ✖ button
      per tag (tap to remove; replaced the multi-select dropdown); new `src/utils/tag_index.py`
      scans vault frontmatter `tags:` (TTL rescan + incremental bump on note write) and powers
      (a) `/tag add` slash command with search-as-you-type autocomplete + usage counts (guild
      sync in on_ready), (b) Add-Tags modal near-duplicate check with one-tap "Use existing"
      swap buttons (airfryer → air-fryer), (c) an existing-tags taxonomy hint in the analysis
      prompt so Claude reuses established tags. 56 no-token tests.
      Follow-up refinement (2026-07-05, Bora): Edit Tags → **Add Tags** — plain typed tags,
      no +/- syntax, add-only (removal = 🗑️ Remove Tags); stray `-tag` tokens are skipped
      with a pointer, not added literally. Button order now Approve → Add Tags → Remove Tags
      → Change Path → NL Edit. custom_id kept as `edit_tags` for pre-rename cards.
- [x] **Live-edit fidelity batch (2026-07-05, from Bora's first live approval session):**
      (a) `SAVESBot._refresh_card()` — EVERY mutation (Add/Remove/swap tags, `/tag add`,
      Change Path, NL edit) re-renders the original approval card, so ✅ Approve never sits
      on stale info; embed + NL reply now list ALL tags (the old 8-tag preview cap hid
      NL-added tags, which append at the end — the "NL edit didn't apply" report);
      (b) Change-Path modal prepopulates the current path; `clean_folder_path()` forces
      ALL-CAPS folder convention at every entry point (AI generation, modal, NL edit);
      (c) tag index now also reads inline body `#tags` + singular `tag:` key (code blocks/
      URL anchors/numeric-only excluded) so manually-added Obsidian tags autocomplete too;
      (d) **/forget** slash command (autocompletes saved history) — `ProcessingState.forget()`
      + queue-manager session-set clear, the sanctioned way to re-save a URL after deleting
      its note (state, not the vault, is the duplicate authority). 123 no-token tests.
- [ ] End-to-end live Discord run (paste → approve → note written) for every button
      (now incl. card-refresh-on-edit, `/forget`, + `/tag add` autocomplete)
- [x] **Self-hosted git forge stood up (Bora, 2026-08-05):** Forgejo 15 LTS + PostgreSQL 17 on
      the NAS (`https://192.168.1.201:3443`), hardened non-root (`cap_drop: ALL`,
      `no-new-privileges`, `read_only`, internal-only DB network), step-ca TLS, HTTPS+PAT only.
      **GitHub retired.** Build + locked infra decisions: `docs/FORGEJO.md`; SAVES-side
      consequences: `CLAUDE.md` → Git Workflow, "Decisions locked" above.
      Follow-on SAVES changes shipped in the same commit: `docker-compose.yml` drops the
      top-level `version:` key and adds `mem_limit: ${SAVES_MEM_LIMIT:-3g}` (the NAS is no
      longer single-tenant); `preflight_nas.sh` gains check `[6]` (RAM headroom vs other
      containers, no-`cpus:`, no-`version:`, `compose config` parse).
- [ ] Docker deploy to NAS (`docker-compose up --build`); verify mounts + vault write + Whisper reach
      — runbook `docs/DEPLOY_NAS.md`; preflight `scripts/preflight_nas.sh`; `.dockerignore` added
      (trims context + keeps secrets/state out of image). Go-live gate: stop DEV bot first
      (one Discord token = one gateway connection).
- [ ] Mobile share capture — **both** phones append to the local vault's `0 - INBOX/SAVES.md`
      via the Obsidian **Advanced URI** plugin (`mode=append`); Obsidian Sync carries it to the
      NAS and the finished note + cleared inbox back. iOS = Shortcuts share sheet; Android =
      Tasker/MacroDroid share trigger. Runbook: `docs/MOBILE_SHORTCUTS.md`.
      **Design decision (Bora, 2026-07-06):** Obsidian-Sync-mediated, NOT the old SMB-over-
      Tailscale plan — capture works off home network with no VPN, same mechanism on both OSes.
      Dependency: an Obsidian client must keep the NAS vault synced (the Sync↔NAS bridge).
- [x] Whisper runbook into HANDBOOK — **§9.1** (start, host/port, `/health` verify, config,
      client 300s+retry, firewall cmd, restart options, IP stability). Two owner facts to confirm
      live (firewall rule in place? restart mechanism?) tracked in §11.

## Phase 5 — Ongoing tuning
- [ ] Feed real URLs via `/save`; refine quality + routing; let `preferences.json` learn
- [ ] Keep docs current per change; targeted single-agent reviews on risky edits only
- [ ] **Revisit NAS-vs-workstation hosting once there is data (Bora, 2026-08-06).** Decision
      for now: **run on the NAS**, but measure rather than guess. The concern is CPU, not RAM
      (32 GB total; SAVES capped at 4 GB alongside Forgejo's 3 GB). The DS1621+'s embedded
      Ryzen is roughly a tenth of the workstation's 7950X, and **Synology makes a CPU quota
      impossible**, so a burst can transiently degrade SMB, Obsidian Sync, and the forge.
      What makes it tolerable: processing is strictly serial and the approval gate idles the
      pipeline between saves, so bursts are short.
      **Instrumentation (shipped):** `processor._process_one` logs one `TIMING` line per save
      with per-stage wall-clock — `extract` and `vision` are the local-CPU stages (Chromium,
      ffmpeg); `download`/`transcribe`/`analyze` are mostly network waits.
      `grep TIMING logs/processor.log` after a few dozen real saves.
      **Move the pipeline to the workstation if** `extract`+`vision` dominate total time, or
      NAS responsiveness visibly suffers during saves. The port is cheap by design — a
      `config.local.yaml` and `python src\main.py`, same code (ARCHITECTURE §1b). Costs to
      weigh then: the workstation must be up 24/7 (it already must be, for Whisper), and
      notes would be written to the NAS vault over SMB, which is untested.
      **Third option if only the browser is the problem:** keep the pipeline on the NAS and
      connect Playwright to a Chromium on the workstation over CDP — the Whisper pattern.
      Bonus: the provecho profile would then live on the machine where it was captured,
      dissolving the §1.4 Windows-DPAPI-on-Linux risk entirely. Real engineering, not a
      config change.

## Phase 5b — Discord-native saving & site crawlers
> Raised by Bora (2026-07-22). Both shipped.

- [x] **Discord-native saving (shipped 2026-07-30):** paste a URL in **`#SAVES-inbox`** (or POST
  via a Discord **webhook** from Android/Tasker — a webhook is just a message) and the bot's
  `on_message` → `_handle_inbox_message` routes it into `QueueManager.enqueue_url()` exactly as
  the inbox watcher does (a creator URL triggers `/crawl` via the shared `_crawl_core`). Dedup,
  approval flow, and vault write are unchanged; a reaction acks each message. `on_message` skips
  only the bot's own posts, so webhook messages are processed — that's what lets the Android
  one-tap share (share sheet → single target → POST to the webhook) work with **no new server or
  port on the NAS**. Optional (`discord.channel_inbox`); mobile setup in `docs/MOBILE_SHORTCUTS.md`.

- **Site crawlers (slash command `/crawl <creator-url>`):** Discover all recipe URLs for **one
  creator**, deduplicate against `processing_state.json`, show a "Found N recipes, M already
  saved — queue K?" confirm card in Discord, then enqueue survivors one at a time through the
  existing pipeline. Each item gets its own normal approval card.

  **Status (2026-07-29):**
  - [x] `SiteCrawler` base (`src/crawlers/base.py`) — shared `partition()` (dedup vs
        `processing_state.json`, normalized) + `enqueue_discovered()` (one-at-a-time, rate-limited).
  - [x] `ProvechoCrawler` (`src/crawlers/provecho.py`) + `get_crawler()` router. `discover_urls()`
        LIVE-VERIFIED: `.../creator/davespizzaoven` → 65 recipes, 0 cross-creator bleed, count matches
        the page's "65 Recipes" header.
  - [x] `scripts/crawl_creator.py` — CLI: dry-run list, or `--to-inbox` to feed the running pipeline.
  - [x] `config.yaml` `crawl:` — `enabled`, `rate_limit_seconds`, `max_recipes`.
  - [x] Discord `/crawl` slash command + confirm card (✅ Queue / 📋 List / ✖ Cancel),
        `CrawlConfirmView` in `bot.py`; backgrounds the enqueue paced by `crawl.rate_limit_seconds`.
  - [x] Embedded video download+transcribe + recipe photos (see below) — LIVE-VERIFIED.
  - [ ] **END-TO-END BATCH RUN — the one thing still untested (as of 2026-08-05).** What IS
        verified is `discover_urls()` + single-recipe extraction, both via the CLI. What has
        **never** been run: `/crawl` in a live bot → ✅ Queue → `enqueue_discovered()` pacing →
        the serial gate holding across dozens of cards → `queue_state.json` restoring after a
        restart mid-batch. Staged test plan (auth check → free CLI dry-run → one recipe →
        small creator → restart test → large creator): **`docs/PROD_ROLLOUT.md` Part 4**.
        Do stages 0–2 in DEV on Windows *before* the NAS cutover — that isolates the crawler
        from the §1.4 cross-OS profile risk. Never big-bang the first run: one click on a
        65-recipe creator is the largest token spend the system can make, and serial approval
        turns it into 65 sequential manual reviews.

  **Per-creator scoping (Bora, 2026-07-28) — HARD REQUIREMENT.** provecho has many creators;
  crawling the whole site would be far too much. `/crawl` takes a single **creator page** and
  must NOT traverse beyond that creator's own recipes. URL structure:
  - Creator page: `https://www.provecho.co/platform/creator/<handle>`
    (e.g. `.../creator/davespizzaoven` = 65 recipes, `.../creator/seans_pizza` = 147 recipes)
  - Recipe page: `https://www.provecho.co/platform/recipe/<id>`
  `discover_urls(creator_url)` collects ONLY `/platform/recipe/<id>` links found within that
  creator page's recipe grid, and never follows links to other creators or a global discover
  feed. The creator page is a Next.js SPA that lazy-loads its grid, so discovery must scroll /
  paginate until all N recipes are present before collecting (and can cross-check the count the
  page shows). Requires the authenticated persistent profile (see Auth finding below).

  **Architecture note (Bora, 2026-07-22):** A generic `SiteCrawler` base class with a shared
  `enqueue_discovered()` method handles the downstream pipeline for all sites. The only
  site-specific part is `discover_urls()` — the method that finds and returns a list of content
  URLs from the index/profile page. Public sites with simple HTML structure can use a generic
  Playwright-based implementation; SPAs (Next.js/React) and login-required sites need a
  site-specific subclass (`ProvechoCrawler` is the first). Rate limiting (configurable delay
  between enqueues) and a dry-run mode (lists found URLs without queueing) are required from
  day one.

  **Auth finding (Bora, 2026-07-28) — ✅ VERIFIED WORKING.** After capturing a persistent
  profile and re-running the dry-run, a previously-paywalled recipe returned its real
  ingredients/directions — the auth gate is cleared and the crawler is unblocked. provecho
  gates full recipes behind **Firebase Authentication**, which stores its refresh token in
  **IndexedDB** (`firebaseLocalStorageDb`)
  — NOT in cookies, localStorage, or sessionStorage. This was proven step by step: a Netscape
  `.txt` session, a Playwright `storage_state()` JSON (cookies + localStorage), and even one
  that additionally captured sessionStorage all replayed to the paywalled "This recipe is
  locked" page; the saved state held only analytics cookies + a `platformVisitorAccountId`
  visitor id and UI-state keys, never a token. The origin exposes `firebaseLocalStorageDb`,
  `firebase-installations-database`, `firebase-heartbeat-database` — a textbook Firebase-Auth
  fingerprint. Since IndexedDB is not reachable by any portable-JSON capture, the fix is a
  **persistent on-disk browser profile** (chosen by Bora, 2026-07-28):
  `scripts/capture_session.py` now uses `launch_persistent_context(cookies/<host>_profile/)`
  — you log in once and the profile keeps IndexedDB/cookies/localStorage on disk exactly like
  a normal browser; `GenericExtractor` prefers a `<host>_profile/` (via `_profile_dir_for_url`)
  and relaunches headless with it, so Firebase re-authenticates. Capture with
  `python scripts\capture_session.py https://www.provecho.co/platform/login provecho.co`
  (log in, open a recipe to confirm it's unlocked, then press Enter). Trade-off: the profile is
  a machine-specific directory, so the NAS needs its own one-time login. The portable
  `_session.json` (cookies + localStorage + sessionStorage) path remains supported for non-
  IndexedDB sites but no longer authenticates provecho.

  **Embedded video + pictures (Bora, 2026-07-28) — ✅ DONE (2026-07-29), LIVE-VERIFIED.**
  - **Video:** provecho embeds a plain direct MP4 (`<video src="…b-cdn.net/….mp4">` — BunnyCDN,
    no HLS/Mux/DRM/signed tokens). `GenericExtractor._extract_video_urls()` collects `<video>`/
    `<source>` srcs (skips blob:/data:) and prepends them to `media_urls`, so the existing
    `download_media()` → `transcribe()` steps grab + Whisper them and the note embeds the player —
    exactly like an IG/TikTok save. Verified: the mp4 downloaded via yt-dlp and embedded at the top
    of the note. (Transcription needs the Whisper server up; it was down during the test, non-fatal.)
  - **Pictures:** SPA recipe pages sprinkle a tiny `/w_100/` Cloudinary ingredient-icon next to every
    ingredient (product/stock photos = noise). `_strip_thumbnail_images()` drops images whose
    Cloudinary transform declares a small render size (< 200 px), keeping the hero (`w_800`) and any
    real step photos. `web_recipe` renders the structured Recipe callout (not the article body), so
    `formatter._article_photo_embeds()` pulls the surviving localized photos into a `## 📸 Photos`
    section. Verified: 25 image refs → 1 (the hero dish photo) rendered under Photos.

## Phase 6 — Cost optimization (post-stabilization)  *(gated: only once quality is dialed in)*
> Deferred here on purpose (Bora, 2026-07-01): batching removes instant results, which would
> cripple the tweaking/testing loop. Do NOT start until saves are consistently high-quality
> and instant feedback is no longer needed.
- [ ] Batch API (night-time): route the two pre-approval Claude calls (OCR + analysis) through
      Message Batches for 50% off; add a "pending batch" state to `processing_state.json` and a
      poller that resumes when the batch completes. Fact-check can batch too once stable.
- [ ] (deferred / rejected for now) Multi-LLM providers (Gemini free tier, bucket-exhaustion
      fallback). Shelved 2026-07-01 — attacks the smallest cost bucket (OCR ~7%) and risks
      output consistency across providers. Revisit ONLY if Claude costs get out of hand; if so,
      scope it narrowly (OCR-only offload or availability failover), never the analysis stage.

---

## Phase 7 — Scaling (tens of thousands of saves/tags)  *(not urgent; full analysis in ARCHITECTURE §11)*
> Raised by Bora (2026-07-05): the vault will eventually hold tens of thousands of saves and
> an unknown number of tags. Dedup lookups (O(1) dict) and prompt size (top-50 tags / ≤400
> folders) scale fine; three paths degrade. Correctness is unaffected — these are perf cliffs.
- [x] **① Tag-index rescan off the event loop — DONE (2026-07-05).** `_tag_choices` is now
      `async` and dispatches `search()` via `asyncio.to_thread`; the Add-Tags modal runs its
      near-duplicate check (`_compute_swap_pairs` → `close_matches`) in a thread too; and
      `TagIndex.refresh()` gained a `threading.Lock` + double-checked TTL so a burst of
      concurrent autocomplete keystrokes collapses to one vault walk. No keystroke can freeze
      the loop anymore. Test §18 covers it (8 concurrent rescans → 1 walk).
  - [ ] ①(b) *(structural, only if the threaded walk itself gets slow on a huge vault)*
        persist the index to a sidecar + incremental mtime-based updates so a rescan doesn't
        re-read unchanged notes at all — removes the O(vault) read entirely.
- [ ] **② `processing_state.json` → SQLite — TRIGGER: ~100k entries** (Bora, 2026-07-05: "I
      don't think we will [reach it], but if we do"). Today every `mark_*`/`forget` rewrites the
      whole file (O(n) per save: ~15 MB re-serialized at 100k). Below ~100k the cost is
      negligible next to extraction + Claude calls — **do not build preemptively.** When the day
      comes: `state(url PRIMARY KEY, status, path, reason, platform, timestamp)` with per-row
      `INSERT … ON CONFLICT DO UPDATE`/`DELETE` and indexed `SELECT`s. Localized to
      `queue_manager.py` — small public surface (`is_done`/`is_processed`/`mark_done`/`forget`/
      `entries`), fully covered by the section-13 no-token tests. Migration sketch + full detail
      in ARCHITECTURE §11 ②. Zero-delete policy unaffected (a row `DELETE` is not a file delete).
- [ ] **③ Autocomplete sorts per keystroke.** `/forget`'s `_forget_choices` sorts the entire
      state dict per keystroke; `search()`/`close_matches()` sort/scan all tags. Risk Discord's
      3s autocomplete deadline + stall the loop at scale. Pre-sort/cap the forget history to
      recent-N and thread the reads (folds into ①).

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
