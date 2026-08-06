# Forgejo on Synology DS1621+ — Hardened Non-Root Build (v3)

> **Scope:** Forgejo 15 LTS + PostgreSQL 17 in Synology Container Manager on DSM 7.3.2-86009 U4.
> LAN-only, Forgejo-terminated HTTPS with step-ca certs, HTTPS-only Git with PATs, no SSH exposure.
> **Status:** v3 — corrected against an actual build on hardware. Supersedes v1 and v2.
> **Verification date:** 2026-08-05.

---

## v3 revision log — corrections found during the real build

Everything below was discovered by deploying this on actual hardware, not from documentation.

| # | v2 said | v3 says | Severity |
|---|---|---|---|
| 1 | `cpus: 2.0` / `cpus: 1.0` in Compose | **Removed.** Synology kernels lack CFS bandwidth control; any CPU quota is a **hard build failure**: `NanoCPUs can not be set…` | 🔴 **Blocked deployment** |
| 2 | Repo root = `/var/lib/gitea/data/forgejo-repositories` | **`/var/lib/gitea/git/repositories`.** The rootless image overrides the cheat sheet's generic default — trust the installer's prefilled value | 🔴 Wrong path |
| 3 | Enter `git` for Run As Username; `$USER` fallback saves you | **False.** `os/user.Current()` does a real `getpwuid()` and returns `""` for an unmapped UID. No typed value can pass. **New Phase 4B mounts a corrected `/etc/passwd`** | 🔴 **Blocked install** |
| 4 | Strip `RUN_USER` from `app.ini` (Step 8.6) | **Obsolete.** With Phase 4B, `git` is genuinely UID 1030's name and `RUN_USER = git` is valid permanently | 🟡 Workaround removed |
| 5 | `INSTALL_LOCK` + CLI admin as the fallback | Kept for the Terraform rebuild, but **no longer needed here.** The `MustInstalled` error it produced was just a **missing `-c` flag** on the exec'd CLI, not corrupted state | 🟡 Bad diagnosis corrected |
| 6 | `ports: "3443:3000"` | **`"192.168.1.201:3443:3000"`.** The short form also binds IPv6 (`::`), which the IPv4-subnet firewall rule does not cover | 🟡 Firewall bypass |
| 7 | Certs at `custom/https/{cert,key}.pem`, defaults implied | **`server.crt` / `server.key`** from step-ca, with `CERT_FILE`/`KEY_FILE` set explicitly | 🟢 Environment-specific |
| 8 | — | **New:** seccomp is `unconfined` DSM-wide — an honest caveat that can't be fixed from Compose | 🟢 Disclosure |
| 9 | — | **New:** rootless vs unprivileged vs non-root defined precisely (§3) | 🟢 Clarity |
| 10 | — | **New:** installer field guidance for Server / third-party / email sections | 🟢 Completeness |
| 11 | — | **New:** Windows/PowerShell test commands (no netcat on Windows) | 🟢 Practicality |

**Confirmed working on this build:** `2222` → refused, `3443` → succeeded, `docker exec forgejo id` → `uid=1030(git) gid=65536(git)`.

---

## Earlier revision log (v1 → v2)

| # | v1 said | v2 says | Impact |
|---|---|---|---|
| 1 | Mounted a second volume `config:/etc/gitea` and put certs at `/etc/gitea/https/` | **`/etc/gitea` is removed in Forgejo v15.** Config lives at `/var/lib/gitea/custom/conf/app.ini`. Certs go at `/var/lib/gitea/custom/https/` | **This was a real bug.** v1's compose would have started with config in a path Forgejo v15 no longer reads |
| 2 | "GID 65536 is not the `users` group, that's GID 100 — verify" | Your `id` output resolves it: **primary GID 100 (`users`), supplementary GID 65536 (`docker_service_accounts`)**. We deliberately run the container as `1030:65536` | Tighter than GID 100. Explained in §1 |
| 3 | Long section correcting "rootless Docker" | You meant **rootless containers**, which is exactly what this builds. Correction retracted; short honest note retained because you want true rootless Docker later | Removed ~600 words of misdirected lecture |
| 4 | `getent group 65536` | **`getent` does not exist on DSM.** Use `id`, `synogroup --get`, or `/etc/group` | v1 gave you a command that can't run |
| 5 | Recommended `INSTALL_LOCK=true` from first boot + CLI-only admin | **Web installer first, then lock.** Your instinct was right | See Decision B |
| 6 | Forgejo 15.0.5 | Still **15.0.5** — see the note below about 15.0.6 | — |
| 7 | Container Manager 24.0.2-1543 | **24.0.2-1606** (yours). 24.0.2-1630 exists for some models | Corrected |
| 8 | — | **New:** Claude Code integration section | You asked; it has non-obvious CA-trust behaviour |

### On the version numbers you gave me

- **PostgreSQL 17.10** — ✅ confirmed. Released 14 May 2026 alongside 18.4, 16.14, 15.18 and 14.23, fixing 11 security vulnerabilities and over 60 bugs. Current 17.x patch.
- **Container Manager 24.0.2-1606** — ✅ confirmed as a real, current build. Synology's KB notes that version 24.0.2-1630 was released for specific models only, and that incompatible models should install 24.0.2-1606 or another compatible version. Worth checking whether the DS1621+ is on that exclusion list — if not, 1630 is available to you.
- **Forgejo 15.0.6** — ⚠️ **I can't confirm this exists.** As of today the Forgejo releases page lists **v15.0.5 (15 July 2026)** as the newest 15.x, and the Codeberg container registry's newest 15-line tags are `15.0.5` / `15.0.5-rootless`, published 2026-07-15. The 16.x line is at 16.0.1. If you have a source showing 15.0.6, send it and I'll correct — otherwise this is likely a transposition. **This does not affect the build:** we pin the floating `15` tag, which resolves to the newest 15.0.x automatically, so you get 15.0.6 the moment it ships.

### Assumptions I'm still making — correct any that are wrong

| # | Assumption | Change it by |
|---|---|---|
| A1 | You have no internal DNS record for the NAS yet, so the cert is built around **IP 192.168.1.201** with hostnames as extra SANs | Tell me your intended hostname; edit the SAN block in Phase 3 |
| A2 | Volume 1 (`/volume1/docker`) is the right home for this | Substitute your volume number throughout |
| A3 | Host port **3443** is free | Verified in Phase 1 Step 5 |
| A4 | You want the CA private key kept off the NAS | Phase 3 does this; skip the move step if you disagree |

---

## 0 · Verified version matrix

| Component | Pin | Resolves to | Support window | Source |
|---|---|---|---|---|
| Forgejo | `codeberg.org/forgejo/forgejo:15-rootless` | 15.0.5-rootless | **LTS to 15 July 2027** | forgejo.org/releases |
| PostgreSQL | `postgres:17-alpine` | 17.10 | 17.x supported to Nov 2029 | postgresql.org |
| DSM | 7.3.2-86009 U4 | — | yours | — |
| Container Manager | 24.0.2-1606 | Docker Engine 24.0.2, Compose V2 | Synology-maintained | Synology KB |

**Why LTS `15` and not `16`:** Forgejo v15.0 is a Long Term Support release supported until 15 July 2027, whereas major releases are published every three months. The 16 line gets ~3 months before 17 supersedes it. For a system you want to *build once and not rework*, LTS is the defensible call. The **15** tag tracks the latest patch release automatically, while upgrading from X to X+1 requires a manual operation and human verification.

---

## 1 · The identity model

Your `id sa_forgejo` output:

```
uid=1030(sa_forgejo) gid=100(users) groups=100(users),65536(docker_service_accounts)
```

This tells us three things:

1. **UID 1030** — your target, above 1000. ✅
2. **Primary GID is 100 (`users`)** — DSM forces this on every account and provides no GUI to change it. This is not a mistake on your part; it's a DSM constraint.
3. **GID 65536 is your custom `docker_service_accounts` group** — a supplementary group.

> **Note on the group name:** this group was originally created as `service accounts` and later renamed to `docker_service_accounts`. **Renaming a DSM group does not change its GID** — it stays 65536, so nothing in the container configuration is affected. The compose `user: "1030:65536"`, every `chown`, and the mounted `/etc/group` all reference the *number*, not the name.
>
> This is the same principle as §2's two-passwd-file model: **numbers cross the boundary, names don't.** The container's `/etc/group` maps 65536 to the name `git` (its own namespace's choice) while DSM maps the same GID to `docker_service_accounts`. Both are correct simultaneously, because the only thing the kernel enforces is `65536`.

### The decision: which GID does the container run as?

Docker's `user: "UID:GID"` sets the process's **effective primary GID directly**, independent of what DSM thinks. It does *not* inherit supplementary groups from the host. So we get to choose:

| | `user: "1030:100"` | **`user: "1030:65536"`** ← chosen |
|---|---|---|
| Matches DSM primary group | ✅ | ❌ (irrelevant inside container) |
| Group membership breadth | `users` — **every DSM account is in it** | `docker_service_accounts` — only what you put there |
| If group-read leaks on a data file | Readable by every NAS user | Readable only by docker_service_accounts members |
| Meets your "1000+ UID and GID" goal | ❌ GID 100 | ✅ GID 65536 |
| Works | ✅ | ✅ |

**Locked: `user: "1030:65536"`, and all data directories `chown`ed to `1030:65536`.** The DSM-side primary group of 100 is irrelevant once the container is running — DSM assigns it, we override it at container start. Worth a paragraph in the blog post: it's a case where the container boundary lets you get tighter isolation than the host OS will give you natively.

### `getent` doesn't exist on DSM

DSM ships BusyBox userland without `getent`. Equivalents:

```bash
id sa_forgejo                          # what you already ran — the authoritative check
grep 65536 /etc/group                  # resolve a GID to a name
synogroup --get docker_service_accounts     # DSM-native group inspection
synouser --get sa_forgejo              # DSM-native user inspection
awk -F: '$3==70' /etc/passwd           # check whether UID 70 is taken (needed in Phase 4)
```

---

## 2 · Why every path says "gitea" — this is correct, not a mix-up

You flagged this and it's a fair thing to flag. Here's the evidence.

Forgejo is a hard fork of Gitea and has **not** renamed its runtime paths. The container image still uses `/var/lib/gitea` as the work directory, and `app.ini` as the config filename. This is deliberate backward compatibility, not leftover Gitea config on my part.

Three primary-source confirmations:

1. **The official Forgejo Docker install page**, in its own rootless example, shows the volume as `- ./forgejo:/var/lib/gitea` and describes it as "Rootless image uses a different path for the data folder for Forgejo."
2. **Forgejo issue #10215** ("Rootless Docker image does not respect FORGEJO_WORK_DIR") documents that if you try to use un-branded paths, the rootless image fails with `mkdir: can't create directory '/var/lib/gitea/custom/': Permission denied` and `/var/lib/gitea/custom/conf is not writable`. The reporter's requested fix — making the rootless image honour `$FORGEJO_WORK_DIR` — is still open. **You cannot move off the gitea-named paths in the rootless image.**
3. Some env vars retain a `GITEA_` prefix (e.g. `GITEA_APP_INI`) even in v15.

### The critical v15 change (and v1's bug)

The v15.0.0 release notes state: In Forgejo v8.0.0 the default config file location changed from `/etc/gitea/app.ini` to `/var/lib/gitea/custom/conf/app.ini`. Backward compatibility logic and startup warnings were added to container setup and entrypoint scripts — **now they are removed**. This change only affects container deployments using rootless images. An unused volume `/etc/gitea` can be safely removed from the container.

**v1 of this document mounted `/etc/gitea` and put the TLS certs there. On Forgejo 15 rootless, that path is dead.** v2 uses one data volume only.

Resulting layout inside the container:

| Purpose | Container path | Host path |
|---|---|---|
| Work dir (everything) | `/var/lib/gitea` | `/volume1/docker/forgejo/data` |
| Config | `/var/lib/gitea/custom/conf/app.ini` | `…/data/custom/conf/app.ini` |
| TLS cert/key | `/var/lib/gitea/custom/https/server.{crt,key}` | `…/data/custom/https/` |
| **Repos** | **`/var/lib/gitea/git/repositories`** | `…/data/git/repositories` |
| LFS + packages/registry | `/var/lib/gitea/data/{lfs,packages}` | `…/data/data/…` |

> ⚠️ **Repo path corrected.** The config cheat sheet's generic default (`%(APP_DATA_PATH)s/forgejo-repositories`) does **not** apply to the rootless image, which sets `HOME=/var/lib/gitea/git` and stores repos under `git/repositories`. Confirmed by Forgejo issue #4269, where a successful rootless run produces exactly `gitea/git` and `gitea/custom`. **Trust the installer's prefilled values over the cheat sheet for this image.**

**Why certs at `custom/https/`:** the config cheat sheet gives `CERT_FILE` default `https/cert.pem` and `KEY_FILE` default `https/key.pem`, and states **"Paths are relative to *CustomPath*."** For the rootless image `CustomPath` = `/var/lib/gitea/custom`. So dropping the files at that default location means **we never have to set `CERT_FILE`/`KEY_FILE` at all** — fewer keys, fewer failure modes, no nested bind mounts for Container Manager to reject.

---

## 3 · Rootless vs unprivileged vs non-root — three different things

These get conflated constantly. Precision here is what makes the blog post defensible.

| Term | Means | Your status |
|---|---|---|
| **Rootless** | The *daemon* runs as a non-root user | ❌ Not possible on DSM |
| **Unprivileged** | Container not run with `--privileged`; no added capabilities | ✅ **Yes — and further** |
| **Non-root** | Container *process* is not UID 0 | ✅ UID 1030 |

**On unprivileged:** `--privileged` is opt-in and you never enabled it, so this build was never privileged. With `cap_drop: ALL` you go beyond the default — you've dropped capabilities an ordinary unprivileged container retains (`CHOWN`, `SETUID`, `SETGID`, `NET_RAW`, and others). This is *more* restrictive than a stock unprivileged container.

**On rootless:** DSM's `dockerd` runs as root and Synology exposes no supported rootless-daemon or `userns-remap` path. The honest claim is **"non-root, unprivileged containers on a root daemon."** An app-level Forgejo or Postgres CVE is contained to UID 1030 / UID 70; a *daemon or runc* CVE would still yield root on the NAS.

### ⚠️ The seccomp caveat — state this honestly

DSM's daemon configuration (`/var/packages/ContainerManager/etc/dockerd.json`) sets:

```json
"seccomp-profile": "unconfined"
```

**Containers on DSM do not get Docker's default seccomp syscall filter.** That filter normally blocks ~44 dangerous syscalls; without it, the kernel attack surface is meaningfully wider than on stock Docker.

This is a Synology-wide decision, not something your Compose file caused, and it can't be fixed from Compose without supplying your own profile. It's a genuine, honest caveat that belongs in the write-up — and another item that resolves itself on the Proxmox migration.

**Net posture, stated fairly:**

| Control | Status |
|---|---|
| Non-root process | ✅ UID 1030 |
| Capabilities | ✅ all dropped |
| Privilege escalation | ✅ blocked |
| Root filesystem | ✅ read-only |
| Docker socket | ✅ not mounted |
| Network isolation | ✅ DB on internal-only bridge |
| Seccomp filter | ⚠️ **unconfined (DSM-wide)** |
| Daemon privilege | ⚠️ **root (DSM-wide)** |

The two warnings are both platform constraints, both out of your control here, and both close on the dedicated-server migration — see Appendix B, which is designed so that move is a data copy rather than a redesign.

---

## 4 · Key decisions

### Decision A · Postgres container UID

The official image documents the constraint precisely: the image supports running as a mostly arbitrary user via `--user`, and as of docker-library/postgres#1018 this is also true for the Alpine variants; the main caveat is that postgres doesn't care what UID it runs as as long as the owner of PGDATA matches, but `initdb` does care and needs the user to exist in `/etc/passwd`. Running `--user 1000:1000` produces `initdb: could not look up effective user ID 1000: user does not exist`.

| | **A1 · `user: "70:70"`** ← chosen | A2 · `user: "1030:65536"` + `/etc/passwd` mount |
|---|---|---|
| How | UID 70 = `postgres`, already in the Alpine image | Bind-mount a passwd file containing UID 1030 |
| Non-root | ✅ | ✅ |
| `cap_drop: ALL` | ✅ | ✅ |
| Extra moving parts | none | a passwd file to maintain across image updates |
| Breaks on image rebuild | no | possible if the image's passwd layout changes |
| Meets "1000+ IDs" | ❌ (internal to container) | ✅ |
| On-disk owner of `db/` | `70` | `1030` |

**Chosen: A1.** The 1000+ UID rule exists to avoid colliding with host system accounts and to keep a distinct identity — UID 70 inside a container that publishes no ports and sits on an `internal: true` network satisfies the spirit of it, with zero maintenance surface. If you'd rather have A2 for consistency in the blog narrative, it's a 4-line change and I'll write it out. Phase 1 includes a check that UID 70 is unused on your DSM host.

### Decision B · Bootstrap method

You asked: *why disable the setup page and make it harder — can't we set it up then disable it?* **You were right, and v1 over-engineered it.**

| | **B1 · Web installer, then lock** ← chosen | B2 · `INSTALL_LOCK=true` + CLI admin |
|---|---|---|
| Exposure window | ~5 min, LAN-only, behind firewall rule | zero |
| Real-world risk | very low | none |
| Learning value (first build) | high — you see every setting | low |
| Reproducible / IaC-friendly | ❌ manual clicks | ✅ fully declarative |
| Works with arbitrary UID | ✅ **once Phase 4B is done** | ✅ always |
| Steps | fewer | more |

**Chosen: B1**, enabled by Phase 4B.

There was an interim period in this build where B1 appeared impossible: the installer's "Run As Username" check couldn't be satisfied by any typed value, and the workaround was B2 plus stripping `RUN_USER`. **That turned out to be treating the symptom.** The real defect was that UID 1030 had no passwd entry, so `os/user.Current()` returned an empty string. Phase 4B fixes the cause, the installer works as designed, and both workarounds disappear.

Worth recording for the blog: the first fix suppressed a legitimate self-check; the second made the check pass by making the underlying claim true. **Prefer correcting configuration over disabling validation** — the check exists for a reason, and an instance that lies about its own identity produces confusing failures later (empty usernames in audit output, `whoami` returning nothing).

**Keep B2 for the Proxmox rebuild.** Under Terraform, declarative bootstrap is the right pattern — a nice arc for the series: *manual first to learn it, declarative second to own it.*

### Decision C · Where the DB volume lives

Locked per your instruction: **same tree**, `/volume1/docker/forgejo/db`. One backup root, one permissions model. Operational note in Phase 12 about excluding it from btrfs snapshots.

---

## 5 · Architecture

```
                    LAN 192.168.1.0/24
                            │
                     tcp/3443 (HTTPS)   ← DSM firewall: allow LAN, deny all
                            │
        ┌───────────────────▼────────────────────┐
        │  Synology DS1621+ · DSM 7.3.2          │
        │  Container Manager (dockerd as root)   │
        │                                        │
        │  ┌──────────────────────────────────┐  │
        │  │ net: forgejo_frontend (bridge)   │  │
        │  │   ┌────────────────────────────┐ │  │
        │  │   │ forgejo   uid 1030:65536   │ │  │
        │  │   │ :3000 TLS  read_only       │ │  │
        │  │   │ cap_drop ALL  no-new-privs │ │  │
        │  │   └────────────┬───────────────┘ │  │
        │  └────────────────┼─────────────────┘  │
        │  ┌────────────────▼─────────────────┐  │
        │  │ net: forgejo_backend             │  │
        │  │      internal: true  ← no route  │  │
        │  │   ┌────────────────────────────┐ │  │
        │  │   │ forgejo-db  uid 70:70      │ │  │
        │  │   │ :5432  NOT published       │ │  │
        │  │   └────────────────────────────┘ │  │
        │  └──────────────────────────────────┘  │
        └────────────────────────────────────────┘
```

---

# BUILD

Everything below is meant to be executed in order.

## Phase 1 · Pre-flight checks

### Step 1.1 — Enable SSH temporarily

1. **Control Panel** → **Terminal & SNMP** → **Terminal** tab
2. Tick **Enable SSH service**
3. Port: leave `22`
4. **Apply**

> You'll disable this again in Phase 13. Everything after Phase 8 can be done from the DSM GUI.

### Step 1.2 — Connect and elevate

```bash
ssh your_admin_user@192.168.1.201
sudo -i          # enter your admin password
```

### Step 1.3 — Re-confirm the service account

```bash
id sa_forgejo
```

Expected: `uid=1030(sa_forgejo) gid=100(users) groups=100(users),65536(docker_service_accounts)`

**If the UID or GID differs, stop.** Every `1030` and `65536` below must be replaced with what this command actually prints.

### Step 1.4 — Confirm UID 70 is free (for Postgres)

```bash
awk -F: '$3==70 {print "TAKEN: " $0}' /etc/passwd
awk -F: '$3==70 {print "TAKEN: " $0}' /etc/group
```

Empty output = good. If something is listed, tell me and we'll switch to Decision A2.

### Step 1.5 — Confirm port 3443 is free

```bash
netstat -tulpn | grep -E ':3443|:5432'
```

Empty output = good. If 3443 is taken, pick another (3444, 8443, 9443) and substitute it everywhere.

### Step 1.6 — Confirm the docker shared folder exists

```bash
ls -ld /volume1/docker
```

If it doesn't exist: **Control Panel** → **Shared Folder** → **Create** → name it `docker` → uncheck "Enable Recycle Bin" → Next through to Apply.

---

## Phase 2 · Create the directory structure

Still in the root SSH session:

```bash
mkdir -p /volume1/docker/forgejo/data
mkdir -p /volume1/docker/forgejo/data/custom/conf
mkdir -p /volume1/docker/forgejo/data/custom/https
mkdir -p /volume1/docker/forgejo/db

ls -la /volume1/docker/forgejo
```

Why these specific directories:

| Directory | Purpose |
|---|---|
| `data/` | The single Forgejo volume → `/var/lib/gitea` |
| `data/custom/conf/` | Where `app.ini` will be written |
| `data/custom/https/` | Where Forgejo reads `cert.pem` / `key.pem` **by default** |
| `db/` | PostgreSQL PGDATA |

---

## Phase 3 · Certificates — step-ca path (CURRENT)

> **Status: done.** Certificates were issued by step-ca on the workstation and placed at
> `/volume1/docker/forgejo/data/custom/https/` as **`server.crt`** and **`server.key`**.
> The Compose file in Phase 6 points at those filenames explicitly.
> The self-signed openssl walkthrough that follows is retained only as a fallback.

### 3A.1 — ⚠️ Check the certificate lifetime FIRST

**step-ca issues 24-hour certificates by default**, and its default `maxTLSCertDuration` is *also* 24h — so `--not-after` beyond a day is rejected unless the CA's claims were raised. Smallstep's own docs state that by default step-ca issues certificates valid for 24 hours, adjustable via `defaultTLSCertDuration` per provisioner or the `--not-after` flag.

If nothing was changed, **Forgejo will start fine today and serve an expired certificate tomorrow.** Check now:

```bash
step certificate inspect \
  /volume1/docker/forgejo/data/custom/https/server.crt --short
```

Or without the step CLI on the NAS:

```bash
openssl x509 -in /volume1/docker/forgejo/data/custom/https/server.crt \
  -noout -subject -issuer -dates -ext subjectAltName
```

Read the `Valid from … to …` / `notAfter` line.

| Result | What it means | Action |
|---|---|---|
| ~24 h window | CA claims are at defaults | Raise claims + reissue, **or** set up renewal — see 3A.3 |
| Weeks/months | You already raised `maxTLSCertDuration` | Note the expiry date and move on |

### 3A.2 — Verify SAN and chain order

```bash
openssl x509 -in .../server.crt -noout -ext subjectAltName
```

Must contain **`IP Address:192.168.1.201`** (and/or the hostname you'll actually type). Without an IP SAN, `https://192.168.1.201:3443` fails validation no matter what's in the trust store.

Then check chain order. The Forgejo config cheat sheet is explicit: *"When chaining, the server certificate must come first, then intermediate CA certificates (if any)."* step-ca uses a root→intermediate→leaf hierarchy, and `step ca certificate` normally bundles leaf + intermediate in that order — but verify:

```bash
grep -c "BEGIN CERTIFICATE" /volume1/docker/forgejo/data/custom/https/server.crt
```

| Count | Meaning |
|---|---|
| `2` | leaf + intermediate — correct, nothing to do |
| `1` | leaf only — clients that don't already have the intermediate will fail. Append it: `step ca provisioner` / `cat server.crt intermediate_ca.crt > server-bundle.crt` and point `CERT_FILE` at the bundle |
| `3` | leaf + intermediate + root — works, root is redundant but harmless |

Confirm the leaf is genuinely first:

```bash
openssl crl2pkcs7 -nocrl -certfile server.crt \
  | openssl pkcs7 -print_certs -noout | head
```

The first `subject=` must be your server, not the CA.

### 3A.3 — Renewal strategy

| Option | How | Fits your build? |
|---|---|---|
| **Long-lived cert (simplest now)** | Raise `maxTLSCertDuration` in `ca.json` claims (e.g. `"9480h"`), reissue with `--not-after=8760h`, replace the two files, restart the container | ✅ Good transitional choice. Keep the leaf under **398 days** for Apple clients |
| **`step ca renew --daemon` on the workstation + push** | Daemon renews at ~2/3 of lifetime, then `scp` to the NAS and restart Forgejo | Works, but couples the NAS to your workstation being up |
| **`step ca renew` in a DSM Task Scheduler job** | Install `step` on the NAS, renew in place, then `docker restart forgejo` | Cleanest end state. Requires the step CLI on DSM |
| **Forgejo built-in ACME against step-ca** | `ENABLE_ACME=true` + `ACME_URL=https://ca.lan/acme/acme/directory` | ❌ **Won't work here** — see below |

**Why Forgejo's ACME support is off the table in this build:** the cheat sheet notes `ENABLE_ACME` requires the CA to reach port 80 or 443 on this host. Our container runs as UID 1030 with `cap_drop: ALL`, so it cannot bind a port below 1024 — it has no `CAP_NET_BIND_SERVICE`. This is exactly the failure in Forgejo issue #6250 (*"Unable to bind port 80 for ACME HTTP-01 challenge"*). You'd have to hand back that capability, which trades a real hardening control for a convenience. **Renew externally and restart the container instead.**

Forgejo reads the certificate at startup, so any renewal needs a restart (or `SIGHUP` — `ALLOW_GRACEFUL_RESTARTS` defaults to true). In a Task Scheduler job:

```bash
docker restart forgejo
```

### 3A.4 — Fix ownership after any cert replacement

Every time you drop in a new `server.crt`/`server.key`, re-run:

```bash
chown 1030:65536 /volume1/docker/forgejo/data/custom/https/server.{crt,key}
chmod 600 /volume1/docker/forgejo/data/custom/https/server.key
chmod 644 /volume1/docker/forgejo/data/custom/https/server.crt
```

Files copied in as root will make Forgejo fail to read its own certificate.

### 3A.5 — Export the step-ca ROOT for client trust

Clients must trust the **root**, not the intermediate:

```bash
# On the workstation, from your step-ca environment:
step ca root vineyard-root-ca.crt
```

Use this file — not `intermediate_ca.crt` — in Phase 11.

---

## Phase 3 (fallback) · Self-signed CA with openssl

> Skip this entirely if you're on the step-ca path above. Retained for reproducibility.

**Where to run this:** ideally on your workstation (WSL, Linux, or macOS) so the CA private key never touches the NAS. Instructions below assume the NAS for convenience, with a step to move the key off at the end. Pick whichever you prefer — the commands are identical.

### Step 3.1 — Working directory

```bash
mkdir -p /root/ca && cd /root/ca
```

### Step 3.2 — Create the root CA

```bash
openssl genrsa -out vineyard-root-ca.key 4096

openssl req -x509 -new -nodes -sha256 -days 3650 \
  -key vineyard-root-ca.key \
  -out vineyard-root-ca.crt \
  -subj "/C=US/O=Vineyard/CN=Vineyard Root CA"
```

This CA is reusable — every future internal service (and eventually step-ca's own bootstrap) can chain to it.

### Step 3.3 — Server key and CSR

```bash
openssl genrsa -out forgejo.key 2048

openssl req -new -sha256 \
  -key forgejo.key \
  -out forgejo.csr \
  -subj "/C=US/O=Vineyard/CN=forgejo.lan"
```

### Step 3.4 — SAN extension file

Modern clients ignore CN entirely and require SANs. Edit the hostnames to match whatever you'll actually type in a browser and in `git clone`.

```bash
cat > forgejo.ext <<'EOF'
basicConstraints = CA:FALSE
keyUsage = digitalSignature, keyEncipherment
extendedKeyUsage = serverAuth
subjectAltName = @alt_names

[alt_names]
DNS.1 = forgejo.lan
DNS.2 = nas.lan
IP.1  = 192.168.1.201
EOF
```

> **The `IP.1` entry is what makes `https://192.168.1.201:3443` work without warnings** before you have internal DNS. When Technitium is live, add the real name and re-issue.

### Step 3.5 — Sign it

```bash
openssl x509 -req -sha256 -days 398 \
  -in forgejo.csr \
  -CA vineyard-root-ca.crt \
  -CAkey vineyard-root-ca.key \
  -CAcreateserial \
  -extfile forgejo.ext \
  -out forgejo.crt
```

**Why 398 days:** Apple platforms reject TLS server certificates with validity over 398 days. Even for a private CA, staying under that ceiling avoids a class of "works everywhere except my iPhone" bugs. Set a calendar reminder — or better, let step-ca automate renewal when you get there.

### Step 3.6 — Verify before deploying

```bash
openssl x509 -in forgejo.crt -noout -text | grep -A1 "Subject Alternative Name"
openssl verify -CAfile vineyard-root-ca.crt forgejo.crt
```

Expect to see your DNS names and IP listed, and `forgejo.crt: OK`.

### Step 3.7 — Install into Forgejo's default cert location

```bash
cp forgejo.crt /volume1/docker/forgejo/data/custom/https/cert.pem
cp forgejo.key /volume1/docker/forgejo/data/custom/https/key.pem
```

### Step 3.8 — Get the CA public cert somewhere you can reach it

```bash
cp vineyard-root-ca.crt /volume1/docker/vineyard-root-ca.crt
```

You'll install this on clients in Phase 11. It's a public certificate — safe to copy around.

### Step 3.9 — Move the CA private key off the NAS

```bash
# From your workstation:
scp your_admin_user@192.168.1.201:/root/ca/vineyard-root-ca.key .
scp your_admin_user@192.168.1.201:/root/ca/vineyard-root-ca.crt .
```

Then on the NAS:

```bash
shred -u /root/ca/vineyard-root-ca.key
```

Store the CA key in your password manager or OpenBao once it's up. **If this key leaks, anyone can mint certificates your machines will trust.**

---

## Phase 4 · Set ownership and permissions

This is the #1 cause of first-boot failures. The Forgejo docs are blunt about it: *"Note that the volume should be owned by the user/group with the UID/GID specified in the config file. If you don't set the volume correct permissions, the container may not start."*

```bash
# Forgejo data → 1030:65536
chown -R 1030:65536 /volume1/docker/forgejo/data

# Postgres data → 70:70 (the postgres user inside the alpine image)
chown -R 70:70 /volume1/docker/forgejo/db

# Private key readable only by the owner (step-ca filenames)
chmod 600 /volume1/docker/forgejo/data/custom/https/server.key
chmod 644 /volume1/docker/forgejo/data/custom/https/server.crt

# Directory modes
chmod 750 /volume1/docker/forgejo/data
chmod 700 /volume1/docker/forgejo/db
```

Verify:

```bash
ls -la /volume1/docker/forgejo/
ls -la /volume1/docker/forgejo/data/custom/https/
```

Expect `1030 65536` on `data`, `70 70` on `db`, and `-rw-------` on `key.pem`.

### ⚠️ The DSM ACL trap

Containers honour **POSIX** ownership. DSM layers its own **ACLs** on top, and those ACLs are invisible from inside a container. Critically:

> **Once you have run the `chown` above, do not open this folder's permissions in File Station or Control Panel → Shared Folder → Edit → Permissions.** Doing so can cause DSM to rewrite ACLs across the subtree and clobber your POSIX owner, which will break the container at the next restart with a permission-denied error.

If you ever need to change permissions here, do it over SSH with `chown`/`chmod`, not the GUI.

---

## Phase 4B · The `/etc/passwd` mount (REQUIRED — do this before first boot)

> **This is the single most important addition in v3.** Without it, the web installer cannot be completed at all when running as an arbitrary UID. Skipping this leads directly to the `'user to run as' username is not the current username: git -> ` dead end.

### Why it's needed

Two independent user databases exist (see §1). DSM knows UID 1030 as `sa_forgejo`; the container's `/etc/passwd` has no entry for 1030 at all — its `git` account is UID 1000.

Forgejo's installer validates that the `RUN_USER` you type matches the username it resolves at runtime. That resolution goes through Go's `os/user.Current()`, which performs a real `getpwuid()` lookup. **For an unmapped UID it returns an empty string**, so the comparison becomes `git` vs `""` and can never pass, no matter what you type.

This is a long-standing, cross-project issue — the Gitea tracker has an issue titled *"`CurrentUsername` is not always reliable"* (#1640), a TrueNAS user hit the byte-identical error with `apps -> ` (truenas/apps #3108), and Gitea #3592 documents the same empty-right-hand-side failure under OpenShift's random-UID model. The upstream advice in the TrueNAS thread is precisely this fix: edit the passwd entry so the `git` user carries the correct UID.

**The fix is to make the container's `/etc/passwd` tell the truth about UID 1030.**

### Step 4B.1 — Extract the image's real passwd and group files

Start from the image's own files so every other system account is preserved:

```bash
docker run --rm codeberg.org/forgejo/forgejo:15-rootless cat /etc/passwd \
  | sudo tee /volume1/docker/forgejo/passwd > /dev/null

docker run --rm codeberg.org/forgejo/forgejo:15-rootless cat /etc/group \
  | sudo tee /volume1/docker/forgejo/group > /dev/null
```

> Use `sudo tee`, not a plain `>` redirect. The redirect is performed by your shell (not by `sudo`), which can fail or silently produce an empty file when writing into a root-owned path.

### Step 4B.2 — Rewrite the `git` lines to your IDs

```bash
sudo sed -i '/^git:/c\git:x:1030:65536:Linux User,,,:/var/lib/gitea/git:/bin/bash' \
  /volume1/docker/forgejo/passwd

sudo sed -i '/^git:/c\git:x:65536:git' \
  /volume1/docker/forgejo/group
```

The `c\` form replaces the entire matched line, which is more robust than a substitution that assumes the original field values.

**The home directory field matters** — `/var/lib/gitea/git` is what the rootless image expects, and Git reads `$HOME` from here when locating its own configuration.

### Step 4B.3 — Verify content AND length

```bash
grep '^git:' /volume1/docker/forgejo/passwd
grep '^git:' /volume1/docker/forgejo/group
wc -l /volume1/docker/forgejo/passwd /volume1/docker/forgejo/group
```

| Expected | |
|---|---|
| passwd git line | `git:x:1030:65536:Linux User,,,:/var/lib/gitea/git:/bin/bash` |
| group git line | `git:x:65536:git` |
| Line counts | **~20 lines each** |

⚠️ **If either file is 1 line long, it was clobbered — regenerate from Step 4B.1.** A single-line `/etc/passwd` mounted over the image's real one hides `root`, `nobody` and every other system account. It appears to work at first and produces strange failures later.

### Step 4B.4 — Ownership and permissions

```bash
sudo chown root:root /volume1/docker/forgejo/passwd /volume1/docker/forgejo/group
sudo chmod 644 /volume1/docker/forgejo/passwd /volume1/docker/forgejo/group

ls -la /volume1/docker/forgejo/passwd /volume1/docker/forgejo/group
```

Expect `-rw-r--r-- 1 root root` on both.

| Setting | Value | Reason |
|---|---|---|
| Owner | `root:root` | The container runs as 1030 — root ownership means it has no write path even if `:ro` were bypassed. Also matches every real Linux host |
| Mode | **`644`** | **World-readable is required, not merely acceptable** |

> ⚠️ **Do not "harden" these to `600`.** Every UID→name lookup reads this file: `whoami`, `id`, `ls -l`, Git, and the `os/user.Current()` call this whole fix exists to satisfy. Mode `600` locks out UID 1030 and reproduces the original bug with a new error (`cat: can't open '/etc/passwd': Permission denied`).
>
> `/etc/passwd` is world-readable on every Linux system **by design** — it holds only name/UID/GID/home/shell mappings. The secret half was split into `/etc/shadow` (mode `640`, root-owned) decades ago precisely so passwd could stay open. You are not creating a shadow file; there is nothing confidential here.

DSM's root shell umask commonly yields `600` on newly created files, so **run the `chmod` after every regeneration**, not just the first.

### Step 4B.5 — Is this secure?

Yes — and it is *more* correct than the alternatives. The only meaningful attack on a mounted passwd file is injecting a UID 0 entry. Four independent controls block that:

| Control | Effect |
|---|---|
| `:ro` mount | Container cannot write the file |
| Host file owned by `root`, process is 1030 | No write access even without `:ro` |
| `no-new-privileges:true` | `setuid` binaries cannot escalate |
| **`cap_drop: ALL`** | **No `CAP_SETUID` — the kernel refuses any UID change regardless of what passwd says** |

The last is decisive: a passwd entry is only a *name-to-number mapping*. It grants nothing on its own.

Prove it after the container is up:

```bash
sudo docker exec forgejo grep ':0:' /etc/passwd   # only the pre-existing root line
sudo docker exec forgejo touch /etc/passwd        # → Read-only file system
sudo docker exec forgejo su root                  # → fails, no CAP_SETUID
```

**Why this beats the workarounds it replaces:** the previous approach relied on the image's `USER=git` environment variable — an assertion that didn't correspond to the running UID. Forgejo's fallback accepted it, but `whoami`, `id -un` and `ls -l` inside the container still returned empty or numeric output, meaning audit and forensic inspection would show a nameless UID. After this mount, UID 1030 *genuinely is* `git`, tooling behaves normally, and the `RUN_USER` strip (old Step 8.6) becomes unnecessary. **Correct configuration beats a suppressed check.**

### Step 4B.6 — Maintenance caveat

If a future Forgejo image adds or changes a system account, your pinned files override it. Low risk — these files are near-static across releases — but **re-derive them from the image on major version upgrades** rather than carrying them forward indefinitely. Steps 4B.1–4B.4 take under a minute.

Honest framing for the blog: this is a workaround for a gap between two systems — Forgejo requiring a passwd entry to resolve identity, and DSM not letting you choose UIDs that match the image's. It is not a feature. It is, however, the standard remedy for arbitrary-UID containers and exactly what OpenShift-style deployments do.

---

### Optional: give `sa_forgejo` shared-folder access without login rights

If you want `sa_forgejo` to be a *real* DSM identity rather than just a UID number:

1. **Control Panel** → **User & Group** → **User** → select `sa_forgejo` → **Edit**
2. **Permissions** tab → `docker` shared folder → **Read/Write**
3. **Applications** tab → **Deny** all (DSM, File Station, WebDAV, etc.)
4. **User Groups** tab → confirm `docker_service_accounts` is ticked
5. Apply

This gives you a coherent identity story for the blog without granting the account any way to log in.

---

## Phase 5 · Generate secrets and write `.env`

### Step 5.1 — Generate the Postgres password

```bash
openssl rand -base64 36 | tr -d '/+=' | head -c 40; echo
```

Copy the output. (The `tr` strips characters that cause quoting pain in INI files and connection strings.)

### Step 5.2 — Generate Forgejo's two secrets

The cheat sheet is emphatic about `SECRET_KEY`: this key is VERY IMPORTANT — if you lose it, data encrypted by it (like 2FA secrets) can no longer be decrypted.

```bash
docker run --rm codeberg.org/forgejo/forgejo:15-rootless forgejo generate secret SECRET_KEY
docker run --rm codeberg.org/forgejo/forgejo:15-rootless forgejo generate secret INTERNAL_TOKEN
```

Copy both outputs. **Put all three secrets in your password manager now**, before you continue.

### Step 5.3 — Write the `.env` file

```bash
cat > /volume1/docker/forgejo/.env <<'EOF'
POSTGRES_PASSWORD=PASTE_STEP_5.1_HERE
FORGEJO_SECRET_KEY=PASTE_SECRET_KEY_HERE
FORGEJO_INTERNAL_TOKEN=PASTE_INTERNAL_TOKEN_HERE
FORGEJO_ROOT_URL=https://192.168.1.201:3443/
FORGEJO_DOMAIN=192.168.1.201
EOF

chmod 600 /volume1/docker/forgejo/.env
chown 1030:65536 /volume1/docker/forgejo/.env
```

Now edit it and paste your real values:

```bash
vi /volume1/docker/forgejo/.env
```

> **Why a `.env` file rather than the `env_file:` Compose key:** Compose automatically reads `.env` from the project directory for `${VARIABLE}` substitution — no Compose key needed. This is one fewer key for Container Manager's schema validator to reject, and it keeps secrets out of the YAML you'll paste into a blog post.

> **Change `FORGEJO_ROOT_URL` and `FORGEJO_DOMAIN` to your hostname** (e.g. `https://forgejo.lan:3443/`) once you have DNS. `ROOT_URL` is what Forgejo puts in clone URLs, webhook callbacks, and emails — get it right or clone URLs will point somewhere wrong.

---

## Phase 6 · Write the Compose file

```bash
vi /volume1/docker/forgejo/docker-compose.yml
```

Paste the following in full:

```yaml
# Forgejo 15 LTS + PostgreSQL 17 — hardened non-root, LAN-only
# Synology DSM 7.3.2 / Container Manager 24.0.2-1606
# Project path: /volume1/docker/forgejo
#
# NOTE: no top-level `version:` key. Compose V2 deprecates it, and its
# presence makes Compose reject the v2-style `mem_limit` / `cpus` keys.

networks:
  frontend:
    driver: bridge
  backend:
    driver: bridge
    internal: true          # no route to the LAN or the internet

services:

  server:
    image: codeberg.org/forgejo/forgejo:15-rootless
    container_name: forgejo
    user: "1030:65536"                     # MUST equal `id sa_forgejo`
    restart: unless-stopped

    security_opt:
      - no-new-privileges:true
    cap_drop:
      - ALL
    read_only: true
    tmpfs:
      - /tmp:size=512m,mode=1777

    environment:
      # ---- database ----
      FORGEJO__database__DB_TYPE: postgres
      FORGEJO__database__HOST: db:5432
      FORGEJO__database__NAME: forgejo
      FORGEJO__database__USER: forgejo
      FORGEJO__database__PASSWD: ${POSTGRES_PASSWORD}
      FORGEJO__database__SSL_MODE: disable   # plaintext is fine on an internal-only bridge

      # ---- server / TLS ----
      # Paths are RELATIVE to CustomPath = /var/lib/gitea/custom.
      # So https/server.crt resolves to /var/lib/gitea/custom/https/server.crt,
      # which on the host is /volume1/docker/forgejo/data/custom/https/server.crt.
      # These are set explicitly because step-ca names its output server.crt/server.key
      # rather than Forgejo's cert.pem/key.pem defaults.
      FORGEJO__server__CERT_FILE: https/server.crt
      FORGEJO__server__KEY_FILE: https/server.key
      FORGEJO__server__PROTOCOL: https
      FORGEJO__server__HTTP_PORT: "3000"
      FORGEJO__server__DOMAIN: ${FORGEJO_DOMAIN}
      FORGEJO__server__ROOT_URL: ${FORGEJO_ROOT_URL}
      FORGEJO__server__SSL_MIN_VERSION: TLSv1.2
      FORGEJO__server__OFFLINE_MODE: "true"     # no CDN / Gravatar calls out to the internet

      # ---- SSH disabled entirely ----
      FORGEJO__server__DISABLE_SSH: "true"
      FORGEJO__server__START_SSH_SERVER: "false"

      # ---- LFS on now so the layout never has to change ----
      FORGEJO__server__LFS_START_SERVER: "true"

      # ---- secrets ----
      FORGEJO__security__SECRET_KEY: ${FORGEJO_SECRET_KEY}
      FORGEJO__security__INTERNAL_TOKEN: ${FORGEJO_INTERNAL_TOKEN}

      # ---- hardening ----
      FORGEJO__security__DISABLE_GIT_HOOKS: "true"
      FORGEJO__security__PASSWORD_HASH_ALGO: argon2
      FORGEJO__security__MIN_PASSWORD_LENGTH: "14"
      FORGEJO__security__PASSWORD_COMPLEXITY: lower,upper,digit,spec
      FORGEJO__security__DISABLE_QUERY_AUTH_TOKEN: "true"
      FORGEJO__service__DISABLE_REGISTRATION: "true"
      FORGEJO__service__REQUIRE_SIGNIN_VIEW: "true"
      FORGEJO__service__ENABLE_BASIC_AUTHENTICATION: "true"   # required for PAT-over-HTTPS
      FORGEJO__session__COOKIE_SECURE: "true"
      FORGEJO__session__SAME_SITE: lax
      FORGEJO__migrations__ALLOW_LOCALNETWORKS: "false"
      FORGEJO__webhook__ALLOWED_HOST_LIST: private

      # ---- future: Actions + registry ----
      FORGEJO__actions__ENABLED: "true"

      FORGEJO__log__LEVEL: Info

    volumes:
      - /volume1/docker/forgejo/data:/var/lib/gitea
      - /etc/localtime:/etc/localtime:ro
      # REQUIRED (Phase 4B): makes UID 1030 resolve to the name 'git' inside the
      # container. Without these, os/user.Current() returns "" and the web
      # installer's "Run As Username" check can never pass.
      - /volume1/docker/forgejo/passwd:/etc/passwd:ro
      - /volume1/docker/forgejo/group:/etc/group:ro

    ports:
      # Bound to the LAN address explicitly rather than "3443:3000".
      # The short form binds 0.0.0.0 AND :: — an IPv6 listener your DSM
      # firewall's IPv4 subnet rule does not cover. See Phase 10.
      - "192.168.1.201:3443:3000"

    networks:
      - frontend
      - backend

    mem_limit: 2g
    # NOTE: no `cpus:` — Synology kernels lack CFS bandwidth control.
    # Setting it produces: "NanoCPUs can not be set, as your kernel does not
    # support CPU CFS scheduler or the cgroup is not mounted" and FAILS THE BUILD.

    depends_on:
      db:
        condition: service_healthy

  db:
    image: postgres:17-alpine
    container_name: forgejo-db
    user: "70:70"                          # 'postgres' — exists in the image's /etc/passwd
    restart: unless-stopped

    security_opt:
      - no-new-privileges:true
    cap_drop:
      - ALL
    read_only: true
    tmpfs:
      - /tmp:size=128m,mode=1777
      - /run/postgresql:size=128m,mode=1777   # required: postgres creates its socket here

    environment:
      POSTGRES_USER: forgejo
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
      POSTGRES_DB: forgejo
      POSTGRES_INITDB_ARGS: "--auth-host=scram-sha-256 --auth-local=scram-sha-256"

    volumes:
      - /volume1/docker/forgejo/db:/var/lib/postgresql/data

    networks:
      - backend                            # backend ONLY — never frontend

    mem_limit: 1g
    # no `cpus:` — see note on the server service above

    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U forgejo -d forgejo"]
      interval: 10s
      timeout: 5s
      retries: 6
      start_period: 30s
```

### What each hardening key buys you

| Key | Effect | Blog-worthy note |
|---|---|---|
| `user: "1030:65536"` | Process runs as your service account, not root | The core of the whole exercise |
| `cap_drop: ALL` | No Linux capabilities at all | Works because we bind :3000, not a privileged port |
| `no-new-privileges:true` | `setuid` binaries can't escalate | Blocks a whole class of container escapes |
| `read_only: true` | Root filesystem immutable | Bind mounts and tmpfs stay writable |
| `tmpfs /run/postgresql` | Postgres socket dir | **Without this, Postgres fails** under `read_only` — documented in docker-library/postgres#264 |
| `internal: true` on backend | No route out of that bridge | Postgres is unreachable even if a port were accidentally published |
| No `/var/run/docker.sock` | — | The single most important thing you *don't* do |
| `OFFLINE_MODE: true` | No Gravatar/CDN egress | Also a privacy win |
| `ALLOW_LOCALNETWORKS: false` | Blocks SSRF via repo migration | Forgejo can't be tricked into scanning your LAN |
| `DISABLE_GIT_HOOKS: true` | No custom server-side hooks | Already the default; set explicitly so it's visible |
| `PASSWORD_HASH_ALGO: argon2` | Stronger than the `pbkdf2` default | Costs RAM — fine at 2 GB |

### Resource limits on Synology — memory yes, CPU no

Two separate constraints, and both bite:

**1. Use `mem_limit:`, not `deploy.resources`.** `docker compose up` ignores the `deploy:` block outside Swarm mode, so `deploy.resources.limits` silently does nothing. The v2-style service-level key is honoured.

**2. `cpus:` does not work on Synology at all.** ⚠️ **Confirmed on DSM 7.3.2 / Container Manager 24.0.2-1606.** Synology's kernel is built without CFS bandwidth control (`CONFIG_CFS_BANDWIDTH`), so any CPU quota — `cpus:`, `--cpus`, `cpu_quota`, or `deploy.resources.limits.cpus` — is rejected by the daemon:

```
Error response from daemon: NanoCPUs can not be set, as your kernel does not
support CPU CFS scheduler or the cgroup is not mounted
```

**This is a hard build failure, not a warning** — the project does not deploy. SynoForum's verdict: *"Nope, not going to work on a Syno. The kernel lacks required modules. This will work on every other Linux system like a charm."* Same error is reported across other NAS platforms with similarly stripped kernels.

**What you can still do for CPU:**

| Approach | Works on DSM? | Notes |
|---|---|---|
| `cpus: 2.0` | ❌ | Hard failure |
| `deploy.resources.limits.cpus` | ❌ | Same daemon rejection, and ignored by Compose anyway |
| `cpu_shares` (relative weight) | ✅ usually | Soft priority, not a cap — only matters under contention |
| Container Manager GUI CPU priority (Low/Medium/High) | ✅ | GUI-only; maps to cpu_shares |
| `mem_limit` | ✅ | Works normally — keep it |

**Practical impact here: near zero.** Forgejo idles at a few percent CPU on a LAN-only instance with one user. The memory limits are what actually protect the NAS, and those work. If you later want CPU containment as a hard guarantee, that's another argument for moving this to the Proxmox host, where the kernel supports it and you can also set cgroup limits at the VM/LXC boundary.

**For the blog:** worth calling out as a concrete example of a vendor-stripped kernel silently removing a control you'd assume was universal. It's the kind of thing that only surfaces at deploy time.

---

## Phase 7 · Create the project in Container Manager

1. Open **Container Manager** from the DSM main menu
2. Left sidebar → **Project**
3. Click **Create**
4. **Project Name:** `forgejo`
5. **Path:** click **Set Path** → browse to `docker` → `forgejo` → **Select**
6. Container Manager detects the existing `docker-compose.yml` and asks whether to use it → **Yes / Use existing**
7. The YAML appears in the editor. **Scroll through it** — if Container Manager rejects a key, it flags the line here before you build.
8. **Next**
9. **Web portal settings:** skip (leave everything unticked) → **Next**
10. Review the summary → **Done**

Container Manager now pulls both images and starts the stack. The build log streams in the window — leave it open.

**Expected first-run log highlights:**

- `db` pulls `postgres:17-alpine`, runs `initdb`, then `database system is ready to accept connections`
- healthcheck flips to healthy after ~15–30 s
- `server` pulls `forgejo:15-rootless` and logs `Listen: https://0.0.0.0:3000`

If either container enters a restart loop, jump to Troubleshooting — don't retry blindly.

---

## Phase 8 · First boot, installer, and admin account

### Step 8.1 — Open the installer

Browse to:

```
https://192.168.1.201:3443/
```

Your browser will warn about the certificate — **expected**, because you haven't installed the CA yet (Phase 11). Click through the warning for now.

### Step 8.2 — Verify the pre-filled database section

Because the `FORGEJO__database__*` env vars are set, the installer should already show:

| Field | Expected |
|---|---|
| Database Type | PostgreSQL |
| Host | `db:5432` |
| Username | `forgejo` |
| Password | (filled) |
| Database Name | `forgejo` |
| SSL | Disable |

**If any of these are blank or wrong, stop** — it means the env vars aren't reaching the container, and you'll get a broken `app.ini`. Check that `.env` is in the project directory and re-deploy.

### Step 8.3 — General settings

| Field | Value | Why |
|---|---|---|
| Site Title | your choice | — |
| Repository Root Path | **leave default — `/var/lib/gitea/git/repositories`** | ⚠️ v2 of this doc wrongly said `/var/lib/gitea/data/forgejo-repositories`. **Trust the installer's prefilled value.** See note below |
| Git LFS Root Path | **leave default** | `/var/lib/gitea/data/lfs` |
| Run As Username | **must match what the container reports** — see Step 8.3b | Do not blindly accept or blindly change it |
| Server Domain | `192.168.1.201` | must match your cert SAN |
| Forgejo HTTP Listen Port | `3000` | container-internal port, not 3443 |
| Forgejo Base URL | `https://192.168.1.201:3443/` | **the externally reachable URL** |
| Log Path | leave default | — |

> The Base URL is the field people get wrong most often. It must be what a *client* types, including the published port 3443 — not the internal 3000.

### ⚠️ Correction: Repository Root Path

**The installer's `/var/lib/gitea/git/repositories` is correct. Leave it.**

My earlier value came from the config cheat sheet, which documents the generic upstream default as `%(APP_DATA_PATH)s/forgejo-repositories`. **The rootless container image overrides this.** The image sets `HOME=/var/lib/gitea/git`, and Forgejo issue #4269 shows that after a successful rootless run the work directory contains exactly `gitea/git` and `gitea/custom` — repositories live under the `git/` tree, not `data/`.

General rule for this image: **where the cheat sheet and the installer's prefilled value disagree, the installer wins**, because it reflects the image's environment overrides rather than the bare-binary defaults.

Corrected on-disk layout:

| Content | Container path | Host path |
|---|---|---|
| Repositories | `/var/lib/gitea/git/repositories` | `…/data/git/repositories` |
| LFS objects | `/var/lib/gitea/data/lfs` | `…/data/data/lfs` |
| Packages / registry | `/var/lib/gitea/data/packages` | `…/data/data/packages` |
| Config | `/var/lib/gitea/custom/conf/app.ini` | `…/data/custom/conf/app.ini` |
| TLS | `/var/lib/gitea/custom/https/server.{crt,key}` | `…/data/custom/https/` |

Backups are unaffected — everything is still under the single `data/` bind mount.

### Step 8.3b — "Run As Username" — enter `git`

**With Phase 4B done, this field just works.** Enter the prefilled `git` and move on.

Confirm first if you like:

```bash
sudo docker exec forgejo id
# → uid=1030(git) gid=65536(git)
```

**The name in parentheses is the whole point.** A bare `uid=1030` with no name means the passwd mount isn't live — revisit Phase 4B, and note that adding a volume requires **Action → Build**, not Stop/Start.

`git` is now a *true* statement about UID 1030, so `RUN_USER = git` in `app.ini` stays valid across restarts forever. **The old "strip RUN_USER" step is obsolete and has been removed.**

#### Background: why this used to fail

The installer's help text says: *"The operating system username that Forgejo runs as. Note that this user must have access to the repository root path."*

Reasonable reading: *"that's `sa_forgejo`, UID 1030."* **That is wrong** — and understanding why is the core mental model of this entire build.

##### Two separate user databases

There are **two independent `/etc/passwd` files** in play, and they know nothing about each other:

| | Host (DSM) `/etc/passwd` | Container `/etc/passwd` |
|---|---|---|
| UID 1000 | *(some DSM account)* | `git` |
| UID 1030 | **`sa_forgejo`** | **no entry — unnamed** |

**Only the number crosses the boundary.** The Linux kernel tracks file ownership and process identity as *integers*. Usernames are a presentation layer that each namespace resolves through its own passwd file.

So when you `ls -la` on the NAS and see `sa_forgejo` owning the data directory, and the container looks at the exact same inode, the container sees owner `1030` and has no name for it. The name `sa_forgejo` **does not exist inside the container** and never will.

`RUN_USER` is read by the Forgejo process *inside* the container. Entering `sa_forgejo` makes Forgejo compare that string against the username it resolves internally — which will never be `sa_forgejo` — and it aborts:

```
Expect user 'sa_forgejo' but current user is: git
```

#### But the help text says it needs repo access — is that satisfied?

**Yes, and it already is** — via the number, not the name. Phase 4 set the data tree to `1030:65536`, and the container runs as `1030:65536`. The kernel compares integers, so access is granted. The *name* is irrelevant to permissions; it only matters for Forgejo's own identity self-check.

This is exactly why Phase 4's `chown` is the make-or-break step and why the GUI ACL warning matters. Numbers are the contract.

##### Why it failed, and never `sa_forgejo`

Forgejo resolves the current username via Go's `os/user.Current()`, a real `getpwuid()` lookup. Before Phase 4B, UID 1030 had no passwd entry, so that returned an **empty string** — the error's telltale signature is the empty right-hand side:

```
The "user to run as" username is not the current username: git -> 
```

No typed value could satisfy it, because everything compares unequal to `""`. Entering `sa_forgejo` fails for a second reason too — that name exists only in DSM's passwd file, never in the container's.

> **Blog note:** a clean illustration of container UID semantics — one integer, two names, only the integer real. It's also why the eventual rootless-Docker migration is fiddly: `subuid` mapping adds a *third* translation layer on top of these two.

### Step 8.3c — SSH port field

The installer shows an SSH port (`2222`) with **no "Disable SSH" checkbox**. That's expected — your env vars (`DISABLE_SSH: "true"`, `START_SSH_SERVER: "false"`) already handle it, and the installer renders the field regardless.

**Leave `2222` as-is.** Nothing binds it: the SSH server never starts, and the compose publishes only 3443. It's an inert value in `app.ini`.

### Step 8.3d — Email settings

**Leave disabled.** No mailer, LAN-only, single user.

Consequences to accept knowingly: no email verification, no notification emails, and **no password reset by email**. Store the admin password properly — recovery means a CLI command.

### Step 8.3e — Server and third-party service settings

| Setting | Value | Why |
|---|---|---|
| Disable Gravatar | ✅ **check** | `OFFLINE_MODE: true` already blocks it; checking keeps `app.ini` consistent with the compose |
| Enable Federated Avatars | ❌ **uncheck** | No outbound calls from a LAN-only box |
| **Enable Local Mode** | ✅ **check** (if shown) | Serves JS/CSS from the container instead of a CDN |
| Enable Open ID Sign-In | ❌ **uncheck** | Needs external IdP reachability; Authentik replaces this later |
| Enable Open ID Sign-Up | ❌ **uncheck** | Same |
| Disable Self-Registration | ✅ **check** | Matches `DISABLE_REGISTRATION: true` |
| Allow Only External Registration | ❌ leave | Irrelevant with registration off |
| Require Sign-In to View Pages | ✅ **check** | Matches `REQUIRE_SIGNIN_VIEW: true` |
| Enable Captcha | ❌ **uncheck** | Pointless with registration disabled; some providers phone home |
| Default Keep Email Private | ✅ **check** | Sensible default |
| Default Allow Creation of Organizations | ✅ leave checked | Useful for grouping homelab repos |
| Default Enable Timetracking | your call | Harmless either way |

**On Local Mode specifically:** a CDN-dependent UI on a LAN-only box produces missing styles and long hangs whenever outbound DNS or HTTP is blocked — and this environment is heading toward tighter egress control, not looser. It also means your browser never tells an external CDN which internal Git host you're loading. Cost is a few hundred KB served locally. If the checkbox isn't present in your version, `OFFLINE_MODE: true` already covers it.

**The pattern:** anything reaching the public internet gets turned off. This instance can't reach out and shouldn't try — a privacy win, a smaller attack surface, and no mystery latency when something upstream is unreachable. Same reasoning behind `ALLOW_LOCALNETWORKS: false`.

Several of these duplicate env vars you've already set. That's deliberate — env vars win at runtime, but having `app.ini` agree means you aren't reading two contradictory sources when debugging this in six months.

### Step 8.4 — Expand "Administrator Account Settings"

**Do this now.** If you skip it, the first user to register becomes admin — and with `DISABLE_REGISTRATION: true` that leaves you locked out.

| Field | Value |
|---|---|
| Administrator Username | e.g. `bora` (not `admin`) |
| Password | 14+ chars, mixed classes (your policy requires it) |
| Confirm Password | — |
| Email Address | any valid-format address; no mailer configured yet |

### Step 8.5 — Install

Click **Install Forgejo**. It writes `app.ini`, runs migrations, creates your admin, and redirects to the dashboard logged in.

### Step 8.6 — ~~Strip `RUN_USER`~~ — OBSOLETE in v3

> **No longer required.** This step existed because `git` was a *lie* about UID 1030 — the name came from the image's `USER` env var while the actual UID had no passwd entry, so a hard-coded `RUN_USER = git` would fail the identity check on the next restart.
>
> **Phase 4B makes `git` true.** UID 1030 now genuinely resolves to `git`, so `RUN_USER = git` in `app.ini` passes on every restart, indefinitely. Leave it in place.

Optional confirmation that the config is coherent:

```bash
grep -n "RUN_USER" /volume1/docker/forgejo/data/custom/conf/app.ini
# → RUN_USER = git    (correct — leave it)
sudo docker exec forgejo id
# → uid=1030(git) gid=65536(git)
```

If `app.ini` says `git` and `id` reports `(git)`, they agree and no action is needed. This is what "fix the configuration rather than suppress the check" looks like in practice.

### Step 8.7 — Confirm ownership survived the install

```bash
ls -la /volume1/docker/forgejo/data/custom/conf/app.ini
```

Should be `1030 65536`. If it isn't, re-run the Phase 4 `chown`.

---

## Phase 9 · Lock the installer and restart

### Step 9.1 — Add `INSTALL_LOCK`

Edit the compose file:

```bash
vi /volume1/docker/forgejo/docker-compose.yml
```

Add this line into the `server:` service's `environment:` block, alongside the other `FORGEJO__security__*` entries:

```yaml
      FORGEJO__security__INSTALL_LOCK: "true"
```

`INSTALL_LOCK` controls access to the installation page — when set to true, the installation page is not accessible.

### Step 9.2 — Rebuild

1. **Container Manager** → **Project** → select `forgejo`
2. Click **Action** → **Build** (this re-reads the YAML and recreates the containers)
3. Wait for both containers to return to running/healthy

### Step 9.3 — Verify the installer is gone

```bash
curl -k -o /dev/null -s -w "%{http_code}\n" https://192.168.1.201:3443/install
```

Should return `302` or `404` — **not** `200`. A `200` means the lock didn't apply.

---

## Phase 10 · DSM firewall

The firewall matters more than usual here because Forgejo is now the only thing standing between your LAN and a service that holds all your source code.

1. **Control Panel** → **Security** → **Firewall** tab
2. Tick **Enable firewall**
3. Click **Edit Rules** on your active profile
4. **Create** → configure:

| Field | Value |
|---|---|
| Ports | **Custom** |
| Protocol | TCP |
| Port | `3443` |
| Source IP | **Specific IP** → **Subnet** → IP `192.168.1.0`, Netmask `255.255.255.0` |
| Action | **Allow** |

5. **OK**
6. Make sure you also have **Allow** rules for the DSM ports you use (5000/5001) from your LAN, or you'll lock yourself out of the GUI
7. **Create** a final rule: all ports, all protocols, source **All**, action **Deny**
8. **Drag the Deny rule to the bottom** of the list

> **Rule order is everything.** DSM evaluates rules top-down and stops at the first match — a deny-all placed above your allow rules will lock you out of your own NAS. Verify the ordering visually before clicking OK.

9. **OK** → **Apply**

### ⚠️ IPv6 — the rule you'd otherwise miss

The DSM rule above covers `192.168.1.0/24` — **IPv4 only**. Docker's short port syntax (`"3443:3000"`) binds **both** stacks:

```
3000/tcp:[{0.0.0.0 3443} {:: 3443}]
          ^^^^^^^ IPv4      ^^ IPv6
```

If your LAN has IPv6, a client could reach 3443 over IPv6 and **bypass the IPv4 subnet rule entirely**. Check:

```bash
ip -6 addr show | grep "scope global"
```

| Result | Action |
|---|---|
| Global IPv6 addresses listed | Add a matching IPv6 firewall rule, **or** use the explicit bind below |
| No output | IPv6 isn't in play — the explicit bind is still good hygiene |

**Recommended fix (already in the Phase 6 compose):** bind the published port to the LAN address explicitly.

```yaml
    ports:
      - "192.168.1.201:3443:3000"
```

This pins the listener to one interface and drops the IPv6 binding — a tighter posture and one less thing to keep in sync between Docker and DSM's firewall. Verify:

```bash
sudo docker inspect forgejo --format '{{.NetworkSettings.Ports}}'
# → map[2222/tcp:[] 3000/tcp:[{192.168.1.201 3443}]]
```

### Reading that Ports output correctly

```
2222/tcp:[]                                    ← EXPOSED, not published
3000/tcp:[{192.168.1.201 3443}]                ← published to the host
```

**`2222/tcp:[]` — the empty brackets are the host-binding list. Empty means nothing is bound and the port is unreachable.** It appears only because the image declares `EXPOSE 2222` in its Dockerfile: metadata, not a listener.

`docker ps` shows exposed-but-unpublished ports in its PORTS column too, which is routinely misread as "this port is open." It isn't. `EXPOSE` documents what the image *would* listen on; only `ports:` in Compose (or `-p`) creates a host binding.

Here, 2222 is unreachable twice over: the SSH server never starts, and there's no host mapping regardless.

**Immediately test from another LAN machine** before closing the browser tab you're logged in with.

Linux/macOS:

```bash
curl -k -I https://192.168.1.201:3443/
nc -vz 192.168.1.201 2222     # expect: refused
```

Windows (no netcat needed — PowerShell has this built in):

```powershell
Test-NetConnection 192.168.1.201 -Port 3443   # TcpTestSucceeded : True
Test-NetConnection 192.168.1.201 -Port 2222   # TcpTestSucceeded : False

# Faster, near-instant alternative:
(New-Object Net.Sockets.TcpClient).ConnectAsync("192.168.1.201",3443).Wait(1000)

# Tests the actual service, not just the socket:
curl.exe -k -I https://192.168.1.201:3443/
```

> Use `curl.exe` explicitly — bare `curl` in PowerShell is an alias for `Invoke-WebRequest`, which takes different flags. And `Test-NetConnection` hangs ~20 s on a closed port before reporting `False`; that's the timeout, not a fault.

**Verified on this build:** 2222 → refused, 3443 → succeeded. ✅

Do **not** port-forward 3443 on your router. When you want remote access, that goes through your existing Tailscale or the future WireGuard site-to-site — not an inbound NAT rule.

---

## Phase 11 · Trust the CA on your clients

Until you do this, every client will throw certificate warnings and `git` will refuse to connect.

You need your CA's **root** certificate.

- **step-ca path:** run `step ca root vineyard-root-ca.crt` on the workstation (Phase 3A.5). **Distribute the root, not `intermediate_ca.crt`** — installing the intermediate as a trust anchor appears to work in some clients and fails in others, which is a miserable bug to chase.
- **openssl fallback path:** `vineyard-root-ca.crt` from Phase 3.8/3.9.

### Windows — use `certutil` (verified working on this build)

**Run PowerShell as Administrator**, then:

```powershell
certutil -addstore -f "Root" "C:\Users\Bora\vineyard-root-ca.crt"
```

Expect: `CertUtil: -addstore command completed successfully.`

> ✅ **This is the method that worked.** `Import-Certificate -CertStoreLocation Cert:\LocalMachine\Root` is the documented PowerShell equivalent but proved less reliable here — `certutil -addstore` gives clearer errors and succeeded directly. If you see `Access is denied`, the window is not elevated.

**Then fully quit Chrome** — every window, and check the system tray. Chrome caches certificate validation results and will keep showing the old error until restarted.

#### Inspecting the root before importing

```powershell
# File size sanity check — a step-ca ECDSA root is ~600-700 bytes. That is NORMAL.
Get-Item C:\Users\Bora\vineyard-root-ca.crt | Select-Object Length

# Read Subject and Issuer properly
$c = [Security.Cryptography.X509Certificates.X509Certificate2]::CreateFromCertFile("C:\Users\Bora\vineyard-root-ca.crt")
$c.Subject
$c.Issuer
```

**Subject and Issuer must be identical** — that is what makes it self-signed, i.e. a root.

> ⚠️ **`certutil -dump file.crt | Select-String "Subject:","Issuer:"` returns blank values.** This is a `Select-String` artifact, not a broken file: `certutil` prints the distinguished name on the *lines after* those labels. Use `-Context 0,2`, or the .NET method above. A 639-byte file with a `-----BEGIN CERTIFICATE-----` line is healthy.

#### Verify it landed

```powershell
Get-ChildItem Cert:\LocalMachine\Root | Where-Object { $_.Subject -eq $c.Subject }
```

> The filter matches the certificate's **Subject**, which was set at `step ca init` — **not the filename**. A root exported as `vineyard-root-ca.crt` may have Subject `Homelab Root CA` or similar. Searching for the wrong string produces a false negative.

**`LocalMachine\Root` rather than `CurrentUser\Root`** is deliberate — it covers every user and service on the box, including anything running under a service account.

#### If the file can't be found from PowerShell

If `step ca root` was run inside WSL, the file is in the WSL filesystem and PowerShell can't see it. Write directly to the Windows side instead:

```bash
step ca root /mnt/c/Users/Bora/Desktop/vineyard-root-ca.crt
# or bypass step's CA-context resolution entirely:
cp ~/.step/certs/root_ca.crt /mnt/c/Users/Bora/Desktop/vineyard-root-ca.crt
```

### Windows — `Import-Certificate` (alternative)

```powershell
Import-Certificate -FilePath "C:\Users\Bora\vineyard-root-ca.crt" `
  -CertStoreLocation Cert:\LocalMachine\Root
```

GUI equivalent: double-click the `.crt` → **Install Certificate** → **Local Machine** → **Place all certificates in the following store** → **Trusted Root Certification Authorities**.

### macOS

```bash
sudo security add-trusted-cert -d -r trustRoot \
  -k /Library/Keychains/System.keychain vineyard-root-ca.crt
```

### Linux (Debian/Ubuntu)

```bash
sudo cp vineyard-root-ca.crt /usr/local/share/ca-certificates/vineyard-root-ca.crt
sudo update-ca-certificates
```

### Linux (RHEL/Fedora)

```bash
sudo cp vineyard-root-ca.crt /etc/pki/ca-trust/source/anchors/
sudo update-ca-trust
```

### Git on Windows — the extra step

Git for Windows ships its **own** CA bundle and ignores the Windows certificate store by default. Either point Git at the Windows store:

```bash
git config --global http.sslBackend schannel
```

…or point it at your CA file explicitly:

```bash
git config --global http.sslCAInfo "C:/path/to/vineyard-root-ca.crt"
```

`schannel` is the cleaner option — it makes Git inherit whatever you trust at the OS level, so you never have to touch this again for future internal services.

### Verify trust actually works

```bash
curl https://192.168.1.201:3443/api/healthz     # note: NO -k flag
```

Success without `-k` means the chain validates. If it still fails, the SAN doesn't match what you typed — re-check Phase 3.4.

---

## Phase 12 · Create a PAT and prove Git works

### Step 12.1 — Generate a token

1. Log into `https://192.168.1.201:3443/`
2. Click your avatar (top right) → **Settings**
3. **Applications** tab
4. Under **Manage Access Tokens**: **Token Name** → e.g. `workstation`
5. **Select permissions** → expand and grant:
   - `repository` → **Read and Write**
   - `package` → **Read and Write** (for the future container registry)
   - leave everything else **No Access**
6. **Generate Token**
7. **Copy it now** — it is shown exactly once

> Forgejo 15 added repository-scoped tokens: when creating an access token it is now possible to restrict it to a specific list of repositories, and the token will not be able to perform operations on any resources outside that list, except for read-only access to public repositories. Use this for CI tokens later.

### Step 12.2 — Create a test repository

**+** (top right) → **New Repository** → name `test` → **Private** → tick **Initialize Repository** → **Create Repository**

### Step 12.3 — Store the credential

```bash
# Linux / macOS
git config --global credential.helper store

# Windows
git config --global credential.helper manager
```

`store` writes plaintext to `~/.git-credentials`. For your workstation that's a considered tradeoff; on anything shared, use `manager` (Windows), `osxkeychain` (macOS), or `libsecret` (Linux).

### Step 12.4 — Clone, commit, push

```bash
git clone https://192.168.1.201:3443/YOUR_USERNAME/test.git
cd test
echo "# Forgejo works" > README.md
git add README.md
git commit -m "First commit over HTTPS with a PAT"
git push
```

At the prompt: **Username** = your Forgejo username, **Password** = the **token** (not your account password).

A successful push is your end-to-end proof: TLS chain, PAT auth, Postgres write, and repo storage all working.

### What you lose by disabling SSH

| Feature | Status without SSH |
|---|---|
| Clone / fetch / push over HTTPS | ✅ |
| REST API | ✅ |
| Web UI | ✅ |
| Git LFS | ✅ |
| Package / container registry | ✅ |
| Forgejo Actions | ✅ |
| SSH clone URLs shown in UI | ❌ hidden |
| SSH key management page | ❌ |
| SSH-key commit signing verification | ❌ (GPG signing still works) |

Nothing on your roadmap needs SSH. If you later want it, the rootless image's SSH server listens on **2222** (not 22) — publish `2222:2222` and flip `DISABLE_SSH` to `false`.

---

## Phase 13 · Integrating Claude Code

This is where the private CA bites in a non-obvious way, so it's worth doing deliberately.

### The key insight: two separate trust stores are in play

| What makes the connection | Trust store it uses | How to fix |
|---|---|---|
| `git clone` / `git push` (a subprocess) | Git's own CA config — OpenSSL bundle or schannel | Phase 11 |
| Claude Code's own HTTPS calls (WebFetch, MCP) | Node/native binary trust chain | `NODE_EXTRA_CA_CERTS` or OS store |

**For ordinary use — Claude Code reading, editing, committing, and pushing your repo — Phase 11 is sufficient.** Claude Code shells out to `git`, and `git` uses the trust you configured there. Nothing else is required.

### If Claude Code needs to reach Forgejo's API or web UI directly

Recent versions changed the default: by default Claude Code trusts both its bundled Mozilla CA certificates and your operating system's certificate store, and system CA store integration requires the native binary distribution — when running on the Node.js runtime the system CA store is not merged automatically, in which case set `NODE_EXTRA_CA_CERTS=/path/to/ca-cert.pem`.

So:

- **Native binary install** → Phase 11's OS-level trust is picked up automatically. Nothing to do.
- **npm/Node install** → set the variable:

```bash
# Linux / macOS — add to ~/.bashrc or ~/.zshrc
export NODE_EXTRA_CA_CERTS=/etc/ssl/certs/vineyard-root-ca.crt

# Windows PowerShell profile
$env:NODE_EXTRA_CA_CERTS = "C:\certs\vineyard-root-ca.crt"
```

> ⚠️ **Set it in the shell, not in `~/.claude/settings.json`.** There are open bug reports (anthropics/claude-code #22512, #26897) that `NODE_EXTRA_CA_CERTS` placed in `settings.json` is not applied, while the same value exported in the shell works. Save yourself the debugging session.

Verify with `/status` inside a Claude Code session — there's an **Additional CA cert(s)** row. Note the docs caveat that this row shows the path without confirming the file loaded, so check the debug log if something still fails.

### Practical setup

```bash
git clone https://192.168.1.201:3443/YOUR_USERNAME/yourproject.git
cd yourproject
claude
```

Claude Code picks up the stored credential helper, so it can commit and push without any Forgejo-specific configuration.

### Worth knowing

- **GitHub-specific features won't work** — `gh` CLI integration, GitHub PR/issue commands, and the GitHub Actions integration are all GitHub-API-specific. Forgejo's API is broadly Gitea/GitHub-shaped but is not a drop-in for those tools.
- **Consider a dedicated PAT for Claude Code**, scoped to the repositories it should touch (Forgejo 15's repository-scoped tokens make this practical). If you ever need to revoke it, you revoke one token rather than rotating your primary credential.
- **`CLAUDE.md` in the repo root** is the right place to note that this repo lives on internal Forgejo, so remote-related suggestions stay accurate.

---

## Verification checklist

Run all of these. Each maps to a specific claim you'll make in the blog post.

```bash
# ---- 1. Containers run as non-root, with the right IDs AND NAMES ----
sudo docker exec forgejo id
#   → uid=1030(git) gid=65536(git)     ← names in parens = passwd mount working
sudo docker exec forgejo whoami
#   → git
sudo docker exec forgejo-db id
#   → uid=70(postgres) gid=70(postgres)

# ---- 1b. passwd mount is read-only and holds no extra root ----
sudo docker exec forgejo grep ':0:' /etc/passwd    # only the stock root line
sudo docker exec forgejo touch /etc/passwd         # → Read-only file system
sudo docker exec forgejo wc -l /etc/passwd         # ~20 lines, NOT 1

# ---- 2. Privilege escalation blocked ----
sudo docker exec forgejo grep NoNewPrivs /proc/1/status
#   → NoNewPrivs:  1
sudo docker inspect --format '{{.HostConfig.CapDrop}}' forgejo
#   → [ALL]
sudo docker inspect --format '{{.HostConfig.ReadonlyRootfs}}' forgejo
#   → true
sudo docker inspect --format '{{.HostConfig.SecurityOpt}}' forgejo
#   → [no-new-privileges:true]

# ---- 3. Read-only rootfs is genuinely enforced ----
sudo docker exec forgejo touch /usr/local/bin/proof 2>&1
#   → Read-only file system
sudo docker exec forgejo touch /tmp/proof && echo "tmpfs writable OK"

# ---- 4. No docker socket anywhere ----
sudo docker inspect --format '{{range .Mounts}}{{.Source}}{{"\n"}}{{end}}' forgejo | grep -c docker.sock
#   → 0

# ---- 5. Database is unreachable ----
sudo docker inspect --format '{{.NetworkSettings.Ports}}' forgejo-db
#   → map[]   (no published ports)
sudo docker network inspect forgejo_backend --format '{{.Internal}}'
#   → true
#   Then, FROM A DIFFERENT LAN MACHINE:
nc -vz 192.168.1.201 5432
#   → refused / timeout

# ---- 6. Forgejo CAN reach the DB (proving the internal net works) ----
sudo docker exec forgejo-db pg_isready -U forgejo -d forgejo
#   → accepting connections

# ---- 7. TLS ----
openssl s_client -connect 192.168.1.201:3443 -servername 192.168.1.201 </dev/null 2>/dev/null \
  | openssl x509 -noout -subject -dates -ext subjectAltName

# ---- 8. Installer sealed ----
curl -k -o /dev/null -s -w "%{http_code}\n" https://192.168.1.201:3443/install
#   → NOT 200

# ---- 9. Anonymous access blocked (REQUIRE_SIGNIN_VIEW) ----
curl -k -o /dev/null -s -w "%{http_code}\n" https://192.168.1.201:3443/explore
#   → 303 redirect to login

# ---- 10. Registration disabled ----
curl -k -o /dev/null -s -w "%{http_code}\n" https://192.168.1.201:3443/user/sign_up
#   → NOT 200

# ---- 11. SSH truly absent ----
sudo docker inspect --format '{{.NetworkSettings.Ports}}' forgejo
#   → map[2222/tcp:[] 3000/tcp:[{192.168.1.201 3443}]]
#     2222/tcp:[]  = EXPOSED but NOT published — empty [] means no host binding
#     Only a non-empty binding list is reachable.
nc -vz 192.168.1.201 2222        # → refused
```

Windows equivalents (no netcat required):

```powershell
Test-NetConnection 192.168.1.201 -Port 3443   # TcpTestSucceeded : True
Test-NetConnection 192.168.1.201 -Port 2222   # TcpTestSucceeded : False
curl.exe -k -I https://192.168.1.201:3443/
```

```bash

# ---- 12. Data ownership on disk ----
ls -la /volume1/docker/forgejo/
#   → data = 1030 65536 ; db = 70 70
```

### Then disable SSH on the NAS

**Control Panel** → **Terminal & SNMP** → untick **Enable SSH service** → **Apply**.

Re-enable it only when you need it. Everything routine (rebuild, logs, restart) is available in Container Manager's GUI.

---

## Troubleshooting

### `mkdir: can't create directory '/var/lib/gitea/...': Permission denied`

**Cause:** host directory ownership doesn't match `user:`. This is the failure mode documented in Forgejo issue #10215 and seen constantly in rootless deployments.

```bash
sudo docker inspect --format '{{.Config.User}}' forgejo   # what the container claims
ls -lan /volume1/docker/forgejo/data                      # what the disk says
sudo chown -R 1030:65536 /volume1/docker/forgejo/data
```

Then rebuild the project.

### `The "user to run as" username is not the current username: git -> ` (empty right side)

**Cause:** UID 1030 has no `/etc/passwd` entry, so `os/user.Current()` returns `""`. No typed value can match. **You skipped Phase 4B.**

**Fix:** do Phase 4B, then **Action → Build** (a volume addition needs a rebuild, not Stop/Start). Verify with `docker exec forgejo id` — you need `uid=1030(git)` *with the name*.

### `cat: can't open '/etc/passwd': Permission denied`

**Cause:** the mounted passwd/group files aren't world-readable. DSM's root shell umask commonly creates them `600`.

```bash
sudo chown root:root /volume1/docker/forgejo/passwd /volume1/docker/forgejo/group
sudo chmod 644 /volume1/docker/forgejo/passwd /volume1/docker/forgejo/group
```

No rebuild needed — permission changes on a bind-mounted file take effect immediately. **`644` is correct, not sloppy**; see Phase 4B.4.

### `MustInstalled() [F] Unable to load config file for a installed Forgejo instance`

**Cause:** you ran a `forgejo` CLI subcommand via `docker exec`, which **bypasses the image entrypoint** and therefore never gets `GITEA_APP_INI` set. The binary looks for `app.ini` at its compiled-in default rather than where the entrypoint writes it. The error text says exactly this: *"you should either use `--config` to set your config file."*

**Fix — pass `-c` explicitly:**

```bash
sudo docker exec -u 1030 forgejo forgejo \
  -c /var/lib/gitea/custom/conf/app.ini \
  admin user create --admin --username bora --email admin@lan \
  --random-password --must-change-password
```

**This is not data corruption.** Do not wipe volumes or drop the database over it.

### `Expect user 'git' but current user is: ...` (non-empty right side)

**Cause:** `RUN_USER` in `app.ini` disagrees with the resolved username. With Phase 4B in place this shouldn't occur; if it does, the passwd mount isn't live.

```bash
sudo docker exec forgejo id                    # expect uid=1030(git)
grep RUN_USER /volume1/docker/forgejo/data/custom/conf/app.ini
```

Make them agree — prefer fixing the passwd mount over deleting the line.

### Container crash-loops immediately under `read_only: true`

Find out what it's trying to write:

```bash
sudo docker diff forgejo
```

Any `A` (added) or `C` (changed) path outside your volumes and tmpfs is the culprit — add it as a tmpfs entry. As a last resort, comment out `read_only: true` for that one service and document the exception honestly in the blog rather than quietly dropping it.

### `initdb: could not look up effective user ID ...: user does not exist`

You changed the Postgres `user:` to something not in the image's `/etc/passwd`. Revert to `user: "70:70"` (Decision A1) or implement A2's passwd mount.

### `could not create lock file "/var/run/postgresql/..."`

The `/run/postgresql` tmpfs is missing or has the wrong mode. It must be present with `mode=1777` — this exact failure is documented in docker-library/postgres#264.

### `password authentication failed for user "forgejo"`

The password in `.env` doesn't match what `initdb` baked into the cluster. Postgres only reads `POSTGRES_PASSWORD` **on first initialization of an empty data directory** — changing `.env` afterwards has no effect. Either set the password inside the DB:

```bash
sudo docker exec -it forgejo-db psql -U forgejo -c "ALTER USER forgejo WITH PASSWORD 'new';"
```

…or wipe `/volume1/docker/forgejo/db` and start over (only safe before you have real data).

### Container Manager rejects the YAML

| Error | Fix |
|---|---|
| `NanoCPUs can not be set, as your kernel does not support CPU CFS scheduler or the cgroup is not mounted` | **Remove every `cpus:` line.** Synology kernels lack CFS bandwidth control. This is a hard build failure — the project will not deploy. Keep `mem_limit` |
| `Additional property X is not allowed` | Typo in a key name — check spelling against the compose above |
| `Unsupported config option for services: 'mem_limit'` | A `version:` key crept in — remove it |
| Long-form `depends_on` rejected | Fall back to `depends_on: [db]` and accept slower first-boot ordering (the healthcheck still protects steady state) |
| `internal` network rejected | Remove `internal: true`, and instead verify no ports are published on `db` — weaker, but functional. Report it; this would be a notable Container Manager limitation |

### `Connection refused` on port 3443

**Diagnose before changing anything** — refused and timeout mean different things, and treating one as the other sends you down the wrong path:

| Symptom | Meaning | Cause |
|---|---|---|
| **Connection refused** (immediate) | TCP RST — **nothing is listening** | Container not running, build failed, or wrong port |
| **Connection timed out** (hangs, then fails) | Packets silently dropped | Firewall blocking |

DSM's firewall **drops** blocked traffic rather than rejecting it, so a firewall problem presents as a *timeout*. **Refused means the container isn't up.**

Check in this order:

```bash
# 1. Is anything running?
sudo docker ps -a --filter name=forgejo

# 2. Is the port actually bound on the host?
sudo netstat -tulpn | grep 3443

# 3. Why did it stop?
sudo docker logs forgejo --tail 50
sudo docker logs forgejo-db --tail 50
```

If `docker ps -a` shows nothing at all, the **project build failed** and no containers were ever created — go back to the Container Manager build log and read the first error, not the last.

### Firewall check (only if you get a *timeout*)

```bash
# Is the DSM firewall even on?
sudo synofirewall --get-status
```

If enabled, confirm your Phase 10 allow rule exists and sits **above** the deny-all.

### `ERR_CERT_AUTHORITY_INVALID` / `schannel: SEC_E_UNTRUSTED_ROOT (0x80090325)`

**Both mean the same thing: the certificate was served correctly, but the issuing CA isn't in the client's trust store.** This is *expected* before Phase 11 and is actually a good sign — it proves TLS is working end to end.

Distinguish it from its neighbour:

| Error | Meaning |
|---|---|
| `ERR_CERT_AUTHORITY_INVALID` / `SEC_E_UNTRUSTED_ROOT` | Cert fine, **issuer not trusted** → install the root |
| `ERR_CERT_COMMON_NAME_INVALID` | Issuer trusted, **wrong hostname/IP in SAN** → reissue |

Check in this order:

1. **Chain completeness on the server:**
   ```bash
   grep -c "BEGIN CERTIFICATE" /volume1/docker/forgejo/data/custom/https/server.crt
   ```
   `2` = leaf + intermediate (correct). `1` = leaf only — clients can't build a path even with the root installed; append the intermediate and restart the container.

2. **Root installed on the client** — see Phase 11. On Windows, `certutil -addstore -f "Root" <path>` in an **elevated** shell.

3. **Chrome fully restarted** — not just the tab.

4. **Right CA?** If step-ca has multiple contexts, `step ca root` may export a different CA's root than the one that signed `server.crt`. The root's **Subject** must equal the server cert's **Issuer**.

### Browser shows a padlock but still says "Not secure" / "Connection is not secure"

Once the root is trusted and the cert reports as valid, Chrome should show a normal padlock. If it still flags the connection, work through these:

| Cause | Check | Fix |
|---|---|---|
| **Chrome not fully restarted** | Most common by far | Quit every window *and* the tray icon, reopen |
| **Stale tab** | Reload with `Ctrl+Shift+R` | Hard-refresh bypasses the cached security state |
| **Cert validity > 398 days** | `$c.NotAfter - $c.NotBefore` | Chrome rejects leaf certs over 398 days. Reissue shorter |
| **Mixed content** | F12 → Console, look for `Mixed Content:` warnings | Usually a wrong `ROOT_URL` — it must be `https://…:3443/`, not `http://` |
| **Wrong `ROOT_URL`** | `grep ROOT_URL …/custom/conf/app.ini` | Must match exactly what you type, including scheme and port |
| **Looking at the wrong indicator** | Click the padlock | "Connection is secure" + "Certificate is valid" = correct |

**Private CAs do not cause a permanent "Not secure" label.** Once the root is in `LocalMachine\Root` and Chrome has restarted, a privately-issued certificate gets the same padlock as a public one. If the warning persists after a full restart, it's mixed content or a `ROOT_URL` mismatch — not the private CA.

Quickest discriminator:

```powershell
curl.exe -I https://192.168.1.201:3443/
```

No `-k`. If curl returns headers cleanly, the chain validates and the trust problem is solved — anything Chrome still shows is a page-content or caching issue, not TLS.

### Firefox still shows a certificate error after the OS import

**Firefox maintains its own certificate store and ignores the OS entirely.** Import via Settings → Privacy & Security → Certificates → View Certificates → Authorities → Import.

### Browser: `NET::ERR_CERT_COMMON_NAME_INVALID`

The address you typed isn't in the cert's SAN list. Reissue with the correct name or IP included.

---

## Backups

Two things must be captured; either alone is useless.

### 1. Logical database dump

**Control Panel** → **Task Scheduler** → **Create** → **Scheduled Task** → **User-defined script**

- **General:** Task name `forgejo-db-dump`, User `root`
- **Schedule:** Daily, e.g. 02:00
- **Task Settings** → Run command:

```bash
#!/bin/bash
set -euo pipefail
OUT=/volume1/docker/forgejo/backup
mkdir -p "$OUT"
/usr/local/bin/docker exec forgejo-db \
  pg_dump -U forgejo -Fc forgejo > "$OUT/forgejo-$(date +%F).dump"
find "$OUT" -name 'forgejo-*.dump' -mtime +14 -delete
```

> Verify the docker binary path first with `which docker` — it varies across DSM versions.

### 2. The data tree

Back up `/volume1/docker/forgejo/data` — repositories, LFS objects, packages/registry blobs, and `app.ini` (which contains `SECRET_KEY` and `INTERNAL_TOKEN`).

### Alternative: Forgejo's own dump

```bash
sudo docker exec -u 1030 forgejo forgejo dump -c /var/lib/gitea/custom/conf/app.ini
```

Produces one consistent archive including the DB. Slower and disk-hungry, but simpler to restore.

### Fitting your existing targets

| Target | What to point it at |
|---|---|
| Restic | `/volume1/docker/forgejo/{data,backup}` |
| Proxmox Backup Server | same paths, via the future backup NAS |
| **Exclude** | `/volume1/docker/forgejo/db` — restore from the logical dump instead; a btrfs snapshot of a live PGDATA is crash-consistent at best |

**Keep `.env` in your password manager.** If you lose `SECRET_KEY`, every stored 2FA secret is permanently undecryptable.

### btrfs note

Postgres on btrfs suffers copy-on-write fragmentation. If you see DB latency later:

```bash
sudo chattr +C /volume1/docker/forgejo/db   # takes effect on NEW files only
```

Realistically irrelevant at your scale — noted for completeness.

---

## Upgrades

### Forgejo patch (15.0.5 → 15.0.6 → …)

The `15` tag tracks patches automatically.

1. Take a backup
2. **Container Manager** → **Registry** → search `forgejo` → download `15-rootless` again (pulls the newer digest)
3. **Project** → `forgejo` → **Action** → **Build**

### Forgejo major (15 → 16, eventually 17)

**Never automatic.** Per the docs, upgrading from X to X+1 requires manual operation and human verification.

1. Full backup (DB dump **and** data tree)
2. Read the release notes for **every** intervening major — v15→16→17 means reading two sets of breaking changes
3. Change the tag in `docker-compose.yml`
4. Build, then watch the migration log to completion before declaring victory

Since 15 is supported to **15 July 2027**, plan this for early 2027 rather than chasing it.

### PostgreSQL patch (17.10 → 17.11)

Safe: as with other minor releases, users are not required to dump and reload their database or use pg_upgrade in order to apply the update — you may simply stop PostgreSQL and update its binaries. Re-pull `postgres:17-alpine` and rebuild.

### PostgreSQL major (17 → 18) — the trap

**You cannot just change the tag.** The on-disk cluster is version-specific, *and* PG18 relocated `PGDATA` and changed the declared `VOLUME` path, so an existing mount won't even line up.

```bash
# 1. Dump from the running 17 container
sudo docker exec forgejo-db pg_dump -U forgejo -Fc forgejo > /volume1/docker/forgejo/pg17.dump

# 2. Stop the project in Container Manager

# 3. Move the old cluster aside; create a fresh empty dir
sudo mv /volume1/docker/forgejo/db /volume1/docker/forgejo/db-pg17-old
sudo mkdir -p /volume1/docker/forgejo/db
sudo chown 70:70 /volume1/docker/forgejo/db

# 4. Change the tag to postgres:18-alpine and CHECK the volume target
#    against the current Docker Hub docs before building.

# 5. Build. Let initdb create a fresh cluster. Then restore:
sudo docker exec -i forgejo-db pg_restore -U forgejo -d forgejo < /volume1/docker/forgejo/pg17.dump

# 6. Verify thoroughly, then delete db-pg17-old
```

**Do not switch base flavours** (`alpine` ↔ Debian) across an existing data directory — musl vs glibc locale/collation differences can corrupt index ordering. Dump and restore if you ever need to.

There's no urgency: PG17 is supported into 2029.

---

## Appendix A · Adding Actions runners and the registry later

The design already accommodates both — nothing here requires rearchitecting.

**Container registry / packages:** already enabled (`[packages] ENABLED` defaults to true). Push images to `192.168.1.201:3443/username/imagename`. Storage lands under `data/data/packages`. **This is your fastest-growing directory** — monitor free space, and when it becomes a problem, move `[storage]` to MinIO/S3 rather than resizing.

**Actions runners:** already enabled via `FORGEJO__actions__ENABLED`.

1. Get a runner token: **Site Administration** → **Actions** → **Runners** → **Create new Runner**
2. Add a `runner` service to the same Compose project, on the **`frontend`** network
3. Point it at `https://192.168.1.201:3443`
4. Give it the CA cert so it can validate TLS — mount `vineyard-root-ca.crt` into the runner container

> **Critical:** a runner needs Docker access to execute container jobs. **Do not mount the host's `/var/run/docker.sock`** — that hands any workflow root on your NAS and would undo everything in this document. Use a **Docker-in-Docker sidecar** so the runner's privilege requirement never touches the Forgejo or Postgres containers.

Honestly: on a DS1621+ (Ryzen V1500B) sharing duty with your primary storage, CI is a poor fit. The right home for runners is the DIY Proxmox server from Deep Dive 2. Plan for Forgejo-on-NAS as the *forge*, and runners elsewhere.

---

## Appendix B · Migrating to true rootless Docker later

When this moves to a dedicated server, you close the last gap — the root daemon.

| | Now (Synology) | Later (dedicated) |
|---|---|---|
| Daemon | root | **rootless** (`dockerd-rootless.sh`) or Podman |
| Container UID | 1030 | 1030 (unchanged) |
| Daemon-CVE blast radius | **root on NAS** | unprivileged user |
| Compose file | as written | ~unchanged |

**Why the migration is easy:** the design is already portable. One data volume, one DB volume, no Docker socket, no host networking, no privileged flags, no DSM-specific Compose keys.

Migration outline:

1. `pg_dump` + `tar` the data tree
2. On the target, `useradd` a user whose subuid range maps 1030 correctly
3. Restore both, `chown` to match the remapped IDs
4. Bring the same Compose file up under rootless Docker
5. Re-issue the cert with the new IP/hostname; swap the two PEM files
6. Update `ROOT_URL` and re-point clients

**The one genuinely fiddly part** is the `subuid`/`subgid` mapping — under rootless Docker, in-container UID 1030 maps to some high host UID, so the host-side `chown` uses different numbers than it does today. Budget an afternoon and test with throwaway data first.

---

## Appendix B2 · Moving step-ca to a dedicated box — and fixing the CA name then

**Deferred by decision (Bora, 2026-08-06).** step-ca currently runs on the **workstation**,
which makes the whole LAN's trust anchor dependent on one desktop being up and backed up.
It moves to a dedicated host (the Proxmox server from Deep Dive 2) as part of the same
migration as Appendix B.

**The naming inconsistency is corrected at that migration, not before.** Today there are
three names that don't quite agree:

| | Value |
|---|---|
| CA as Bora named it | `vineyard` |
| Root cert **Subject** | set at `step ca init` — verify with the .NET snippet in Phase 11 |
| Filename | **`vineyard-root-ca.crt`** (normalized 2026-08-06; was `vineyards-…`) |

> ⚠️ **Do not re-initialise the CA just to fix a name.** The filename is free to change — it
> is only a container for the bytes. The **Subject** is baked into the root certificate and
> stamped as the `Issuer` on every certificate it has ever signed. Changing it means a new
> root key, every issued cert invalidated, and a re-trust on every client and container.
> There is **zero functional gain** for a cosmetic fix.

**Why the migration is the right moment:** moving step-ca to a new host re-initialises it
anyway — new root, fresh trust distribution to every client. That is the one point where
correcting the Subject costs nothing incremental. Do it in a single pass then:

1. `step ca init` on the dedicated host with the intended name (`Vineyard Root CA`)
2. Re-issue the Forgejo leaf from the new CA; swap `server.crt` / `server.key`; restart
3. Distribute the new root to every client (Phase 11) and to the NAS
   (`/volume1/docker/certs/vineyard-root-ca.crt` — see `PROD_ROLLOUT.md` step 1a)
4. Retire the old root from the trust stores **last**, once everything validates

Until then the mismatch is cosmetic and harmless. Record it here so it is not "discovered"
again and fixed impulsively at the wrong time.

---

## Appendix C · What actually uses this forge — the SAVES repo

This document lives *inside* the first repository hosted on this instance, so the two are
coupled in a few concrete ways worth recording.

**Remote.** `origin` for SAVES is `https://192.168.1.201:3443/<user>/SAVES.git`.
**GitHub is retired (Bora, 2026-08-05.)** SAVES-side consequences are documented in
`CLAUDE.md` → *Git Workflow*; the constraining decision is in `docs/ROADMAP.md` →
*Decisions locked*.

**⚠️ This forge is now the only copy of that history.** With GitHub gone there is no off-site
remote. `/volume1/docker/forgejo` — the repo tree **and** a `pg_dump` of the database — must
be in the backup set (see *Backups* above). Local clones hold the commits but not issues,
PRs, or settings.

**Same-NAS co-tenancy.** SAVES also *deploys* to this NAS (`/volume1/docker/saves`, Compose
project `saves_app`), so the two stacks share RAM, disk, and the DSM firewall:

| Concern | Forgejo | SAVES |
|---|---|---|
| Published ports | `192.168.1.201:3443` | **none** — dials out only (Discord, Whisper, Anthropic) |
| Memory | 2 GB + 1 GB (Postgres) | `SAVES_MEM_LIMIT`, 4 GB (NAS measured 32 GB) |
| CPU quota | ❌ impossible (§6) | ❌ same — never add `cpus:` |
| Runs as | UID 1030 (`sa_forgejo`) : GID 65536 | UID of `sa_saves` : GID 65536 |

Both the **`cpus:` finding (§6)** and the **top-level-`version:`-key finding** were
propagated into `docker/docker-compose.yml`; `scripts/preflight_nas.sh` check `[6]` fails the
deploy if either regresses, and reports RAM headroom against the limits other containers
already reserve.

**SAVES adopted this document's identity model (2026-08-06).** `saves_app` now runs
**non-root** as `sa_saves`, the direct analogue of `sa_forgejo` — same reasoning, same
GID 65536, same DSM-ACL caveat. It is not yet hardened to the *full* standard here
(`cap_drop: ALL`, `read_only`, `no-new-privileges` are still Forgejo-only); that remains a
sensible follow-on, not a rollout prerequisite. SAVES's ownership map, which extends this
model to a human-shared vault via setgid + group `users`, is `docs/PROD_ROLLOUT.md` §1.6.

**The NAS pulls SAVES from the NAS.** `git pull` in `/volume1/docker/saves/app` talks to this
forge over the LAN address, so the forge must be up to *update* SAVES. A running `saves_app`
container needs no git access at all, so a forge outage never interrupts saving. The clone
needs the step-ca root in the NAS's Git trust config plus a `repository: Read` PAT —
sequence in `docs/PROD_ROLLOUT.md` step 1.

---

## LOCKED DECISIONS

1. **Image:** `codeberg.org/forgejo/forgejo:15-rootless` — LTS to 15 July 2027, floating `15` tag for automatic patches. *(15.0.6 unverified; `15` makes it moot.)*
2. **Database:** `postgres:17-alpine` (17.10), `user: "70:70"`, `scram-sha-256`, **no published port**, `internal: true` network only.
3. **Container identity:** `user: "1030:65536"` — UID 1030 with the *`docker_service_accounts`* GID, deliberately **not** DSM's broad `users` GID 100.
3b. **`/etc/passwd` + `/etc/group` mounted read-only** (Phase 4B), rewritten so `git` = 1030:65536. **Mandatory** — without it the web installer cannot be completed. Files are `root:root 644`; `644` is required, not lax.
4. **Volume layout:** single Forgejo volume `data/ → /var/lib/gitea`. **No `/etc/gitea` mount** — removed in Forgejo v15. DB in the same tree per your instruction.
5. **TLS:** Forgejo built-in HTTPS, static cert issued by **step-ca**, at `custom/https/server.crt` + `server.key` with `CERT_FILE`/`KEY_FILE` set explicitly (relative to CustomPath). Host port **3443**. Distribute the step-ca **root** to clients. **Forgejo's built-in ACME is rejected** — it cannot bind :80 under `cap_drop: ALL`; renew externally and restart the container. Keep leaf validity under 398 days.
6. **Git access:** HTTPS + PAT only. `DISABLE_SSH=true`, `START_SSH_SERVER=false`, no SSH port published.
7. **Bootstrap:** web installer first, `INSTALL_LOCK=true` immediately after. **`RUN_USER = git` stays in `app.ini`** — Phase 4B makes it true. Declarative bootstrap deferred to the Terraform rebuild.
7b. **Repository root:** `/var/lib/gitea/git/repositories` — the installer's prefilled value. **Where the cheat sheet and the installer disagree, the installer wins**, because it reflects the image's env overrides.
7c. **Port binding:** `192.168.1.201:3443:3000` — explicit IPv4 bind, no IPv6 listener. `2222/tcp:[]` is exposed-not-published and unreachable.
8. **Hardening:** `cap_drop: ALL`, `no-new-privileges:true`, `read_only: true` + tmpfs, no Docker socket, argon2 password hashing, `OFFLINE_MODE`, SSRF guards on migrations and webhooks.
8b. **Resource limits:** `mem_limit` only. **No `cpus:` — Synology kernels lack CFS bandwidth control and any CPU quota is a hard build failure.** Hard CPU containment waits for the Proxmox migration.
9. **Security posture claim (blog-precise):** *non-root, unprivileged containers on a root daemon, without seccomp filtering.* Two platform caveats you cannot fix from Compose: DSM's `dockerd` runs as root (no supported rootless or `userns-remap` path), and DSM sets `"seccomp-profile": "unconfined"` daemon-wide. Both close on the dedicated-server migration. **Rootless ≠ unprivileged ≠ non-root** — see §3.
10. **Permissions:** POSIX `chown` over SSH — **never** touch this subtree's permissions in File Station or Control Panel afterwards.
11. **Firewall:** DSM firewall allow `192.168.1.0/24 → tcp/3443`, deny-all last. **No router port-forward.**
12. **Claude Code:** works via the OS/Git trust store from Phase 11; `NODE_EXTRA_CA_CERTS` **exported in the shell** (not `settings.json`) only if running the Node distribution.
13. **CI runners:** deferred to the Proxmox server. If ever run on the NAS, **DinD sidecar — never the host Docker socket.**
14. **CA naming + relocation (Bora, 2026-08-06):** the root cert **file** is normalized to `vineyard-root-ca.crt` now (cosmetic, invalidates nothing). The **CA's own Subject** is corrected only when step-ca moves to a dedicated box, because that migration re-initialises the CA anyway — see **Appendix B2**. **Never re-init a running CA to fix a name:** it invalidates every issued certificate and forces a re-trust on every client, for zero functional gain.
15. **This document is repo-canonical (Bora, 2026-08-06).** `docs/FORGEJO.md` in the SAVES repo is the authoritative copy — edit it here, not in an external copy pasted over the top. Two earlier paste-overs silently dropped Appendix C and a requested cert rename. Verify with `git status` before editing.

---

## Sources

Primary, verified 2026-07-30:

- Forgejo releases index and 15.x page — `forgejo.org/releases`
- Forgejo v15.0 release announcement (LTS window, `/etc/gitea` removal) — `forgejo.org/2026-04-release-v15-0`
- Forgejo v15.0.0 release notes (config path migration) — Codeberg milestone 36366
- Forgejo Installation with Docker (rootless example, `FORGEJO__` syntax) — `forgejo.org/docs/latest/admin/installation/docker`
- Forgejo Configuration Cheat Sheet (`CERT_FILE` relative to CustomPath, `INSTALL_LOCK`, `DISABLE_SSH`, `RUN_USER`, `SECRET_KEY`) — `forgejo.org/docs/latest/admin/config-cheat-sheet`
- Forgejo issue #10215 (rootless image ignores `FORGEJO_WORK_DIR`) — `codeberg.org/forgejo/forgejo/issues/10215`
- Codeberg container registry version list — `codeberg.org/forgejo/-/packages/container/forgejo/versions`
- PostgreSQL 17.10 release announcement and notes — `postgresql.org`
- Docker Hub `_/postgres` (arbitrary `--user`, `initdb` constraint, PG18 volume change)
- docker-library/postgres #264 (`/var/run/postgresql` lock file under non-root)
- Synology KB: Container Manager 24.0.2-1630 model availability
- Claude Code enterprise network configuration — `code.claude.com/docs/en/network-config`
- anthropics/claude-code #22512, #26897 (`NODE_EXTRA_CA_CERTS` in `settings.json` not applied)

Community cross-reference: r/synology, r/selfhosted, SynoForum, mariushosting, drfrankenstein.co.uk, blackvoid.club.
