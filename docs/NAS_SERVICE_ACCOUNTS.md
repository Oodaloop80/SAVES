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

Docker's `user: "UID:GID"` sets the process's **effective primary GID directly**. It does
**not** read DSM's group database and does **not** inherit the account's supplementary
groups. (`FORGEJO.md` §1 documents the same finding.)

So although DSM says `sa_saves` is in `users` (100) *and* `docker_service_accounts` (65536),
a container started as `user: "1031:65536"` has exactly **one** group: 65536.

**The resolution is NOT `group_add`.** Reaching for `group_add` — or inventing a new shared
group per sharing need — is what turns a permission question into a sprawl of half-understood
memberships. Grant the **account** directly with a DSM ACL entry instead (§5). An ACL `user:`
ACE matches on **UID**, which the container always carries correctly, so group membership
never enters into it.

| | |
|---|---|
| ❌ Don't | add the container to a broad group (`users`), or mint a new group to share one folder |
| ✅ Do | add one ACL ACE naming the account, on exactly the folder it needs |

`group_add` remains legitimate for one narrow case: a POSIX-only path whose group the account
genuinely holds. It is **not** how APPS-tree access is granted here, and SAVES's compose file
carries none.

### Names vs numbers across the boundary

The kernel enforces **numbers only**. The container's `/etc/passwd` and `/etc/group` may map
65536 to a completely different name than DSM does (Forgejo's mounted files call it `git`) and
both are correct simultaneously. This is why a DSM group rename is a no-op for containers, and
why every `chown` in a runbook should use the **numeric** id.

---

## 5 · The permission scheme (Bora, 2026-08-06) — this is the standard

Shared storage on this NAS is governed by **DSM ACLs**, not POSIX groups. Four rules.

### Rule 1 — `users` (GID 100) is granted NOTHING, anywhere

DSM force-adds **every** account to `users`, so it is the everyone-group. Granting it any
right on any path makes that path reachable by every present *and future* account on the NAS.
It must never appear in an ACL as an allow, and must never be the group on a `chown`.

> This supersedes an earlier revision of the SAVES docs that suggested group `users` + setgid
> for "human-shared" data. That was wrong. There is no category of data on this NAS for which
> the everyone-group is the right answer.

### Rule 2 — Only `OodaAdmin` + `administrators` hold Full Control

Full Control = read + write + **take ownership** + **modify permissions**. No service account
ever gets it. A service account that can rewrite its own ACL can undo every other rule here.

### Rule 3 — Tree-level grants are deliberate, and the two trees differ

As actually built (verified 2026-08-06):

| Tree | Tree-level allows | Tree-level denies |
|---|---|---|
| `/volume1/APPS` | `OodaAdmin`, `administrators`, **`app_service_accounts`** | `docker_service_accounts`, `sa_forgejo`, `Bora`, `http`, `guest`, `admin`, `Malana` |
| `/volume1/docker` | `OodaAdmin`, `administrators`, `ContainerManager`, owner | `app_service_accounts`, `sa_obsidian` |
| `/volume1/MEDIA` | `administrators` | **`docker_service_accounts`**, `sa_forgejo`, `Bora`, `guest`, `admin`, `Malana` |

**The asymmetry is intentional and worth understanding.** `/volume1/APPS` grants its service
group (`app_service_accounts`) RW at the tree level, so any APPS app can operate in the tree.
`/volume1/docker` grants its service group **nothing** — each container is instead named
individually on its own project folder (`user:sa_saves:allow` on `/volume1/docker/saves`).

The `/volume1/docker` model is the tighter of the two: it means `sa_forgejo` cannot read
`/volume1/docker/saves` — no shared-group path exists between two containers. Prefer it for
new trees. Where a tree-level group grant already exists, treat it as a *classification*
grant, never as a licence to reach another tree.

> **Closed 2026-08-06:** `/volume1/MEDIA` now carries the `docker_service_accounts` deny too,
> so all three trees deny the other tree's service group consistently.
>
> ⚠️ **Consequence worth noting:** `sa_saves` is *in* `docker_service_accounts`, so MEDIA is
> now a second place where the scheme depends on ACE **ordering** — the `level:0`
> `user:sa_saves:allow` on `/volume1/MEDIA/SAVES` must keep sorting ahead of the inherited
> tree deny. Adding a deny tightens the default but makes one more path order-sensitive.
> Both are on the re-verify list after any GUI permission change (§5.1, §7).

### Rule 4 — Everything else is denied; cross-tree access is a NAMED exception

Principals that must never touch a tree get an **explicit deny** (`guest`, `http`, the other
tree's service group, unrelated service accounts). Where one app genuinely needs another app's
data, grant **that one account, on that one folder, with the least verb it needs**:

```bash
# read-only where it only reads:
sudo synoacltool -add "<path>" "user:sa_<app>:allow:r-x---a-R-c--:fd--"
# read+write where it actually writes:
sudo synoacltool -add "<path>" "user:sa_<app>:allow:rwxpdDaARWc--:fd--"
```

Scope every grant to the narrowest folder that works. Never grant at a tree root for
convenience.

> ⚠️ **Order matters — the scheme's one fragility.** An explicitly-set ACE (`level:0`) sorts
> *before* an inherited deny (`level:3+`), which is what lets a named exception beat the
> tree-wide deny. If the ACL is rebuilt — most commonly by applying permissions from a parent
> folder in File Station (§7) — the level-0 exception can be lost or re-sorted behind the deny,
> and the app **silently** loses access. Re-verify (§5.1) after any GUI permission change.

### 5.1 — Verify, and verify from inside a container

`synoacltool -get <path>` lists the ACEs; DSM's **Permission Inspector** shows how DSM
evaluates them for one account. Neither proves the kernel applies the ACL to a *container*
process, which never authenticated through DSM.

> ✅ **VERIFIED 2026-08-06 — DSM ACLs are enforced for containerized processes.**
> `docker run -u 1031:65536 -v /volume1/MEDIA/SAVES:/t alpine touch /t/.acltest` → **WRITE OK**,
> against a path whose only grant is `user:sa_saves:allow` at `level:0`. The container carries
> no DSM session and is not in any group that is granted there, so the write can only have
> succeeded by the kernel matching the ACE on **UID**.
>
> **This is the assumption the whole scheme rests on.** Everything else — named exceptions,
> `fd--` inheritance, default-deny trees — is only meaningful if the kernel honours an ACL for
> a container. It does. Re-verify after a DSM major upgrade.

Re-run this whenever you add a grant, and **always** after any permission change made through
the GUI (§7):

```bash
sudo docker run --rm -u <uid>:<gid> -v "<host path>:/t" alpine \
  sh -c 'touch /t/.acltest 2>/dev/null && { echo "WRITE OK"; rm -f /t/.acltest; } || \
         { ls /t >/dev/null 2>&1 && echo "READ ONLY" || echo "NO ACCESS"; }'
```

#### The other half: prove the DENIES bind too

The test above proves **allows** are honoured. The security posture depends equally on
**denies** and on default-deny actually holding for containers — an untested assumption in the
opposite direction, and the more dangerous one to get wrong. Run both negatives:

```bash
# 1. EXPLICIT DENY — sa_forgejo (1030) is denied on MEDIA. Expect BLOCKED.
sudo docker run --rm -u 1030:65536 -v /volume1/MEDIA/SAVES:/t alpine \
  sh -c 'touch /t/.denytest 2>/dev/null && { echo "*** WRITE OK — DENY NOT ENFORCED ***"; rm -f /t/.denytest; } || echo "BLOCKED (correct)"'

# 2. DEFAULT DENY — an unused UID with no ACE anywhere. Expect BLOCKED.
sudo docker run --rm -u 9999:9999 -v /volume1/docker/saves:/t alpine \
  sh -c 'touch /t/.denytest 2>/dev/null && { echo "*** WRITE OK — DEFAULT-DENY NOT ENFORCED ***"; rm -f /t/.denytest; } || echo "BLOCKED (correct)"'
```

Anything other than `BLOCKED` means container isolation on this NAS is weaker than the ACLs
suggest, and every "denied" principal in §2 needs re-thinking.

### 5.2 — Which regime governs a path, and the `chmod` trap

**Every `/volume1` share on this NAS is ACL-managed.** Confirmed 2026-08-06 — `/volume1/APPS`,
`/volume1/MEDIA` and `/volume1/docker` all report `has_ACL,is_support_ACL`. Check before
assuming:

```bash
sudo synoacltool -get <path> | head -3
#   Archive: has_ACL,is_support_ACL   -> the ACL is authoritative; use synoacltool
#   (no has_ACL)                      -> plain POSIX; chown/chmod applies
```

> ⚠️ **Do not `chmod`/`chown` inside an ACL-managed share.** On DSM the POSIX mode shown by
> `ls -l` is a *synthesized approximation* of the ACL (which is why these folders read
> `drwxrwxrwx+` — the `+` means "there is an ACL, this mode is not the real story"). Running
> `chmod` against such a path can rewrite or drop the ACL, silently removing the named
> exceptions the whole scheme depends on. Manage permissions with **`synoacltool` only**.
>
> The one exception is setting a share's top-level owner before any ACEs exist, e.g.
> `sudo chown -R OodaAdmin:administrators /volume1/MEDIA/`.

### 5.3 — Inheritance does the work; do not grant per file

The `fd--` flags on an ACE mean **f**ile + **d**irectory inheritance: the grant propagates to
everything created beneath it. One ACE on a project root covers the whole subtree.

`/volume1/docker/saves` carries `user:sa_saves:allow:rwxpdDaARWc--:fd--` at `level:0`, so
`app/`, `app/cookies/`, `app/logs/` and `state/` are **already covered**. Adding per-file
`chown`/`chmod` there is not tightening anything — it is the `chmod` trap above.

**Are secrets safe under a subtree grant?** On `/volume1/docker`, `docker_service_accounts` is
granted nothing, so `sa_forgejo` has **no** access to `/volume1/docker/saves` at all — the
concern about the forge reading SAVES's API key is handled by the tree ACL, not by file modes.
Everyone who *can* read it (`sa_saves`, `administrators`, `OodaAdmin`, `ContainerManager`) is
trusted by design.

## 6 · Worked example: SAVES reaching the Obsidian vault

The case that shaped the scheme. SAVES (`sa_saves`, a **docker**-tree account) must read and
write inside `/volume1/APPS/OBSIDIAN/Remote Vault` — an **APPS**-tree path.
`docker_service_accounts` is denied on APPS, which is correct and stays.

Three grants, each the narrowest that works, traced to what the code actually does:

| Path | Grant | Because |
|---|---|---|
| `Remote Vault/` | **read + traverse** | `tag_index` walks the *entire* vault to build tag autocomplete and the taxonomy hint. Denied, it returns nothing **silently** — autocomplete dies and the analyzer starts inventing near-duplicate tags |
| `Remote Vault/0 - INBOX/SAVES/` | **read + write** | the inbox rewrite is `tempfile.mkstemp(dir=<same dir>)` + `os.replace()` — needs **create file** *and* **delete child** on the directory, not write on the file |
| `Remote Vault/SAVES/` | **read + write** | `write_note()` calls `os.makedirs()` — notes land here and it **creates nested folders**; this is *not* the inbox folder |

```bash
V="/volume1/APPS/OBSIDIAN/Remote Vault"
sudo synoacltool -add "$V"                  "user:sa_saves:allow:r-x---a-R-c--:fd--"
sudo synoacltool -add "$V/0 - INBOX/SAVES"  "user:sa_saves:allow:rwxpdDaARWc--:fd--"
sudo synoacltool -add "$V/SAVES"            "user:sa_saves:allow:rwxpdDaARWc--:fd--"
```

Compose stays minimal — no `group_add`, because the ACL matches `sa_saves` by UID:

```yaml
user: "1031:65536"
```

Net effect: containers as a class still cannot touch APPS; `sa_saves` reads the vault and
writes exactly two folders; `sa_forgejo` stays denied; Full Control stays with `OodaAdmin` and
`administrators`; `users` is granted nothing anywhere.

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
- [ ] Create the account in DSM; **deny all applications**; grant Read/Write only on its shares
- [ ] `id sa_<appname>` → **record the real UID in §2**
- [ ] Ownership: `OodaAdmin` + `administrators` keep Full Control (Rule 2) — **never** the
      service account
- [ ] Confirm `users` appears **nowhere** in the ACL and is not the group on any `chown`
      (Rule 1)
- [ ] Does it need another app's data? Add **one named ACL ACE per folder**, least verb —
      read-only where it only reads (Rule 4). Do **not** create a group for it, and do **not**
      grant at a tree root
- [ ] POSIX paths: credentials `600`/`700`, state and logs `2750` owned by the account with
      group `administrators` (§5.2) — **not** the tree service group, which its siblings share
- [ ] If it is a container: `user: "<uid>:<service-gid>"`, **no `group_add`** unless a
      POSIX-only path needs a group the account genuinely holds
- [ ] Verify: `synoacltool -get` **and** the container write test (§5.1) — Permission Inspector
      alone does not prove container access
- [ ] Never touch those permissions in the DSM GUI afterwards (§7); if you do, re-verify

---

## 9 · Related

- `docs/FORGEJO.md` — the reference hardened-container build (non-root UID, `cap_drop: ALL`,
  `read_only`, `no-new-privileges`, the `/etc/passwd` mount). §1 covers the identity model
  in depth; §3 defines rootless vs unprivileged vs non-root.
- `docs/PROD_ROLLOUT.md` §1.6 — SAVES's concrete ownership map, built on this SOP.
- Not yet applied to SAVES: the full Forgejo hardening set (`cap_drop`, `read_only`,
  `no-new-privileges`). Non-root is done; the rest is a sensible follow-on.
