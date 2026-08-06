# SAVES → Production Rollout (Synology NAS, Docker Compose)

The full guided rollout: the **plan**, the **step-by-step**, the **acceptance tests** (including
everything added recently — serial queue, `#SAVES-inbox`, the Android/Tasker webhook, `/crawl`),
the **staged first `/crawl` run** (Part 4 — the batch path has never been exercised end to end,
and it's the largest token spend one click can trigger), and **rollback**. Self-contained —
follow it top to bottom.

> This supersedes and expands `docs/DEPLOY_NAS.md` (kept as the terse core-install reference).
> Portability model: `ARCHITECTURE.md` §1b. User-facing behavior: `USER_GUIDE.md` / `COMMANDS.md`.

---

## 0. Decisions locked for this rollout (Bora, 2026-07-30)

| Decision | Choice | Consequence |
|---|---|---|
| Cutover | **Direct, one bot token** | Stop the DEV bot, start PROD on the **real** vault + **existing** `#SAVES` channels. One token = one live bot; they never run at once. |
| Orchestration | **Docker Compose only** | No Komodo/Dozzle/DOCO-CD in this plan — compose is the whole deployment. (Optional-later note at the end, no steps.) |
| Scope | **Everything built** | Core pipeline + serial approval queue + `#SAVES-inbox` + Android/Tasker webhook + `/crawl` (needs the provecho login profile on the NAS). |
| State/history | **Fresh dedup, keep prefs** | Start with EMPTY `processing_state.json` (so real-vault saves aren't blocked by DEV's test-vault history); copy **`preferences.json`** so learned folder routing carries over. |
| Code source | **Self-hosted Forgejo** (added 2026-08-05) | The NAS clones SAVES from its *own* Forgejo at `https://192.168.1.201:3443/`, not GitHub (retired). Needs the step-ca root CA in the NAS trust store + a PAT. Build details: `docs/FORGEJO.md`. |
| Resource limits | **`mem_limit` yes, `cpus:` never** | The NAS is now shared with the forge (Forgejo 2 GB + Postgres 1 GB), so `saves_app` declares `SAVES_MEM_LIMIT` (default `3g`). A CPU quota is a **hard deploy failure** on Synology — see §1.5. |

---

# PART 1 — THE PLAN

## 1.1 What we're standing up

The SAVES pipeline moves from **DEV** (bare Python on the Windows workstation, against a *test*
vault) to **PROD** (a Docker container on the Synology NAS, against the **real** Obsidian vault).
Nothing about the code changes per host — `config.yaml` uses canonical container paths
(`/vault`, `/media`, `/app/state`); the NAS's real locations are supplied only through
`docker/.env`. (See `ARCHITECTURE.md` §1b.)

```
                          ┌─────────────────── Synology NAS ───────────────────┐
 Inputs (3 ways):         │                                                     │
  • Obsidian inbox file ──┼─▶ 0 - INBOX/SAVES.md (real vault, host mount)       │
  • #SAVES-inbox paste ───┼─▶ Discord ─▶ bot on_message ─┐                      │
  • Android/Tasker share ─┼─▶ Discord webhook ─▶ #SAVES-inbox ─┘                │
                          │                               ▼                      │
                          │   saves_app container:  watcher + processor + bot   │
                          │     extract → media(/media) → Whisper(LAN) → Claude │
                          │     → serial approval card in #SAVES-approvals       │
                          │        approve ▶ note written into /vault            │
                          │   state: /app/state (host: /volume1/docker/saves/state)
                          └──────────────┬──────────────────────────────────────┘
                                         │ Whisper POST (LAN)
                         Workstation: whisper_server.py @ 192.168.1.90:5000
```

## 1.2 Host layout (confirm these against your NAS)

| Canonical (container) | Host (`docker/.env`) | Holds |
|---|---|---|
| `/vault` | `VAULT_HOST=/volume1/NAS/OBSIDIAN/Remote Vault` | real vault; inbox `0 - INBOX/SAVES.md`; notes written here |
| `/media` | `MEDIA_HOST=/volume1/NAS/MEDIA/SAVES` | downloaded videos/images |
| `/app/state` | `STATE_HOST=/volume1/docker/saves/state` | `processing_state.json`, `pending_approvals.json`, `preferences.json`, `queue_state.json` |
| `/app/cookies` (**:rw**) | `../cookies` in the repo clone | `*.txt` cookies **and** `provecho.co_profile/` (browser profile — needs write) |
| `/app/config.yaml` (:ro) | `../config.yaml` | configuration (edit + restart to change) |
| repo `.env` | `../.env` | secrets: `ANTHROPIC_API_KEY`, `DISCORD_BOT_TOKEN` |

Repo clone on the NAS: **`/volume1/docker/saves/app`**.

The NAS (`192.168.1.201`) also hosts the **Forgejo forge** under `/volume1/docker/forgejo`
(host port `3443`). The two stacks are independent — separate Compose projects, separate
volumes, no shared network — but they share RAM and disk. See §1.5.

## 1.3 Prerequisites (have these before you start)

- **SSH admin** access to the NAS; **DSM 7.2+ Container Manager** (Docker Compose v2) or the older Docker package.
- Your **`ANTHROPIC_API_KEY`** and **`DISCORD_BOT_TOKEN`** (same keys as DEV).
- **Forgejo reachable + trusted from the NAS shell**, and a **PAT** with `repository: Read`
  for the clone (step 1). The forge is HTTPS-only with a step-ca certificate, so the NAS
  needs the step-ca **root** in its trust store or `git clone` fails TLS verification.
- Workstation **Whisper** reachable from the NAS: `whisper_server.py` running + Windows Firewall inbound TCP **5000** open. Give the workstation a **DHCP reservation** so it stays `192.168.1.90`.
- The real vault already has a **`0 - INBOX/`** folder, and an **Obsidian Sync client** is bridging that vault (needed for the phone→inbox path and for notes to sync back to your devices).
- **Disk:** a few GB free on `/volume1` for the first image build (Chromium + Playwright), plus room for media growth.
- **Provecho profile** (for `/crawl`): captured on a machine with a browser — see §1.4.

## 1.4 The one item with real risk: the provecho login profile

`/crawl` and provecho recipe extraction authenticate via a **persistent Chromium profile**
(`cookies/provecho.co_profile/`) whose Firebase token lives in **IndexedDB**. The NAS is
headless, so you can't log in there — the profile must be captured elsewhere and copied in.

**Cross-OS caveat (read this):** your DEV profile was captured on **Windows**. Its **IndexedDB**
(the Firebase auth) is portable to the Linux container, but Windows Chromium encrypts the
*cookie SQLite* with DPAPI, which **won't decrypt on Linux**. Provecho's auth is IndexedDB-based,
so the Windows profile *often works anyway* — but it's the one thing that may need iteration.

- **Quick path:** copy the existing Windows profile to the NAS and test `/crawl` (§ step 6). If provecho content unlocks, you're done.
- **Robust path (if it shows "locked"/login):** re-capture the profile in a **Linux** browser that matches the container — e.g. **WSL2 + WSLg** on the workstation (`wsl`, then run `capture_session.py` there), or any Linux desktop — then copy that profile in. A Linux-origin profile avoids the DPAPI mismatch entirely.

Everything else in scope (core saves, serial queue, `#SAVES-inbox`, webhook, tags, icons) has **no** such risk.

## 1.5 Sharing the NAS with the Forgejo forge (new — 2026-08-05)

SAVES is no longer the only thing on this box. Three concrete consequences:

**Memory.** Forgejo reserves 2 GB and PostgreSQL 1 GB. `saves_app` now declares
`mem_limit: ${SAVES_MEM_LIMIT:-3g}` — previously it was uncapped, so a runaway
Chromium/ffmpeg could have OOM-killed the forge. Set `SAVES_MEM_LIMIT` in `docker/.env`
against the NAS's real RAM, leaving ~1.5 GB for DSM:

| NAS RAM | Forgejo+PG | DSM | Suggested `SAVES_MEM_LIMIT` |
|---|---|---|---|
| 4 GB | 3 GB | ~1.5 GB | **`1g`** — tight; Chromium will be slow. Consider a RAM upgrade. |
| 8 GB | 3 GB | ~1.5 GB | **`3g`** (the default) |
| 16 GB+ | 3 GB | ~1.5 GB | `4g`–`6g` |

Preflight check **`[6]`** prints total RAM, sums what other running containers already
reserve, and warns on over-commit — trust it over the table.

**⚠️ Never set a CPU limit.** Synology kernels are built without CFS bandwidth control, so
`cpus:`, `cpu_quota`, and `deploy.resources.limits.cpus` are all **rejected by the daemon**
(`NanoCPUs can not be set, as your kernel does not support CPU CFS scheduler`) — the deploy
fails outright rather than warning. This is also why `docker/docker-compose.yml` has no
top-level `version:` key (it makes Compose V2 silently discard `mem_limit`). Preflight `[6]`
fails on either. Full writeup: `docs/FORGEJO.md` §6.

**Ports + firewall.** Forgejo binds `192.168.1.201:3443`. SAVES publishes **no ports at all**
(it dials out to Discord and to Whisper), so there is no conflict and the DSM firewall's
deny-all-inbound rule does not affect it. If you added that rule during the Forgejo build,
verify SMB/Obsidian-Sync access to the vault still works — that path *is* inbound.

**⚠️ The DSM ACL trap applies to SAVES paths too.** Once ownership is set over SSH on
`/volume1/docker/saves/`, do **not** edit that folder's permissions in File Station or
Control Panel → Shared Folder → Permissions. DSM rewrites ACLs across the subtree and can
clobber the POSIX owner, breaking the container at its next restart.

## 1.6 Risk register

| Risk | Impact | Mitigation |
|---|---|---|
| DEV bot still running at cutover | Both bots fight the one token; double-processing | **Stop DEV** before `up` (step 5). One token, one bot. |
| NAS trust store lacks the step-ca root | `git clone` from Forgejo fails TLS verification | Install the root per `FORGEJO.md` Phase 11. **Never** work around it with `http.sslVerify=false`. |
| SAVES memory cap over-committed vs Forgejo | OOM kills the forge or SAVES mid-save | `SAVES_MEM_LIMIT` sized per §1.5; preflight `[6]` warns before you deploy. |
| A `cpus:` key gets added to compose | **Hard deploy failure** on Synology | Never set one; preflight `[6]` fails on it. `docs/FORGEJO.md` §6. |
| DSM firewall deny-all added during the Forgejo build | SMB / Obsidian-Sync to the vault blocked (SAVES itself unaffected — no inbound ports) | Verify vault access after the firewall change; §1.5. |
| Whisper workstation off/unreachable | Video/audio saves get **empty transcripts** (non-fatal) | Preflight `[5]`; DHCP-reserve + auto-start the Whisper server. |
| Cookies mounted read-only | `/crawl` + login sites fail to launch browser | **Fixed:** compose now mounts `cookies :rw`; preflight `[4]` checks writability. |
| Windows provecho profile won't decrypt on Linux | `/crawl` provecho shows locked | §1.4 fallback: re-capture under WSL2/WSLg. |
| DEV `processing_state.json` copied by mistake | PROD thinks real-vault URLs are already saved | **Don't copy it.** Only copy `preferences.json` (step 4). |
| Obsidian Sync bridge down | Phone saves + note sync-back stall | Keep the bridging Obsidian client up; the `#SAVES-inbox`/webhook path is independent of it. |
| Synology is arm64 | Heavier/longer first build | Supported (Chromium arm64); ensure ~4 GB free; expect a longer build. |
| First build is slow | 10–30 min | Cached after first build; only re-builds on code change. |

---

# PART 2 — STEP-BY-STEP

> **[YOU]** = manual (SSH / DSM / phone). **[APP]** = automated (compose / the container).

### Step 1 — [YOU] Get the code onto the NAS (from Forgejo)
Enable SSH (DSM → Control Panel → Terminal & SNMP → Enable SSH), then from the workstation:
```bash
ssh <you>@192.168.1.201
```

**1a. Trust the step-ca root on the NAS** (once — `git` will not talk to the forge without it).
Copy the root you exported in `FORGEJO.md` §3A.5 and register it:
```bash
# from the WORKSTATION:
scp homelab-root-ca.crt <you>@192.168.1.201:/tmp/
# on the NAS:
sudo cp /tmp/homelab-root-ca.crt /usr/share/pki/trust/anchors/ 2>/dev/null \
  || sudo cp /tmp/homelab-root-ca.crt /etc/ssl/certs/
sudo git config --system http.sslCAInfo /etc/ssl/certs/homelab-root-ca.crt
```
Verify before continuing — this must succeed with **no** TLS error:
```bash
curl -fsS --cacert /etc/ssl/certs/homelab-root-ca.crt \
  https://192.168.1.201:3443/api/v1/version
```
> ⚠️ If it fails, fix the trust store. **Do not set `http.sslVerify=false`** — that discards
> the entire point of the certificate work and silently accepts any MITM on the LAN.

**1b. Clone.** Use a **PAT** (Forgejo Settings → Applications → *Manage Access Tokens*, scope
`repository: Read` — a deploy-only token, separate from your workstation token):
```bash
sudo mkdir -p /volume1/docker/saves && cd /volume1/docker/saves
sudo git clone https://192.168.1.201:3443/<user>/SAVES.git app
cd app
uname -m          # expect x86_64 (aarch64 also works, heavier build)
```
Username = your Forgejo username; password = **the token**. To avoid re-typing on every
`git pull`, either `sudo git config --global credential.helper store` (plaintext in
`/root/.git-credentials` — acceptable on a NAS only you administer) or embed the token in
the remote URL.

No git on the NAS? Install **Git Server** from Package Center, or copy the repo over SMB into `/volume1/docker/saves/app`.

### Step 2 — [YOU] Secrets + host paths (two gitignored files)
```bash
cp .env.example .env
vi .env                       # ANTHROPIC_API_KEY=... and DISCORD_BOT_TOKEN=... (same as DEV)

cp docker/.env.example docker/.env
cat docker/.env               # confirm VAULT_HOST / MEDIA_HOST / STATE_HOST match your NAS
```
`docker/.env.example` already has the PROD values; leave `Remote Vault` **unquoted** (the compose file quotes the mount, so the space is fine).

### Step 3 — [YOU] Carry cookies + the provecho profile into `cookies/`
The clone has no cookies (gitignored). From the **workstation**:
```bash
# .txt cookies (Instagram/TikTok/Facebook):
scp cookies/*.txt <you>@192.168.1.201:/volume1/docker/saves/app/cookies/

# provecho login profile for /crawl (large — ~160 MB; trims below):
scp -r cookies/provecho.co_profile <you>@192.168.1.201:/volume1/docker/saves/app/cookies/
```
**Shrink the profile (optional):** most of the 160 MB is disposable cache. Auth lives in
`Default/IndexedDB`, `Default/Local Storage`, `Default/Preferences`, and top-level `Local State`.
You may exclude `Default/Cache`, `Default/Code Cache`, `Default/GPUCache`, `Default/Service Worker/CacheStorage`
when copying to save space and time.

> If `/crawl` later shows provecho **locked** in PROD, this profile didn't port cleanly — re-capture under **WSL2/WSLg** (§1.4) and recopy. This is expected-possible; nothing else depends on it.

### Step 4 — [YOU] State dir + carry preferences (fresh dedup, keep prefs)
```bash
sudo mkdir -p /volume1/docker/saves/state
# keep learned folder routing (vault-relative paths → portable):
scp preferences.json <you>@192.168.1.201:/tmp/ && sudo mv /tmp/preferences.json /volume1/docker/saves/state/
```
**Do NOT copy** `processing_state.json`, `pending_approvals.json`, or `queue_state.json` from DEV — PROD starts with an **empty** save-history on purpose (the real vault doesn't have DEV's test saves). `queue_state.json` is created empty on first run.

### Step 5 — [YOU] Whisper up + firewall + stop the DEV bot
On the **workstation** (PowerShell):
```powershell
python scripts\whisper_server.py --model large-v3-turbo --port 5000   # leave running
# one-time, as Administrator:
New-NetFirewallRule -DisplayName "SAVES Whisper 5000" -Direction Inbound `
  -Protocol TCP -LocalPort 5000 -Action Allow -Profile Private
```
Confirm the workstation LAN IP is still `192.168.1.90` (`ipconfig`); if it changed, update `transcription.remote_url` in `config.yaml`.
Then **stop the DEV bot** (`Ctrl-C` on `python src\main.py`). Leave Whisper running — PROD needs it.

### Step 6 — [YOU] Create the `#SAVES-inbox` channel + webhook (new)
In the **existing** "Bora's AI Ops" server:
1. Create a text channel named exactly **`SAVES-inbox`** (matches `discord.channel_inbox` in `config.yaml`).
2. **Edit Channel → Integrations → Webhooks → New Webhook → Copy Webhook URL.** Save that URL for the phone shortcut (step 10). Treat it like a password.

No bot restart needed for the channel — the listener matches by channel name at message time.

### Step 7 — [YOU] Pre-flight, then [APP] build & launch
From `/volume1/docker/saves/app`:
```bash
sh scripts/preflight_nas.sh
```
Fix every **FAIL** (missing mount, unwritable state/cookies, unreachable Whisper, missing profile) before continuing. On **PASSED**:
```bash
sudo docker compose -f docker/docker-compose.yml up --build -d
# older DSM/Docker: sudo docker-compose -f docker/docker-compose.yml up --build -d
```
First build: **10–30 min** (base image, apt, pip, Chromium). Cached afterward.

### Step 8 — [APP/YOU] Confirm startup
```bash
sudo docker compose -f docker/docker-compose.yml logs -f
```
Look for: **no** `ConfigError`, the bot connecting, and `Slash commands synced to N guild(s)`. Then run the **acceptance tests (Part 3)**.

### Step 9 — [YOU] Verify the real-vault round trip
Paste one URL into the **real** inbox `/volume1/NAS/OBSIDIAN/Remote Vault/0 - INBOX/SAVES.md` (via Obsidian or `echo >>`), watch for the approval card in **#SAVES-approvals**, approve, and confirm the note lands in the real vault and the inbox line is removed.

### Step 10 — [YOU] Phone: the one-tap Android share (webhook)
Set up a single share-sheet target that POSTs to the webhook from step 6 — full instructions (HTTP Shortcuts app, or Tasker+AutoShare, or MacroDroid) are in **`docs/MOBILE_SHORTCUTS.md`**. Quick test from the workstation first:
```bash
curl -H "Content-Type: application/json" \
  -d '{"content":"https://www.reddit.com/r/test/"}' "<your-webhook-url>"
```
→ a message appears in `#SAVES-inbox`, the bot reacts ✅, and an approval card follows.

---

# PART 3 — ACCEPTANCE TESTS

Run these in PROD after step 8. Each is one action with a clear expected result. ✅ column is yours to tick.

| # | Feature | Action | Expected | ✅ |
|---|---|---|---|---|
| 1 | Startup | `logs -f` | No `ConfigError`; `Slash commands synced to N guild(s)` | |
| 2 | Core save (file) | Paste a Reddit/web URL into the real inbox file | Card in `#SAVES-approvals` within ~3 s | |
| 3 | Approve → note | Click ✅ Approve | Note written into the real vault at the shown path; inbox line removed | |
| 4 | Duplicate | Re-paste the same URL | 🔁 duplicate notice with **Re-save / Dismiss** in `#SAVES-approvals`; no tokens spent | |
| 5 | `/forget` | `/forget <that url>` then re-paste | Old card dropped, inbox re-scanned, fresh card appears | |
| 6 | `#SAVES-inbox` paste | Paste a normal URL **in the channel** | Bot reacts ✅ + an approval card | |
| 7 | Webhook (mobile path) | `curl` POST to the webhook (step 10) | Message posts to `#SAVES-inbox`, bot reacts ✅, card follows | |
| 8 | `#SAVES-inbox` crawl | Paste a **creator** URL (`…/platform/creator/<handle>`) | 🕸️ reaction + a **Crawl** confirm card (Found/Already-saved/New) | |
| 9 | `/crawl` slash | `/crawl <creator-url>` | Same confirm card via the slash command | |
| 10 | Serial queue | On the crawl card, click **✅ Queue** | Cards arrive **one at a time**; footer reads "Save X of N · M still waiting" | |
| 11 | `/queue` | `/queue` while a batch is mid-review | Reports the current save + how many wait behind it | |
| 12 | ⏭️ Skip | Click Skip on the active card | Card retracts, next one shows; skipped URL returns later | |
| 13 | Ingredient tags | Approve/inspect a recipe card | Every ingredient tagged in detailed **+** simplified form; `provecho` + author identity tags present | |
| 14 | Inline icons | Open a saved provecho recipe note in Obsidian | Per-ingredient icons render **inline** (base64 data URIs; no broken/remote images) | |
| 15 | Media + Whisper | Save a video (e.g. a reel or a provecho recipe with video) | Video downloaded under `/media`; transcript present (Whisper reachable) | |
| 16 | Buttons | Try 🏷️ Add Tags, 🗑️ Remove Tags, 📁 Change Path, ✏️ NL Edit | Each re-renders the card with the change | |
| 17 | `/tag add` | `/tag add <partial>` | Search-as-you-type autocomplete over vault tags | |
| 18 | Fact-check | Save a health/finance post; try 🔍 Deep fact-check | Flags surface; ⚠️ Approve+Warning writes a callout | |
| 19 | Restart resilience | While a card is pending: `docker compose restart` | After restart the pending card still gates; queue resumes (nothing auto-approved) | |
| 20 | Phone (Obsidian path) | Share a link from the phone via Advanced URI | Reaches the NAS inbox (via Sync bridge) → card; finished note syncs back | |

Tests **15** and **20** depend on external pieces (Whisper up; Obsidian Sync bridge up). Tests **8/9/14** depend on the provecho profile (§1.4).

Acceptance tests **8–12** touch `/crawl` at the smallest possible scale. **Before running a
real creator crawl, work through PART 4** — the batch path has never been exercised end to end.

---

# PART 4 — PROVECHO CRAWL: STAGED FIRST RUN

> **Why this has its own part.** As of 2026-08-05, `discover_urls()` and single-recipe
> extraction are live-verified, but **`/crawl` → ✅ Queue → approval cards → notes has never
> been run end to end.** It is also the largest token spend a single click can trigger: one
> click on a 65-recipe creator queues 65 saves, and `serial_approval` then makes that 65
> sequential manual approvals. **Escalate in stages; never big-bang the first run.**

Relevant config (`config.yaml` → `crawl:`): `rate_limit_seconds: 2.0`, `max_recipes: 300`
(safety cap on one crawl), `enabled: true`.

### Do stages 0–2 in DEV, on the workstation, BEFORE the PROD cutover

The provecho profile is the rollout's one genuinely risky item (§1.4 — Windows DPAPI cookie
encryption doesn't port to the Linux container). Proving the crawl works **natively on
Windows first** means that if PROD later shows "locked", you know it's the cross-OS port and
not the crawler. This ordering costs nothing and removes the main ambiguity.

### Stage 0 — Is the auth still alive? *(zero tokens, ~1 min)*

Firebase refresh tokens expire, and the profile ages. Check before anything else:

```powershell
python scripts\process_one.py "https://www.provecho.co/platform/recipe/<known-id>" --dry-run
```

| Result | Meaning |
|---|---|
| Real ingredients + directions printed | Auth alive — continue |
| "This recipe is locked" / a login page | Re-capture: `python scripts\capture_session.py https://www.provecho.co/platform/login provecho.co` |

### Stage 1 — CLI dry-run *(zero tokens, queues nothing)*

```powershell
python scripts\crawl_creator.py https://www.provecho.co/platform/creator/<handle>
```

Exercises `discover_urls()` + `partition()` with no cost and no queueing. Verify:

- The count matches the page's own "**N** Recipes" header (a mismatch is logged as a WARNING).
- Every URL is `/platform/recipe/<id>` — **zero** `/platform/creator/` URLs (per-creator
  scoping is a hard requirement; cross-creator bleed is a bug, not a preference).
- The new-vs-already-saved split looks sane against `processing_state.json`.

### Stage 2 — One recipe, full pipeline, nothing written

```powershell
python scripts\process_one.py "<one URL from stage 1>" --dry-run
```

Inspect the printed note for:

| Check | Expected |
|---|---|
| Embedded video | downloaded + `EmbedRelativeTo` block; transcript present (needs Whisper up) |
| Ingredient icons | inline `![\|24](data:image/webp;base64,…)` |
| Self-containment | **zero** `http` image URLs anywhere in the note (Hard Constraint #3) |
| Ingredient tags | every ingredient in **both** detailed and simplified form |
| Identity | `platform: provecho` (not `generic`), author handle resolved (not `unknown`) |
| Caption | suppressed if it's a pure recipe re-dump; kept if it carries extra content |

### Stage 3 — The Discord surface, smallest possible creator

**This is the part that has never run.** In Discord: `/crawl <a SMALL creator's URL>`.

1. Confirm card appears with Found / Already-saved / New counts.
2. **📋 List first** — the ephemeral dry-run list is free. Verify before spending.
3. Then **✅ Queue**.

Watch for:

| # | Expected |
|---|---|
| 3a | Cards arrive **one at a time**, not all at once |
| 3b | Footer reads "Save X of N · M still waiting" and the counter advances on each approve |
| 3c | `/queue` agrees with what you actually see |
| 3d | **⏭️ Skip** retracts the card, releases the gate, and the next card appears immediately |
| 3e | A skipped URL comes back later (re-queued to the BACK, reprocessed fresh) |

### Stage 4 — Restart resilience mid-batch

With one card pending and several still queued:

```bash
sudo docker compose -f docker/docker-compose.yml restart
```

The pending card must still gate, `queue_state.json` must restore the waiting list, and
**nothing may be auto-approved** (`auto_approve_on_timeout` stays `false` by decision). This
is the specific failure mode the persistent queue was built for — verify it deliberately.

### Stage 5 — Only now, a large creator

Two things to accept before clicking ✅ Queue on a 65-recipe creator: it is the largest token
spend one click can trigger, and serial approval turns it into **65 sequential manual
approvals**. Use **⏭️ Skip** freely — skipped URLs return to the back of the queue rather than
being lost.

> **Blast-radius tip:** while you're still learning the batch behavior, temporarily lower
> `crawl.max_recipes` (e.g. to `10`) in `config.yaml` and `restart`. It's a hard cap on how
> many URLs one crawl will queue, and it's the cheapest possible safety net.

### If PROD shows "locked" but DEV worked

That is the §1.4 cross-OS profile failure, not a crawler bug. Re-capture the profile under
**WSL2/WSLg** (or any Linux desktop), recopy to `cookies/provecho.co_profile/`, and
`restart`. Nothing else in the rollout depends on it.

---

# PART 5 — ROLLBACK

Because it's **one bot token**, only one of DEV/PROD runs at a time — rollback is "stop PROD, start DEV."

```bash
# On the NAS — stop PROD (host mounts/state untouched):
sudo docker compose -f docker/docker-compose.yml down
```
```powershell
# On the workstation — bring DEV back:
python src\main.py
```
- Notes PROD already wrote are **real saves** in the real vault — leave them (that's the point). To undo one, delete it in Obsidian *and* `/forget` its URL (deleting the note alone doesn't clear PROD's dedup).
- PROD's `processing_state.json` lives in the NAS state dir, separate from DEV's — the two never cross-contaminate.
- To retry PROD later: `docker compose ... up -d` (no `--build` unless code changed). Stop DEV first.

---

# PART 6 — DAY-2 OPERATIONS

| Task | Command (from `/volume1/docker/saves/app`) |
|---|---|
| Follow logs | `sudo docker compose -f docker/docker-compose.yml logs -f` |
| Restart | `sudo docker compose -f docker/docker-compose.yml restart` |
| Stop | `sudo docker compose -f docker/docker-compose.yml down` (host mounts/state kept) |
| Update to a new commit | `git pull && sudo docker compose -f docker/docker-compose.yml up --build -d` (pulls from Forgejo on this same NAS — the forge must be up) |
| Change `config.yaml` | edit it, then `restart` (RO mount, re-read on start — no rebuild) |
| Shell in | `sudo docker exec -it saves_app sh` |
| Refresh provecho auth | re-capture the profile (workstation/WSL2), recopy to `cookies/provecho.co_profile/`, `restart` |
| Refresh IG/TikTok/FB cookies | re-export `*.txt`, copy into `cookies/`, `restart` |

**Health signals:** the container is `restart: unless-stopped`; `docker compose ps` shows it Up. Alerts (cookie expiry, extraction failures, unreachable Whisper) post to **#SAVES-alerts**; the pipeline log lives in `logs/` and `#SAVES-logs`.

---

# PART 7 — Optional later (NOT needed to run)

Docker Compose above **is** the whole deployment. If you later want conveniences, each is
additive and changes nothing about how SAVES runs — ask for steps when you want them:

- **Dozzle** — a tiny container that reads the Docker socket and shows live logs in a browser (nicer than `logs -f`).
- **Komodo** — a web UI to manage/restart/monitor the stack and its env from one place.
- **DOCO-CD** — GitOps: watches this repo and auto-redeploys the compose stack on `git push` (secrets stay off-repo in the NAS `.env`).

None are prerequisites; they'd sit *around* the same `docker/docker-compose.yml`.
