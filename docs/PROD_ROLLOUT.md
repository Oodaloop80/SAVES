# SAVES → Production Rollout (Windows workstation, bare-metal)

> ⚠️ **Target changed 2026-08-07** — this was a NAS/Docker rollout. See §0.0 for why it moved
> to the workstation and what is now 🕐 deferred. NAS material is kept, not deleted.

The full guided rollout: the **plan**, the **step-by-step**, the **acceptance tests** (including
everything added recently — serial queue, `#SAVES-inbox`, the Android/Tasker webhook, `/crawl`),
the **staged first `/crawl` run** (Part 4 — the batch path has never been exercised end to end,
and it's the largest token spend one click can trigger), and **rollback**. Self-contained —
follow it top to bottom.

> This supersedes and expands `docs/DEPLOY_NAS.md` (kept as the terse core-install reference).
> Portability model: `ARCHITECTURE.md` §1b. User-facing behavior: `USER_GUIDE.md` / `COMMANDS.md`.

---

## 0. Decisions locked for this rollout

### ⚠️ 0.0 — The target changed on 2026-08-07: **workstation, not NAS**

Everything below that describes Docker, the NAS, service accounts and DSM ACLs is
🕐 **DEFERRED**, not deleted. It was built, and much of it was verified on real hardware. It
is simply not the deployment being done now.

**What forced the change.** The plan put the vault on the NAS and Obsidian on the workstation
over a mapped network drive. That failed immediately with folder-modification errors — and it
was never going to work: **Obsidian does not support vaults on network drives**, because it
depends on native file watchers, fast full-vault indexing, and file locking, none of which SMB
provides reliably. Keeping the vault on the NAS therefore required a filesystem-level sync
layer (Syncthing, or Obsidian-in-Docker + CouchDB) purely to bridge one machine to another.

**Why the workstation is the better answer, not merely the easier one.** Running SAVES where
the vault already lives removes four risks at once:

| Risk removed | Detail |
|---|---|
| The sync layer | No Syncthing, no CouchDB, no bridge, no conflict class |
| **The provecho profile** | §1.4 called this *"the one item with real risk"* — the Chromium profile was captured on Windows and its cookie SQLite is DPAPI-encrypted, which **does not decrypt on Linux**. Running on Windows makes it a non-issue |
| Untested Dockerfile changes | Non-root `USER`, `PLAYWRIGHT_BROWSERS_PATH`, volume ownership — all unproven, all moot |
| NAS permission complexity | ACL grants, UID mapping, preflight `[7]` — not needed when nothing runs on the NAS |

And there was **no new deployment to build**: `config.local.yaml` is the existing, working
bare-metal mechanism — the same one DEV has used all along. This is a config change, not a
migration.

**The cost, stated honestly.** SAVES is no longer 24/7. It processes when the workstation is
awake. One specific consequence:

| Capture path | Survives downtime? |
|---|---|
| Obsidian inbox **file** | ✅ **Yes** — `scan_inbox()` runs at startup and queues everything waiting |
| Discord **`#SAVES-inbox`** | ❌ **No** — `on_message` is live-only; there is no history backfill, so a URL pasted while the workstation sleeps is silently lost |

With Obsidian Sync on mobile, the phone captures into the inbox *file* — the resilient path —
so this is a demotion of the Discord channel to a convenience, not a loss of function.

🕐 **What would revisit the NAS:** wanting genuine 24/7 capture, or the workstation becoming
unavailable. The Docker/ACL work is complete and verified; it would be re-enabled, not rebuilt.

### ⚠️ 0.0.1 — The inbox backlog is PARKED (2026-08-07)

The real vault's inbox held **592 unique URLs** — accumulated over months of saving links
without SAVES pointed at it. `scan_inbox()` queues **everything** in that file at startup, so a
first PROD run against it would have meant roughly **$180–300** in tokens and 592 sequential
approval cards.

**What was done:** 590 URLs moved to `0 - INBOX/SAVES-BACKLOG.md`; **2 left in the inbox** (the
Instagram reels carried over from the pending-16). A timestamped `.bak` of the original inbox
sits beside it.

**Why that file is safe:** the watcher binds to `0 - INBOX/SAVES.md` **only** — `watcher.py:27`
compares the exact normalised path — so URLs in the backlog are inert. Not queued, not deduped,
zero cost.

**Feeding it back:** cut 10–20 lines into `SAVES.md`, let them drain, repeat. At ~$0.30–0.50
per save a batch of 20 is ~$6–10. Same staged-escalation logic as Part 4's first `/crawl`.

> This is why the first PROD run has exactly **2 URLs**: enough to prove the pipeline end to
> end, small enough that a mistake costs pennies.

### 0.1 — Rollout decisions (Bora, 2026-07-30, still in force)

| Decision | Choice | Consequence |
|---|---|---|
| Cutover | **Direct, one bot token** | Stop the DEV bot, start PROD on the **real** vault + **existing** `#SAVES` channels. One token = one live bot; they never run at once. |
| ~~Orchestration~~ | 🕐 **Deferred with the NAS** | Was "Docker Compose only". Not applicable to a bare-metal workstation run. |
| Scope | **Everything built** | Core pipeline + serial approval queue + `#SAVES-inbox` + `/crawl`. The provecho profile is already native here — no cross-OS risk. |
| State/history | **Fresh dedup, keep prefs, CARRY pending** (extended 2026-08-07) | Start with EMPTY `processing_state.json` (so real-vault saves aren't blocked by DEV's test-vault history); keep **`preferences.json`** so learned folder routing carries over; **carry `pending_approvals.json`** — the 16 live approval cards re-send on startup and their media paths are already media-root-relative, so they stay valid. |
| Code source | **GitHub for now; Forgejo cutover is a test item** (revised 2026-08-07) | Forgejo is up and working, but the origin switch is treated as something to *test*, not assume. `git remote -v` is the authority. Build details: `OKAYNET/SELF HOST/forgejo.md` (vault). |
| ~~Resource limits~~ | 🕐 **Deferred with the NAS** | `mem_limit` yes / `cpus:` never still applies *if* the container is ever used — a CPU quota is a hard deploy failure on Synology (§1.5). Irrelevant bare-metal. |
| ~~Container identity~~ | 🕐 **Deferred with the NAS** | The non-root `sa_saves` model and its DSM ACL grants (§1.6) are complete and were verified on hardware 2026-08-06. Bare-metal runs as you, against your own files — no identity work needed. |

---

# PART 1 — THE PLAN

## 1.1 What we're standing up

SAVES runs **bare-metal Python on the Windows workstation**, against the **real** Obsidian
vault on local disk. `config.yaml` holds canonical container paths; `config.local.yaml`
(gitignored) overrides them with the real Windows locations — the same mechanism DEV has always
used. (Portability model: `ARCHITECTURE.md` §1b.)

```
                    ┌────────── Windows workstation ──────────┐
 Inputs:            │                                              │
  • Obsidian inbox ─┼─▶ 0 - INBOX/SAVES.md  (local disk)          │
    (+ phone via     │        │ watchdog, 3s debounce                │
     Obsidian Sync)  │        ▼                                     │
  • #SAVES-inbox ───┼─▶ python src\main.py                        │
    (live-only)      │     watcher + processor + Discord bot        │
                     │     extract → media → Whisper(localhost)      │
                     │     → Claude → serial approval card          │
                     │        approve ▶ note → REMOTE VAULT          │
                     │     state: JSONs beside the app              │
                     └─────────────┬──────────────────────┘
                                   │ SMB (bulk media only)
                        NAS \\192.168.1.201\MEDIA\SAVES
```

Whisper runs on the same machine (`127.0.0.1:5000`), still over HTTP so it stays a separate
restartable process and the code path matches the deferred NAS deployment.

## 1.2 Layout (all in `config.local.yaml`)

| Canonical (`config.yaml`) | Real path (`config.local.yaml`) | Holds |
|---|---|---|
| `/vault` | `C:/Users/Bora/Documents/OBSIDIAN/REMOTE VAULT` | real vault; inbox `0 - INBOX/SAVES.md`; notes written here |
| `/media` | `//192.168.1.201/MEDIA/SAVES` | downloaded videos/images — stays on the NAS (bulk storage, already backed up) |
| `/app/state` | beside the app | `processing_state.json`, `pending_approvals.json`, `preferences.json`, `queue_state.json` |
| `/app/cookies` | `cookies/` in the app dir | `*.txt` cookies **and** `provecho.co_profile/` (163 MB, Windows-native — no DPAPI problem) |
| repo `.env` | `.env` in the app dir | secrets: `ANTHROPIC_API_KEY`, `DISCORD_BOT_TOKEN` |

**App location: `C:\APPS\AI\SAVES`.** No spaces (avoids a whole class of quoting bugs in
future scripts and scheduled tasks), mirrors the NAS's `/volume1/APPS/` convention, and shorter
— which matters because `file_manager.py` budgets note paths against Windows' 260-char limit.

> ⚠️ **The vault must stay on local disk.** Not a mapped drive, not a cloud-synced folder.
> Verified 2026-08-07 that `C:\Users\Bora\Documents` is **not** OneDrive-redirected. If OneDrive
> Known Folder Move is ever enabled, this vault becomes cloud-synced and breaks exactly the way
> the network drive did. Backup design: `docs/BACKUP_AND_RECOVERY.md`.

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
fails on either. Full writeup: `OKAYNET/SELF HOST/forgejo.md` (vault) §6.

**Ports + firewall.** Forgejo binds `192.168.1.201:3443`. SAVES publishes **no ports at all**
(it dials out to Discord and to Whisper), so there is no conflict and the DSM firewall's
deny-all-inbound rule does not affect it. If you added that rule during the Forgejo build,
verify SMB/Obsidian-Sync access to the vault still works — that path *is* inbound.

**⚠️ The DSM ACL trap applies to SAVES paths too.** Once ownership is set over SSH on
`/volume1/docker/saves/`, do **not** edit that folder's permissions in File Station or
Control Panel → Shared Folder → Permissions. DSM rewrites ACLs across the subtree and can
clobber the POSIX owner, breaking the container at its next restart.

## 1.6 Identity, ownership and permissions (READ BEFORE CREATING ANYTHING)

> **Standing rule (Bora, 2026-08-06): every step that creates, copies or moves a file states
> its owner and mode, and gives the command to set them.** You do this work from an admin
> account over SSH, so *everything you touch is created owned by that admin account* — which
> is almost never the account that should own it at rest. Verifying ownership is part of each
> step, not a cleanup pass afterwards.

### The identity

`saves_app` runs as a **non-root DSM service account, `sa_saves`** — per the NAS-wide SOP in
**`OKAYNET/SOP/sop_synology_service_accounts.md` (vault)**, which is the authority on naming and group assignment.
Before 2026-08-06 the container ran as **root**, which is why this section did not exist and
why the vault directories were never created: root in a container writing to a bind-mounted
vault produces `root:root` notes that **Obsidian and SMB cannot edit or delete**.

Confirmed on the NAS (2026-08-06):

```
uid=1031(sa_saves) gid=100(users) groups=100(users),65536(docker_service_accounts)
```

Settings that must agree, or nothing works:

| Where | Setting |
|---|---|
| DSM | `id sa_saves` → **1031**, service group **65536** |
| `docker/.env` | `SAVES_UID=1031`, `SAVES_GID=65536` |
| compose | `user: "1031:65536"` |
| APPS tree | a **DSM ACL** granting `sa_saves` (see below) |
| `/volume1/docker/*`, `/volume1/MEDIA/SAVES` | POSIX ownership per the map below |

**If they disagree, every write fails with `EACCES`.** Preflight `[7]` checks the POSIX side.

### The APPS tree is governed by DSM ACLs, not POSIX groups

**Correction (Bora, 2026-08-06).** An earlier revision of this section told you to give the
vault group `users` (GID 100) and rely on setgid. **That was wrong for a secure build:** on
DSM, `users` contains *every* account and DSM force-adds every new account to it, so it is
the everyone-group. Using it as the access group would make the vault writable by every
present and future DSM user.

The APPS tree instead uses **DSM ACLs**, which is the DSM-native mechanism and strictly
tighter:

| Principal | Access on `/volume1/APPS` and below |
|---|---|
| `OodaAdmin` + group `administrators` | **Full Control** (own, modify permissions, RW) |
| group `app_service_accounts` (65537) | Read + Write |
| group `docker_service_accounts` (65536) | **explicitly denied** — containers get nothing by default |
| `sa_forgejo`, `guest`, `http`, others | explicitly denied |

`sa_saves` is in `docker_service_accounts`, so **the blanket deny catches it.** It is
therefore granted as a **named exception**, with an explicitly-set (level 0) ACE that sorts
*before* the inherited group deny:

```
[0] user:sa_saves:allow:rwxpdDaARWc--:fd--  (level:0)   ← explicit, evaluated first
...
[4] group:docker_service_accounts:deny:…    (level:4)   ← inherited, would otherwise block it
```

> ⚠️ **This makes ACE *order* load-bearing.** If the ACL is ever rebuilt — most likely by
> applying permissions from a parent folder in File Station, the DSM ACL trap below — the
> level-0 exception can be dropped or re-sorted behind the deny, and SAVES silently loses
> vault access. Re-run the verification below after any permission change in the GUI.

### Which paths `sa_saves` actually needs, and why

Scoped as tightly as the code allows — these three are not interchangeable:

| Path | Access | Why exactly |
|---|---|---|
| `Remote Vault/` | **read + traverse** | `tag_index` does `os.walk(vault_root)` across the *entire* vault to build the tag autocomplete and the taxonomy hint fed to Claude. Without read it silently returns nothing: `/tag add` stops autocompleting and the analyzer starts inventing near-duplicate tags. |
| `Remote Vault/0 - INBOX/SAVES/` | **read + write** | `remove_url_from_inbox()` is an *atomic rewrite*: `tempfile.mkstemp(dir=<same dir>)` then `os.replace()`. That needs **create file** *and* **delete child** on the directory — not merely write on `SAVES.md`. |
| `Remote Vault/SAVES/` | **read + write** | `write_note()` does `os.makedirs(folder_abs)` then writes — it **creates nested folders** (`SAVES/COOKING/RECIPES/…`, per `preferences.json`). Notes land here, *not* in the inbox folder. |

> The write sandbox is `vault_root`, not `saves_root` — a hallucinated `folder_path` could
> aim outside `SAVES/`. Under this ACL that fails with EACCES rather than scattering notes,
> which is the safe outcome, but the error will look like a permissions bug. Worth knowing.

### Verify the ACL actually binds the container

DSM's Permission Inspector shows how *DSM* evaluates the ACL. It does **not** prove the
kernel applies it to a container process that never authenticated through DSM. Confirm
directly — 30 seconds, no risk, and it is the exact operation SAVES performs:

```bash
V="/volume1/APPS/OBSIDIAN/Remote Vault"
for p in "" "/0 - INBOX/SAVES" "/SAVES"; do
  sudo docker run --rm -u 1031:65536 -v "$V$p:/t" alpine \
    sh -c 'touch /t/.acltest 2>/dev/null && { echo "WRITE OK"; rm -f /t/.acltest; } || \
           { ls /t >/dev/null 2>&1 && echo "READ ONLY" || echo "NO ACCESS"; }' \
    | sed "s|^|  ${p:-/ (vault root)} -> |"
done
```

Wanted: vault root **READ ONLY**, both subfolders **WRITE OK**. Anything reading `NO ACCESS`
is a blocker — SAVES will start cleanly and then fail with no useful error.

### The rest of the layout

**`/volume1/docker` is ACL-managed too** (verified 2026-08-06). `/volume1/docker/saves` carries
`user:sa_saves:allow:rwxpdDaARWc--:fd--` at `level:0`, and `fd--` inheritance pushes it down to
`app/`, `app/cookies/`, `app/logs/` and `state/`. **Those need no grants, no `chown` and no
`chmod` of their own** — one ACE covers the subtree.

Ownership stays `OodaAdmin` throughout (SOP Rule 2). **SAVES never checks who owns its files**,
so unlike PostgreSQL it needs no `chown` to start — the ACE alone is sufficient. (The
distinction, and the apps that *do* self-check, are in SOP §5.2.)

| Path | What it holds | Reachable by |
|---|---|---|
| `/volume1/docker/saves` | project root — carries the `sa_saves` ACE that covers everything below | `sa_saves`, `administrators`, `ContainerManager`, `OodaAdmin` |
| `…/saves/app` | the git clone: source + build context, read-only at runtime | inherited |
| `…/app/.env` | **secrets** — `ANTHROPIC_API_KEY`, `DISCORD_BOT_TOKEN` | inherited — and **not** `sa_forgejo`, which has no ACE on this tree † |
| `…/app/docker/.env` | host paths + `SAVES_UID`/`SAVES_GID` — no secrets | inherited |
| `…/app/config.yaml` | configuration, mounted `:ro` | inherited |
| `…/app/cookies` | **credentials** — platform cookies + the provecho browser profile the container *writes* | inherited |
| `…/app/logs` | container logs | inherited |
| `/volume1/docker/saves/state` | `processing_state.json`, `pending_approvals.json`, `preferences.json`, `queue_state.json` | inherited |
| `/volume1/docker/certs/vineyard-root-ca.crt` | public CA certificate for the Forgejo clone | inherited |

† **This is what protects the secrets, not a file mode.** `/volume1/docker` grants
`docker_service_accounts` nothing, and `sa_forgejo` has no ACE — so the forge cannot read
SAVES's API key or session cookies even though both stacks live in the same tree. Same tree
does not mean same trust. Preflight `[7]` asserts `sa_forgejo` is absent from this tree's ACL.

**On `umask`:** `src/main.py` sets `os.umask(0o027)` — files `640`, dirs `750`, never
world-readable. It is **not** what grants anyone access (the ACL is, and its `fd--` flags
propagate to everything SAVES creates); its only job is to make sure nothing SAVES writes is
world-readable if it ever lands on a non-ACL path. Deliberately not `002`.

**`group_add` in compose: removed.** Vault and media access come from DSM ACL entries naming
`sa_saves`, which match on **UID** — group membership is irrelevant to them. Adding the
container to `users` (the everyone-group) is forbidden by **SOP Rule 1**. Preflight `[7]`
fails if GID 100 reappears in a `group_add`.

### ⚠️ The DSM ACL trap — applies to every path above

Containers honour **POSIX** ownership; DSM layers its own **ACLs** on top, invisible from
inside a container. **Never open these folders'
permissions in File Station or Control Panel → Shared Folder → Edit → Permissions.** DSM
rewrites ACLs across the subtree and clobbers the POSIX owner, breaking the container at its
next restart. If you need to change something here, use **`synoacltool`** over SSH — not
`chown`/`chmod`, which on an ACL-managed share can rewrite or drop the ACL (SOP §5.2).

### Verify, always

```bash
# %U = owner name, %G = group name, %a = octal mode
stat -c '%n  %U:%G  %a' "$VAULT_HOST" "$MEDIA_HOST" /volume1/docker/saves/state
```

## 1.7 Risk register

| Risk | Impact | Mitigation |
|---|---|---|
| DEV bot still running at cutover | Both bots fight the one token; double-processing | **Stop DEV** before `up` (step 5). One token, one bot. |
| NAS trust store lacks the step-ca root | `git clone` from Forgejo fails TLS verification | Install the root per `OKAYNET/SELF HOST/forgejo.md` (vault) Phase 11. **Never** work around it with `http.sslVerify=false`. |
| SAVES memory cap over-committed vs Forgejo | OOM kills the forge or SAVES mid-save | `SAVES_MEM_LIMIT` sized per §1.5; preflight `[6]` warns before you deploy. |
| A `cpus:` key gets added to compose | **Hard deploy failure** on Synology | Never set one; preflight `[6]` fails on it. `OKAYNET/SELF HOST/forgejo.md` (vault) §6. |
| DSM firewall deny-all added during the Forgejo build | SMB / Obsidian-Sync to the vault blocked (SAVES itself unaffected — no inbound ports) | Verify vault access after the firewall change; §1.5. |
| Whisper workstation off/unreachable | Video/audio saves get **empty transcripts** (non-fatal) | Preflight `[5]`; DHCP-reserve + auto-start the Whisper server. |
| Cookies mounted read-only | `/crawl` + login sites fail to launch browser | **Fixed:** compose now mounts `cookies :rw`; preflight `[4]` checks writability. |
| Windows provecho profile won't decrypt on Linux | `/crawl` provecho shows locked | §1.4 fallback: re-capture under WSL2/WSLg. |
| `SAVES_UID` ≠ real `id sa_saves` | Container starts, then **every write fails with EACCES** — looks like "notes never appear, no errors" | Read the UID from `id sa_saves` (step 0b), never assume `1031`; preflight **`[7]`** compares it against every directory's owner. |
| Folder created but never granted | Same EACCES failure — the default outcome, since every tree is default-deny | Every step in Part 2 states the grant; verify with `synoacltool -get` **and** the container write test (step 0d). |
| ACL rebuilt from a parent in File Station | The `level:0` `sa_saves` exception is dropped or re-sorted behind the `docker_service_accounts` deny — SAVES silently loses the vault | Never re-apply permissions from a parent (§1.6); preflight `[7]` fails on the ordering; re-run step 0d after any GUI change. |
| `chmod`/`chown` run inside an ACL-managed share | Can strip the ACL, removing the named exceptions entirely | Use `synoacltool` only (SOP §5.2). |
| Permissions "fixed" in File Station afterwards | DSM re-applies the parent ACL across the subtree, dropping the `level:0` named exceptions; container breaks at next restart | The DSM ACL trap (§1.6) — `synoacltool` over SSH only, never the GUI. Re-run step 0d after any GUI change. |
| DEV `processing_state.json` copied by mistake | PROD thinks real-vault URLs are already saved | **Don't copy it.** Only copy `preferences.json` (step 4). |
| Obsidian Sync bridge down | Phone saves + note sync-back stall | Keep the bridging Obsidian client up; the `#SAVES-inbox`/webhook path is independent of it. |
| Synology is arm64 | Heavier/longer first build | Supported (Chromium arm64); ensure ~4 GB free; expect a longer build. |
| First build is slow | 10–30 min | Cached after first build; only re-builds on code change. |

---

# PART 2 — STEP-BY-STEP

> **[YOU]** = manual (SSH / DSM / phone). **[APP]** = automated (compose / the container).
>
> **Every step states the working directory, the owner, and the mode.** Read §1.6 first.
> Shorthand used below: `APP=/volume1/docker/saves/app`.

### Step 0 — [YOU] Verify the service accounts, then create the data directories

Service-account convention (naming, which group, how to create one):
**`OKAYNET/SOP/sop_synology_service_accounts.md` (vault)**. Both accounts this rollout needs already exist.

**0a. Confirm the accounts — these UIDs go into `docker/.env` and every ACL grant below:**

```bash
ssh <you>@192.168.1.201
id sa_saves ; id sa_obsidian
```

Expected (confirmed 2026-08-06):

```
uid=1031(sa_saves)    gid=100(users) groups=100(users),65536(docker_service_accounts)
uid=1032(sa_obsidian) gid=100(users) groups=100(users),65537(app_service_accounts)
```

> ⚠️ If either UID differs, **stop** and substitute the real number everywhere below and in
> `docker/.env`. DSM assigns UIDs; you do not choose them.

**Why two accounts:** the vault lives in the **APPS** tree, which belongs to **Obsidian**
(`sa_obsidian`). SAVES reaches it as a **named ACL exception** — never by taking ownership and
never through a shared group. The media store and SAVES's own state belong to `sa_saves`.
Scheme: **`OKAYNET/SOP/sop_synology_service_accounts.md` (vault) §5**; worked example §6.

**0b. Create the directories.** Ownership stays with `OodaAdmin` / `administrators`
(SOP Rule 2) — a service account is **granted** access by an ACE, never given ownership.

```bash
VAULT="/volume1/APPS/OBSIDIAN/Remote Vault"
MEDIA="/volume1/MEDIA/SAVES"

sudo mkdir -p "$VAULT/0 - INBOX/SAVES" "$VAULT/SAVES" "$MEDIA"
sudo touch    "$VAULT/0 - INBOX/SAVES/SAVES.md"
```

> ⚠️ **Moving the real vault in later will not carry these ACLs.** A *copy* into the folder
> inherits the destination ACL; a *move* within the same volume keeps the source's ACL, which
> is almost certainly wrong. **Re-run 0c and 0d after the vault is actually in place** — this
> is the single easiest way to end up with a silently broken deploy.

**0c. Grant `sa_saves` — three separate grants, least verb each.**

`docker_service_accounts` is denied across `/volume1/APPS` by design, so `sa_saves` needs an
explicitly-set (`level:0`) allow ACE on each folder. Nothing is granted at the tree root, and
no group is created:

```bash
# Read + traverse ONLY — tag_index walks the entire vault; it never writes here:
sudo synoacltool -add "$VAULT"                 "user:sa_saves:allow:r-x---a-R-c--:fd--"

# Read + write — the inbox rewrite is tempfile+os.replace, so it needs create AND delete-child:
sudo synoacltool -add "$VAULT/0 - INBOX/SAVES" "user:sa_saves:allow:rwxpdDaARWc--:fd--"

# Read + write — write_note() does os.makedirs(); notes and their nested folders land here:
sudo synoacltool -add "$VAULT/SAVES"           "user:sa_saves:allow:rwxpdDaARWc--:fd--"

# Media: SAVES writes it; sa_obsidian only needs to read it for media:// rendering.
sudo synoacltool -add "$MEDIA" "user:sa_saves:allow:rwxpdDaARWc--:fd--"
sudo synoacltool -add "$MEDIA" "user:sa_obsidian:allow:r-x---a-R-c--:fd--"
```

Verify the permission strings landed as intended — do not trust them unread:

```bash
sudo synoacltool -get "$VAULT/0 - INBOX/SAVES"
```

The `user:sa_saves:allow` ACE must appear **above** any
`group:docker_service_accounts:deny`, and be `level:0` (explicitly set on this folder, not
inherited). If it sorts below the deny, SAVES is blocked.

**0d. Verify from inside a container — the only test that proves it.**

`synoacltool -get` and DSM's Permission Inspector show how *DSM* evaluates the ACL. Neither
proves the kernel applies it to a container process, which never authenticated through DSM.

> ✅ **Proven on this NAS (2026-08-06).** Three container tests all passed: `sa_saves`
> (1031) **wrote** to `/volume1/MEDIA/SAVES`; `sa_forgejo` (1030) was **blocked** on the same
> path with the same GID; an unused UID 9999 was **blocked** by default-deny. So DSM ACLs bind
> containers, they match on **UID** (not group), denies and default-deny both hold, and a
> `level:0` allow beats an inherited group deny. Full matrix: `OKAYNET/SOP/sop_synology_service_accounts.md` (vault) §5.1.
> Run the same tests on the vault paths once the vault is in place:

```bash
for p in "" "/0 - INBOX/SAVES" "/SAVES"; do
  sudo docker run --rm -u 1031:65536 -v "$VAULT$p:/t" alpine \
    sh -c 'touch /t/.acltest 2>/dev/null && { echo "WRITE OK"; rm -f /t/.acltest; } || \
           { ls /t >/dev/null 2>&1 && echo "READ ONLY" || echo "NO ACCESS"; }' \
    | sed "s|^|  ${p:-/ (vault root)} -> |"
done
sudo docker run --rm -u 1031:65536 -v "$MEDIA:/t" alpine \
  sh -c 'touch /t/.acltest && rm -f /t/.acltest && echo "  media -> WRITE OK" || echo "  media -> BLOCKED"'
```

| Path | Wanted |
|---|---|
| `/ (vault root)` | **READ ONLY** — write here would mean the grant is too broad |
| `/0 - INBOX/SAVES` | **WRITE OK** |
| `/SAVES` | **WRITE OK** |
| `MEDIA` | **WRITE OK** |

Any `NO ACCESS` is a blocker: SAVES would start cleanly and then fail with no useful error.
`preflight_nas.sh [7]` re-checks the ACL ordering before every deploy.

> ⚠️ **From here on, never touch these folders' permissions in File Station or Control
> Panel → Shared Folder → Permissions.** DSM will rewrite ACLs across the subtree and
> clobber the POSIX owner (§1.6, the DSM ACL trap).

### Step 1 — [YOU] Get the code onto the NAS (from Forgejo)

Enable SSH (DSM → Control Panel → Terminal & SNMP → Enable SSH), then `ssh <you>@192.168.1.201`.

**1a. Trust the step-ca root on the NAS** — `git` will not talk to the forge without it.

Store it in **one** place, on `/volume1`, and point Git at that file. Deliberately **not**
`/etc/ssl/certs`: DSM rewrites `/etc` on package and OS updates, which would silently break
`git pull` months later. One canonical copy, one config key, survives upgrades.

```bash
# from the WORKSTATION — copy to your home dir (the only place scp can write unprivileged):
scp vineyard-root-ca.crt <you>@192.168.1.201:~/

# on the NAS — move it to its permanent home and set ownership explicitly:
sudo mkdir -p /volume1/docker/certs
sudo mv ~/vineyard-root-ca.crt /volume1/docker/certs/
# /volume1/docker is ACL-managed — the new dir inherits the tree ACL; nothing to chmod.
sudo git config --system http.sslCAInfo /volume1/docker/certs/vineyard-root-ca.crt
```

Verify before continuing — this must succeed with **no** TLS error and **no** `-k`:

```bash
git --version          # confirm git is present on the NAS before you rely on it

# Authoritative check — proves the cert chain validates against your root:
curl -fsS --cacert /volume1/docker/certs/vineyard-root-ca.crt \
  https://192.168.1.201:3443/api/v1/version
```

`curl` needs `--cacert` because it reads the *system* trust store, which we deliberately did
not modify. **Git** gets its trust from the `http.sslCAInfo` key set above, so the clone in
1b needs no extra flags — that asymmetry is expected, not a misconfiguration.

> ⚠️ If it fails, fix the trust store. **Do not set `http.sslVerify=false`** — that discards
> the entire point of the certificate work and silently accepts any MITM on the LAN.

**1b. Clone the repo.**

> **Yes, SAVES runs as a Docker container** — `saves_app`, defined by
> `docker/docker-compose.yml`, exactly like the Forgejo stack next door. `/volume1/docker/saves/`
> is therefore the correct home: it is a **container project directory**, the same role
> `/volume1/docker/forgejo/` plays.
>
> What the clone *is*, precisely: `docker compose up --build` reads it as the **build context**,
> produces an image, and runs that image as the container. The clone then keeps serving the
> container at **runtime** through three bind mounts — `config.yaml`, `cookies/`, and `logs/` —
> which is why it stays on disk permanently rather than being consumed by the build. That dual
> role (build context *and* live mount source) is the only reason this looks different from a
> plain "docker pull an image" deployment.

Use a **PAT** (Forgejo → Settings → Applications → *Manage Access Tokens*, scope
`repository: Read` — a deploy-only token, separate from your workstation token):

```bash
sudo mkdir -p /volume1/docker/saves
cd /volume1/docker/saves      # already ACL-managed; do NOT chown/chmod it (SOP §5.2)
sudo git clone https://192.168.1.201:3443/<user>/SAVES.git app
uname -m          # expect x86_64 (aarch64 also works, heavier build)
```

Username = your Forgejo username; password = **the token**. To avoid re-typing on every
`git pull`: `sudo git config --global credential.helper store` (plaintext in
`/root/.git-credentials`, mode 600 — acceptable on a NAS only you administer).

### Step 2 — [YOU] Secrets + host paths (two gitignored files)

Both files live **inside the clone**, not at the root of `/volume1/docker`:

| File | Absolute path | Holds |
|---|---|---|
| repo `.env` | `/volume1/docker/saves/app/.env` | **secrets** — API key, bot token |
| `docker/.env` | `/volume1/docker/saves/app/docker/.env` | host paths + UID/GID + TZ — **no secrets** |

```bash
cd /volume1/docker/saves/app          # ← all paths below are relative to HERE

sudo cp .env.example .env
sudo vi .env                          # ANTHROPIC_API_KEY=... DISCORD_BOT_TOKEN=... (same as DEV)
# NO chown/chmod: /volume1/docker/saves carries an inherited sa_saves ACL (fd-- flags), and
# chmod on an ACL-managed path can strip it. sa_forgejo has no access to this tree at all.

sudo cp docker/.env.example docker/.env
sudo vi docker/.env                   # set SAVES_UID from step 0b; confirm VAULT_HOST/MEDIA_HOST
# (no chown/chmod — inherited ACL, see above)
```

Leave `Remote Vault` **unquoted** despite the space — the compose file quotes the mount.

Verify:
```bash
sudo synoacltool -get .env | grep -E 'sa_saves|sa_forgejo'
# want: a user:sa_saves:allow ACE; NO sa_forgejo access
```

### Step 3 — [YOU] Carry cookies + the provecho profile into `cookies/`

The clone has no cookies (gitignored). These are **credentials** — the container must *write*
the browser profile, so `sa_saves` owns the directory and nobody else may read it.

```bash
# from the WORKSTATION (scp into your home dir — you cannot scp directly into a root-owned dir):
scp cookies/*.txt <you>@192.168.1.201:~/saves-cookies/
scp -r cookies/provecho.co_profile <you>@192.168.1.201:~/saves-cookies/
```
```bash
# on the NAS:
cd /volume1/docker/saves/app
sudo mkdir -p cookies
sudo cp -r ~/saves-cookies/. cookies/
# NO chown/chmod — the sa_saves ACE on /volume1/docker/saves inherits down to cookies/.
# Verify instead:
sudo synoacltool -get cookies | grep -E 'sa_saves|sa_forgejo'
```

**Shrink the profile (optional):** most of the ~160 MB is disposable cache. Auth lives in
`Default/IndexedDB`, `Default/Local Storage`, `Default/Preferences`, and top-level `Local State`.
You may exclude `Default/Cache`, `Default/Code Cache`, `Default/GPUCache`, `Default/Service Worker/CacheStorage`.

> If `/crawl` later shows provecho **locked** in PROD, this profile didn't port cleanly — re-capture under **WSL2/WSLg** (§1.4) and recopy. This is expected-possible; nothing else depends on it.

### Step 4 — [YOU] State dir + carry preferences (fresh dedup, keep prefs)

```bash
sudo mkdir -p /volume1/docker/saves/state
# NO chown/chmod — inherited from the /volume1/docker/saves ACL.

# keep learned folder routing (vault-relative paths → portable):
#   from the WORKSTATION:  scp preferences.json <you>@192.168.1.201:~/
sudo mv ~/preferences.json /volume1/docker/saves/state/
# (inherits the sa_saves ACE — nothing to set)

sudo synoacltool -get /volume1/docker/saves/state | grep sa_saves
# want: a user:sa_saves:allow ACE with write (inherited from the project root)
```

**Do NOT copy** `processing_state.json`, `pending_approvals.json`, or `queue_state.json` from DEV — PROD starts with an **empty** save-history on purpose (the real vault doesn't have DEV's test saves). `queue_state.json` is created empty on first run.

### Step 4b — [YOU] The `logs/` directory the container writes

```bash
cd /volume1/docker/saves/app
sudo mkdir -p logs      # inherits the sa_saves ACE from /volume1/docker/saves
```
Without this, compose creates `logs/` as **root** on first `up` and the non-root container
cannot open `logs/processor.log` — startup fails immediately.

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
Paste one URL into the **real** inbox `/volume1/APPS/OBSIDIAN/Remote Vault/0 - INBOX/SAVES/SAVES.md` (via Obsidian or `echo >>`), watch for the approval card in **#SAVES-approvals**, approve, and confirm the note lands in the real vault and the inbox line is removed.

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

**Is the NAS the right home? Measure, don't guess.** Every save logs one `TIMING` line with
per-stage wall-clock:

```bash
sudo docker compose -f docker/docker-compose.yml logs | grep TIMING
# TIMING [provecho] total=48.3s extract=31.2s download=6.1s transcribe=8.0s vision=2.4s analyze=0.6s | https://…
```

`extract` (Chromium) and `vision` (ffmpeg) are the **local-CPU** stages; `download`,
`transcribe` and `analyze` are mostly network waits. If `extract`+`vision` come to dominate,
or the NAS feels sluggish over SMB while a save runs, that's the signal to move the pipeline
to the workstation — same code, a `config.local.yaml` away (`ARCHITECTURE.md` §1b,
`ROADMAP.md` Phase 5). Synology cannot cap CPU (§1.5), so this log is the only warning you get.

**Health signals:** the container is `restart: unless-stopped`; `docker compose ps` shows it Up. Alerts (cookie expiry, extraction failures, unreachable Whisper) post to **#SAVES-alerts**; the pipeline log lives in `logs/` and `#SAVES-logs`.

---

# PART 7 — Optional later (NOT needed to run)

Docker Compose above **is** the whole deployment. If you later want conveniences, each is
additive and changes nothing about how SAVES runs — ask for steps when you want them:

- **Dozzle** — a tiny container that reads the Docker socket and shows live logs in a browser (nicer than `logs -f`).
- **Komodo** — a web UI to manage/restart/monitor the stack and its env from one place.
- **DOCO-CD** — GitOps: watches this repo and auto-redeploys the compose stack on `git push` (secrets stay off-repo in the NAS `.env`).

None are prerequisites; they'd sit *around* the same `docker/docker-compose.yml`.
