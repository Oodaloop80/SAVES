# SOP · Synology NAS — Service Accounts for Apps and Containers

> **Standard operating procedure (Bora, 2026-08-06).** Every app and every container on the
> DS1621+ runs under its **own** DSM service account. No shared accounts, no running as root,
> no reusing `admin`. This document is the rule, the registry, and the procedure.
>
> Scope: `192.168.1.201` (DS1621+, DSM 7.3.2). Applies to anything that owns files on
> `/volume1`. Worked example of the hardened container pattern: `docs/FORGEJO.md`.

---

## 1 · The rule

### 1.1 Naming

```
sa_<appname>          all lowercase, no spaces
```

`<appname>` is the app's own name — `sa_forgejo`, `sa_saves`, `sa_obsidian`. One account per
app or container, never shared between two.

### 1.2 Group assignment — by where the app lives

| The app lives in | Supplementary group | GID |
|---|---|---|
| `/volume1/APPS/…` | `app_service_accounts` | **65537** |
| `/volume1/docker/…` | `docker_service_accounts` | **65536** |

**Primary GID is always `100` (`users`) and you cannot change it.** DSM forces this on every
account and exposes no GUI to alter it. That is a DSM constraint, not a mistake — the
*container* overrides its primary GID at runtime (§4), which is where the tightening happens.

### 1.3 Why one account each

| Control | Effect |
|---|---|
| Blast radius | An app-level CVE is confined to one UID's files. A shared account would expose every app's data at once. |
| Auditability | `ls -l` / DSM logs name the responsible service. A directory owned by `admin` tells you nothing. |
| Revocability | Disable or re-permission one account without touching anything else. |
| Least privilege | Each account gets Read/Write on exactly its own shares, `No Access` everywhere else. |

---

## 2 · Registry — current accounts

Keep this table current. **The UID is assigned by DSM — always read it back, never assume.**

| Account | UID | Primary GID | Supplementary group | Lives in | Runs |
|---|---|---|---|---|---|
| `sa_forgejo` | **1030** | 100 (`users`) | 65536 `docker_service_accounts` | `/volume1/docker/forgejo` | Forgejo git forge + PostgreSQL |
| `sa_saves` | **1031** | 100 (`users`) | 65536 `docker_service_accounts` | `/volume1/docker/saves` | SAVES archiving pipeline |
| `sa_obsidian` | **1032** | 100 (`users`) | 65537 `app_service_accounts` | `/volume1/APPS/OBSIDIAN` | Obsidian vault / sync |

Verified `id` output:

```
uid=1030(sa_forgejo)  gid=100(users) groups=100(users),65536(docker_service_accounts)
uid=1031(sa_saves)    gid=100(users) groups=100(users),65536(docker_service_accounts)
uid=1032(sa_obsidian) gid=100(users) groups=100(users),65537(app_service_accounts)
```

> **Group renamed 2026-08-06:** `service accounts` → `docker_service_accounts`. **A DSM group
> rename does not change its GID** — it stays 65536, so no container config, `chown`, or mount
> was affected. Only prose and expected-output examples needed updating. Numbers cross the
> container boundary; names do not (§4).

---

## 3 · Procedure — creating a new service account

DSM has no supported CLI for user creation. Use the GUI, then verify over SSH.

**3.1 Create it.** Control Panel → **User & Group → User → Create**

1. Name: `sa_<appname>`. Password: long and random — it is never used.
2. **User Groups:** tick `docker_service_accounts` **or** `app_service_accounts` per §1.2.
   Leave `users` ticked (DSM forces it).
3. **Permissions:** Read/Write on **only** the shared folders this app owns. `No Access` on
   everything else.
4. **Applications:** **Deny all** — DSM, File Station, WebDAV, everything. It must not log in.
5. Apply.

**3.2 Read back the real UID — this is mandatory.**

```bash
ssh <admin>@192.168.1.201
id sa_<appname>
```

> ⚠️ **Never hardcode a UID before running this.** DSM assigns UIDs sequentially and you do
> not control the number. Every `chown`, every compose `user:` key, and every `.env` derives
> from this output. Add the result to the registry in §2.

**3.3 Own the app's directories** — see §5 for which owner/group/mode.

**3.4 Verify.**

```bash
stat -c '%n  %U:%G  %a' /volume1/<tree>/<app> …
```

---

## 4 · ⚠️ The Docker trap: containers do NOT inherit supplementary groups

This is the single most important thing in this document, and it is not obvious.

Docker's `user: "UID:GID"` sets the process's **effective primary GID directly**. It does
**not** read DSM's group database and does **not** inherit the account's supplementary
groups. (`FORGEJO.md` §1 documents the same finding.)

So although DSM says `sa_saves` is in `users` (100) *and* `docker_service_accounts` (65536),
a container started as `user: "1031:65536"` has exactly **one** group: 65536. It is **not** in
`users`, and it cannot write anything that relies on group-`users` permission.

**Fix: restate the needed groups with `group_add:`.**

```yaml
services:
  app:
    user: "1031:65536"        # primary GID = the service group (tight by default)
    group_add:
      - "100"                 # users — required to write the human-shared vault/media
```

Rules of thumb:

- `user:` picks the **primary** GID → this is what new files get *unless* a setgid directory
  overrides it. Keep it as the **service** group.
- `group_add:` grants **additional** memberships → this is how a container reaches a directory
  owned by a different group.
- **Only add groups the DSM account genuinely has.** `group_add` will happily grant a
  membership the real account does not possess; that silently diverges the container from its
  own identity and defeats the audit story. If a container needs a new group, add it to the
  DSM account first, re-run `id`, then update the registry *and* the compose file.

### Names vs numbers across the boundary

The kernel enforces **numbers only**. The container's `/etc/passwd` and `/etc/group` may map
65536 to a completely different name than DSM does (Forgejo's mounted files call it `git`) and
both are correct simultaneously. This is why a DSM group rename is a no-op for containers, and
why every `chown` in a runbook should use the **numeric** id.

---

## 5 · Directory ownership convention

**The group is chosen by the audience, not by the tree.** Two categories:

### 5.1 Service-private data — nothing human-facing reads it

Databases, runtime state, session/credential stores, caches.

| | Value |
|---|---|
| Owner | `sa_<app>` |
| Group | the app's service group (65536 or 65537) |
| Mode | **`2770`** dirs / `660` files |
| Credentials (cookies, browser profiles, keys) | **`2700`** / `600` |

### 5.2 Human-shared data — a person edits or browses it

Obsidian vaults, media libraries, document shares — anything reached over SMB or by a
desktop app.

| | Value |
|---|---|
| Owner | `sa_<app>` (whichever service writes it) |
| Group | **`users` (100)** |
| Mode | **`2775`** dirs / `664` files |

**Why `users` here and not a service group:** a human edits these. Locking a vault to
`app_service_accounts` locks *you* out of your own notes over SMB. The security boundary that
matters is §5.1 — credentials and state — not your document library, which every DSM account
can already reach through the share anyway.

### 5.3 The two bits that make it work

- **setgid (`2xxx`)** — new files and subdirectories inherit the **directory's** group instead
  of the creating process's. Without it, a service writing into a shared directory stamps its
  own service group on every file and the human loses write access one file at a time.
- **umask `002`** in the writing process — produces `664`/`775` instead of `644`/`755`. Without
  it the group bit is decorative and setgid achieves nothing. (SAVES sets this in
  `src/main.py`.)

---

## 6 · Worked example: cross-tree access (SAVES → the Obsidian vault)

The case the SOP has to handle, and the reason §4 matters:

| | |
|---|---|
| Writer | `saves_app` container, `sa_saves` = 1031, service group 65536 (`/volume1/docker`) |
| Target | `/volume1/APPS/OBSIDIAN/Remote Vault` — the **Obsidian** app's tree, `sa_obsidian` = 1032, group 65537 |
| Also uses it | Bora over SMB; the Obsidian sync client |

Two different service accounts, two different trees, two different service groups, plus a
human. Resolution:

```bash
# The vault is human-shared data (§5.2) — owner is the app that owns the tree, group is users:
sudo chown -R 1032:100 "/volume1/APPS/OBSIDIAN/Remote Vault"
sudo chmod -R 2775     "/volume1/APPS/OBSIDIAN/Remote Vault"

# Media is written by SAVES and browsed by a human — same category, different owner:
sudo chown -R 1031:100 /volume1/MEDIA/SAVES
sudo chmod -R 2775     /volume1/MEDIA/SAVES

# SAVES's own private state stays on the service group (§5.1):
sudo chown -R 1031:65536 /volume1/docker/saves/state
sudo chmod    2770       /volume1/docker/saves/state
```

```yaml
# …and the container must be told it is in `users`, because Docker won't infer it (§4):
user: "1031:65536"
group_add: ["100"]
```

Result: SAVES writes notes into Obsidian's vault via group `users`; setgid stamps each new
note `…:users` so Obsidian and SMB can still edit it; SAVES's credentials and state stay
locked to `docker_service_accounts`; neither service can read the other's private data.

---

## 7 · ⚠️ The DSM ACL trap — applies to every path above

Containers honour **POSIX** ownership. DSM layers its own **ACLs** on top, invisible from
inside a container.

> **Once POSIX ownership is set over SSH, never open that folder's permissions in File Station
> or Control Panel → Shared Folder → Edit → Permissions.** DSM rewrites ACLs across the whole
> subtree and clobbers the POSIX owner — the container then fails at its next restart with
> permission errors that look nothing like their cause.

Change permissions here **only** with `chown`/`chmod` over SSH.

---

## 8 · Checklist — adding a new app or container

- [ ] Pick the name: `sa_<appname>`
- [ ] Decide the tree → `/volume1/APPS` (65537) or `/volume1/docker` (65536)
- [ ] Create the account in DSM; deny all applications; grant Read/Write only on its shares
- [ ] `id sa_<appname>` → **record the real UID in §2**
- [ ] Create its directories with owner + mode per §5 (service-private vs human-shared)
- [ ] If it is a container: set `user: "<uid>:<service-gid>"`, and `group_add` any group it
      genuinely needs and genuinely has
- [ ] Ensure the process uses umask `002` if it writes human-shared data
- [ ] `stat -c '%n %U:%G %a'` every path to verify
- [ ] Never touch those permissions in the DSM GUI afterwards (§7)

---

## 9 · Related

- `docs/FORGEJO.md` — the reference hardened-container build (non-root UID, `cap_drop: ALL`,
  `read_only`, `no-new-privileges`, the `/etc/passwd` mount). §1 covers the identity model
  in depth; §3 defines rootless vs unprivileged vs non-root.
- `docs/PROD_ROLLOUT.md` §1.6 — SAVES's concrete ownership map, built on this SOP.
- Not yet applied to SAVES: the full Forgejo hardening set (`cap_drop`, `read_only`,
  `no-new-privileges`). Non-root is done; the rest is a sensible follow-on.
