# Deploying SAVES to the Synology NAS (Docker)

> ▶ **Superseded by `docs/PROD_ROLLOUT.md`** — the fuller guided rollout that adds the serial
> approval queue, `#SAVES-inbox` + the Android/Tasker webhook, the `/crawl` provecho-profile
> setup, a full acceptance-test matrix, and rollback. Use **PROD_ROLLOUT.md** for a live
> deploy. This file remains as the terse core-install reference; the two critical deltas it now
> reflects: cookies mount is **`:rw`** (browser profiles need write), and `/crawl` needs the
> **provecho profile** copied to the NAS (step 3).

The step-by-step for standing up **PROD** — the pipeline running as a Docker container on the
NAS, against the **real** Obsidian vault. This is a *fresh implementation*, not a migration:
the real vault and media already live on the NAS; DEV's state describes a different (test)
vault, so PROD starts with an empty dedup memory. (Architecture rationale: `ARCHITECTURE.md`
§1b.)

> **Who does what.** Steps marked **[YOU]** are manual (SSH, DSM UI, physical firewall).
> Steps marked **[APP]** are automated (compose, the container). Claude prepared the
> `.dockerignore`, `scripts/preflight_nas.sh`, and this runbook; the rest is hands-on-NAS.

---

## 0. Before you start — the three things that bite

1. **One Discord token = one bot.** Discord allows a bot token exactly one gateway
   connection. If your DEV `python src\main.py` is still running on the workstation when the
   PROD container starts, the two bots fight over the connection **and** both try to process
   the same channels. **For the live test, stop the DEV bot.** (The Whisper server is a
   *separate* Flask process with no Discord connection — leave it running; PROD needs it.)
   Long-term, if you want DEV and PROD both live, they need separate bot tokens + channels —
   decide that later; for go-live, just stop DEV.
2. **Never run DEV and PROD against the same vault/state at once** — two watchers + two state
   writers on one inbox will corrupt each other. Today DEV uses its own test vault, so simply
   stopping the DEV bot is enough.
3. **Whisper lives on the workstation, not the NAS.** The container POSTs audio to
   `http://192.168.1.90:5000/transcribe`. That only works if the workstation server is
   running **and** Windows Firewall allows inbound TCP 5000 from the LAN (step 5).

**You will need:** SSH access to the NAS (admin), your `ANTHROPIC_API_KEY` and
`DISCORD_BOT_TOKEN`, and your `cookies/*.txt` files from the workstation.

---

## 1. [YOU] Get the code onto the NAS

Enable SSH (DSM → **Control Panel → Terminal & SNMP → Enable SSH service**), then from the
workstation:

```bash
ssh <you>@192.168.1.201          # the NAS (also hosts the Forgejo forge)
```

Pick a working dir on a real volume (not `/tmp`) and clone:

```bash
sudo mkdir -p /volume1/docker/saves && cd /volume1/docker/saves
sudo git clone https://192.168.1.201:3443/<user>/SAVES.git app
cd app
```

> **The remote is the self-hosted Forgejo forge on this same NAS** (GitHub retired
> 2026-08-05). Two prerequisites the clone will fail without: the **step-ca root CA** in the
> NAS trust store, and a **PAT** (`repository: Read`) as the password. Full sequence with the
> `curl` verification step: `PROD_ROLLOUT.md` step 1. Forge build: `docs/FORGEJO.md`.

- No `git` on the NAS? Either install **Git Server** from Package Center, or copy the repo
  over SMB from the workstation into `/volume1/docker/saves/app` (skip `.git` if you do).
- **Verify architecture:** `uname -m` should print `x86_64`. If it prints `aarch64` the image
  still builds (Playwright/Chromium support arm64) but the build is heavier — expect a longer
  first build and make sure the volume has ~4 GB free.

## 2. [YOU] Secrets and host paths (two small files, both gitignored)

**Secrets** — repo root `.env` (same two keys as DEV; copy them from your workstation `.env`):

```bash
cp .env.example .env
vi .env          # set ANTHROPIC_API_KEY=... and DISCORD_BOT_TOKEN=...
```

**Host paths** — `docker/.env` maps the canonical container paths to real NAS locations. The
example already has the PROD values; confirm they match your NAS:

```bash
cp docker/.env.example docker/.env
cat docker/.env
```

```
VAULT_HOST=/volume1/APPS/OBSIDIAN/Remote Vault     # ← your real vault (note the space)
MEDIA_HOST=/volume1/MEDIA/SAVES               # ← where downloaded media goes
STATE_HOST=/volume1/docker/saves/state            # ← runtime JSONs (created in step 4)
TZ=America/New_York
```

> Leave the values **unquoted even though `Remote Vault` has a space** — docker-compose's
> env-file parser takes the whole line after `=`. The compose file quotes the mount, so the
> space is handled. Adjust any path that doesn't match your NAS's actual shares.

## 3. [YOU] Carry over cookies (and optionally learned preferences)

Cookies are gitignored, so the clone has none. Copy your workstation files into the NAS repo's
`cookies/` dir (SMB drag-drop, or `scp` from the workstation):

```bash
# from the WORKSTATION:
scp cookies/*.txt <you>@192.168.1.201:/volume1/docker/saves/app/cookies/
# for /crawl — the provecho browser profile (large; cross-OS caveat in PROD_ROLLOUT §1.4):
scp -r cookies/provecho.co_profile <you>@192.168.1.201:/volume1/docker/saves/app/cookies/
```

The `cookies/` mount is **`:rw`** (a browser profile is a live Chromium user-data-dir Playwright
writes to). If `/crawl` shows provecho "locked" in PROD, the Windows-captured profile didn't port
— re-capture under Linux (WSL2/WSLg); see `PROD_ROLLOUT.md` §1.4.

Optional: copy `preferences.json` into the **state dir** (step 4) to keep learned folder
routing — it stores vault-relative paths, so it transfers cleanly. Do **not** copy
`processing_state.json` / `pending_approvals.json` from DEV (they describe test-vault saves;
starting empty lets the same URLs be saved "for real").

## 4. [YOU] Create the state directory

```bash
sudo mkdir -p /volume1/docker/saves/state
# if you copied preferences.json, drop it in here now:
# sudo cp /path/to/preferences.json /volume1/docker/saves/state/
```

This one **directory** holds all runtime JSONs. Never bind single files (see the note in
`docker-compose.yml`).

## 5. [YOU] Start Whisper + open the firewall (on the WORKSTATION)

On the workstation (PowerShell):

```powershell
# start the server (leave this window running):
python scripts\whisper_server.py --model large-v3-turbo --port 5000

# one-time: allow the NAS to reach it (run as Administrator):
New-NetFirewallRule -DisplayName "SAVES Whisper 5000" -Direction Inbound `
  -Protocol TCP -LocalPort 5000 -Action Allow -Profile Private
```

Confirm the workstation's LAN IP is still `192.168.1.90` (`ipconfig`); if it changed, update
`transcription.remote_url` in `config.yaml`. Consider a DHCP reservation so it stays put.

## 6. [YOU] Stop the DEV bot

On the workstation, stop `python src\main.py` (Ctrl-C in its window). Leave the Whisper server
from step 5 running.

## 7. [YOU] Pre-flight, then [APP] build & launch

From the NAS repo root:

```bash
sh scripts/preflight_nas.sh
```

Fix any **FAIL** lines (missing mount, unwritable state dir, unreachable Whisper) before
continuing — that's the whole point of the check. When it prints **PASSED**:

```bash
sudo docker compose up --build -d          # DSM 7.2+ Container Manager (compose v2)
# older DSM / Docker package:  sudo docker-compose up --build -d
```

First build downloads the base image, apt packages, pip deps, and a Chromium for Playwright —
**10–30 min** on a NAS, needs a few GB free. It's cached after that.

## 8. [YOU] Verify

```bash
sudo docker compose logs -f
```

You want to see startup validation pass and the bot connect — **no** `ConfigError`, and a line
like `Slash commands synced to N guild(s)`. Then a real end-to-end save:

1. Paste one URL into the **real** inbox: `/volume1/APPS/OBSIDIAN/Remote Vault/0 - INBOX/SAVES/SAVES.md`
   (edit in Obsidian, or `echo` it in).
2. Within ~3 s the container picks it up → an approval card appears in **#SAVES-approvals**.
3. Approve it → confirm the note is written into the real vault and the URL leaves the inbox.
4. Paste the same URL again → confirm the **duplicate notice** appears in #SAVES-approvals.

That's PROD live. DEV can stay stopped, or come back later on its own token/channels.

---

## Operating notes

| Task | Command (from NAS repo root) |
|---|---|
| Follow logs | `sudo docker compose logs -f` |
| Restart | `sudo docker compose restart` |
| Stop | `sudo docker compose down` (keeps volumes; state/vault are host mounts, untouched) |
| Update to a new commit | `git pull && sudo docker compose up --build -d` |
| Shell into the container | `sudo docker exec -it saves_app sh` |
| Change config (no rebuild) | edit `config.yaml`, then `sudo docker compose restart` (it's a read-only mount, not baked in) |

## Troubleshooting

| Symptom | Likely cause / fix |
|---|---|
| `ConfigError: paths.* not set` at startup | `config.yaml` mount missing or a channel/path empty — preflight [2]/[3] catches this |
| Bot connects then disconnects repeatedly | DEV bot still running with the same token (step 6) |
| Approval cards appear but transcripts are empty | Whisper unreachable — preflight [5]; check the workstation server + firewall (step 5) |
| `os.replace` / EBUSY errors on state | `STATE_HOST` was bound as a file, not a directory (step 4) |
| Notes never appear, no errors | vault not writable **by the container's UID**. The container runs non-root as `sa_saves`; if the vault is owned by anyone else every write fails with EACCES. Preflight `[7]`; fix per `PROD_ROLLOUT.md` §1.6 |
| `PermissionError` / EACCES on state or logs | those dirs were created as admin (or by compose as root) and never chowned to `SAVES_UID` — `PROD_ROLLOUT.md` steps 4 / 4b |
| Notes appear but Obsidian can't edit them | vault missing the **setgid** bit, so new notes got the service group — `chmod 2775` on the vault (§1.6) |
| `Executable doesn't exist` from Playwright | image built before the non-root change; rebuild so Chromium lands in `PLAYWRIGHT_BROWSERS_PATH=/opt/playwright` |
| Instagram/TikTok/Facebook fail immediately | cookies missing/expired — preflight [4]; re-export and copy into `cookies/` |
| Build OOM / disk full on NAS | free space on the volume; if arm64, expect a heavier Chromium build |

Cross-references: portability model → `ARCHITECTURE.md` §1b; Whisper runbook → `HANDBOOK.md`
§9.1; user-facing button/notice behavior → `USER_GUIDE.md`.
