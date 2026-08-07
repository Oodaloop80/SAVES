# Backup & Recovery — the Obsidian vaults

> **Requirement (Bora, 2026-08-07):** *"I'd like a one way backup of all my vaults (not just
> remote vault) to my NAS with versioning. The workstation's vault will be the source of truth…
> if AI is touching my vaults, something bad could happen and I want to be able to recover."*

**Source of truth: the workstation.** The NAS holds a one-way copy plus history. Nothing ever
syncs *back* into a vault.

---

## 1 · The principle that drives the whole design

> **A one-way sync is not a backup.** It faithfully replicates destruction. If something
> mangles or deletes a note on the workstation, the next sync copies that state to the NAS and
> the good version is gone from both.

**Versioning is the entire value; the transport is almost incidental.** Every choice below is
made on that basis — which is why the immutable-snapshot layer matters more than the copier.

---

## 2 · The chosen stack (Bora, 2026-08-07)

Four layers, each catching a failure the others don't.

| # | Layer | Catches | Where it runs |
|---|---|---|---|
| 1 | **Obsidian File Recovery** — 5 min / **30 days** | "I mangled this note an hour ago" | Local, in Obsidian |
| 2 | **Obsidian Sync** | Device loss; phone ↔ workstation | Obsidian service |
| 3 | **Synology Drive Client — Backup Task** | Workstation dies | Windows → NAS, one-way |
| 4 | **Synology immutable (WORM) snapshots** | Mass corruption, ransomware, a bad actor *or a bad script* | NAS, on the destination share |

### Layer 1 — Obsidian File Recovery *(core plugin)*

Settings → Files and links → File recovery: **snapshot interval 5 minutes**, **keep 30 days**.

> Obsidian's default retention is **7 days**; 30 is a deliberate increase. This is the layer
> that resolves ~90% of real incidents, in two seconds, with no network involved. Enable it
> first — it is free and instant.

### Layer 2 — Obsidian Sync

Device-to-device (phone ↔ workstation). Also carries its own version history.

### Layer 3 — Synology Drive Client, **Backup Task** *(not Sync Task)*

> ### ⚠️ The single most important setting in this document
>
> **It must be a *Backup Task*, not a *Sync Task*.** This is the difference between "safe" and
> "the OneDrive problem":
>
> | | Sync Task | **Backup Task** |
> |---|---|---|
> | On-demand / placeholder files | **Yes** — Obsidian indexes files that aren't really on disk. *This is what wrecks vaults.* | **No** |
> | Writes back into your folder | Yes — conflict copies land *inside* the vault, `.obsidian` fights across devices | **No** — one-way, read-and-upload |
>
> The Drive Client's most prominent setup flow is the **Sync** Task. Choosing it by accident
> reproduces exactly the failure that drove the vault off the network drive in the first place.

Also: **version control is OFF by default** on the destination. Enable it in **Synology Drive
Admin Console** or you will have a copy with no history and believe you have backups. Ceiling
is **32 versions** per file — which is why Layer 4 exists.

> **Do not run Obsidian Sync and a Drive Client *Sync* Task on the same vault** — two sync
> services on one vault is a documented data-loss path. Obsidian Sync alongside a one-way
> *Backup* Task is fine, because a Backup Task is not a second syncer.

### Layer 4 — Immutable (WORM) snapshots on the NAS

DSM **Snapshot Replication** on the destination shared folder. Requires **Btrfs**.

- Point-in-time copies as often as **every 5 minutes**; up to **256** retained recovery points
- Copy-on-write: seconds to take, almost no space
- **Immutable / WORM (DSM 7.2+, and this NAS runs 7.3.2):** within the retention window a
  snapshot **cannot be deleted — not by a script, not by an administrator, not by a compromised
  admin account**

**This is the layer that answers the actual concern.** Layers 1–3 all sit in the path where
something destructive could propagate. Snapshots sit *outside* it: they restore the **whole
vault** to a moment in time, and nothing flowing through the sync can touch them.

### ⚠️ Immutable *snapshots* — NOT a WORM *shared folder*

DSM offers two features whose names both suggest "can't be changed." They are different, and
picking the wrong one breaks things (Bora, 2026-08-07 — asked while in the WORM dialog):

| | **Immutable snapshots** ← use this | **WORM shared folder** |
|---|---|---|
| What is frozen | the *snapshots* | the *live files* |
| Live folder | stays normal and writable | files become unwritable after the auto-lock timer |
| Configured in | Snapshot Replication | Shared folder → WORM (Mode / Auto Lock / Lock State / Retention) |

**A WORM shared folder breaks both of our destinations:**

- **Media.** `download_media()` builds `save_dir` from `_source_subdir(author, title, source_url)`
  — **deterministic per URL** (`downloader.py:66`). A **🔁 Re-save re-downloads to the same
  path**, and the HEIC→JPG conversion writes derived files into the same directory. Against
  WORM-locked files, yt-dlp fails with permission denied and the media download fails. Re-save
  is a normal workflow, not an edge case.
- **Vault backup.** Notes change constantly, so Synology Drive's Backup Task must overwrite the
  destination copy. Immutable files mean it cannot write, and the backup silently stops working.

**If WORM is used anyway** (it fits a *finalized* archive nothing ever rewrites — a possible
future split of "approved media" from "working store"):

| Setting | Choice | Reason |
|---|---|---|
| **Mode** | **Enterprise** — ❌ never Compliance | Compliance means **nobody** can delete the share or its data before retention expires — not you, not an admin, no override. A misconfiguration is permanent and may require destroying the volume. It exists for regulated industries under audit. |
| **Auto Lock** | Enabled, timer **≥ 24 h** | Must comfortably exceed the longest download + conversion, or files lock mid-write |
| **Lock State** | **Immutable** | Append-only permits appending but not modifying — that is for logs. Media files are complete when written. |
| **Retention** | Start **7 days**, extend later | Extending is possible; shortening generally is not |

---

## 3 · Share layout — group by retention need, not by content type

**Snapshots are configured per *shared folder*.** That is the unit of policy, so the split is
driven by how each dataset behaves — not by what it contains.

The fact that decides it: **copy-on-write snapshots only consume space when data changes or is
deleted.** SAVES media is effectively append-only (zero delete calls; existing files are never
rewritten except by a deliberate re-save), so dozens of snapshots of it cost almost nothing.
Mixed into a folder that churns, those same snapshots capture every change and cost real space.

| Shared folder | Holds | Churn | Snapshots | Immutable lock |
|---|---|---|---|---|
| **`OBSIDIAN_BACKUP`** (new) | backup of **all** vaults | high churn, tiny files | hourly · 24h/30d/12w/12m ≈ 78 | **7 days** to start |
| **`ARCHIVE`** (new) | SAVES media (`media_root`) | append-only | daily · 30d/12w/12m ≈ 54 | **30 days** — cheap here |
| **`MEDIA`** (existing) | general media | churns | your call | none |

Cap is **256** snapshots per shared folder, so both policies sit well inside it.

> ⚠️ **Start the lock short and lengthen it.** A locked snapshot **cannot be deleted by anyone,
> including you, until it expires.** If something writes 500 GB and gets snapshotted, that space
> is unreclaimable for the whole lock period. Run 7 days, watch the space behaviour for a
> fortnight, then extend. The reverse is not possible.

**Prerequisite:** the volume must be **Btrfs** — Storage Manager → Volume → File System.

**Retire `/volume1/APPS/OBSIDIAN/Remote Vault`** once the backup is verified. With SAVES on the
workstation it has no reader and no writer; leaving it invites mistaking a stale copy for the
real one.

## 4 · Scope — *all* vaults, not just Remote Vault

The requirement is every vault. Point the Backup Task at the parent
(`C:\Users\Bora\Documents\OBSIDIAN\`) rather than one vault, so a new vault is covered the day
it is created instead of being silently unprotected until someone remembers.

---

## 5 · What SAVES itself can and cannot do to a vault

Relevant to the threat model, and **verified 2026-08-07**, not assumed:

```bash
grep -rn "os.remove\|os.unlink\|shutil.rmtree\|\.unlink(" src/ scripts/    # → no matches
```

- ✅ **Zero delete calls anywhere in `src/` or `scripts/`** — Hard Constraint #1. SAVES has no
  code path that removes a file.
- ✅ **Writes are atomic** — `tempfile.mkstemp()` + `os.replace()`, so a crash mid-write cannot
  leave a truncated note.
- ✅ **Filename collisions get a `-NN` suffix** rather than overwriting.
- ✅ **Re-saves *rename* the old note to `.md.bak`** (`retire_note_to_bak`) — never delete.
- ⚠️ It **can** create files and folders anywhere inside `vault_root` (the write sandbox is the
  vault, not `SAVES/`), so a wrong `folder_path` puts a note somewhere unexpected. Recoverable,
  and it cannot destroy anything.

**Net:** SAVES is a low-risk writer by design. The real risk surface is Obsidian plugins, sync
misconfiguration, and ordinary human error — which is what Layers 1 and 4 are for.

---

## 6 · Verify it — a backup you have never restored from is not a backup

Do this **once, now**, and again after any change to the stack.

```
1. Create  0 - INBOX/_restore-test.md  with a known string.
2. Wait for the Backup Task to run, and for a snapshot to be taken.
3. Edit the file to something different. Then delete it.
4. Recover it three ways, and confirm each independently:
     a. Obsidian File Recovery      -> the previous content       (Layer 1)
     b. Synology Drive version list -> an earlier version         (Layer 3)
     c. A NAS snapshot              -> the file as of that moment (Layer 4)
```

If any of the three cannot produce the file, that layer is not configured the way you think it
is. Find out now, not during an incident.

---

## 7 · Off-site — the remaining gap

Everything above lives in **one building**. Fire, theft, or flood takes the workstation and the
NAS together. Standard practice is **3-2-1**: three copies, two media types, one off-site.

**Hyper Backup → Backblaze B2 or Synology C2**, of the backup share, closes it for a few dollars
a month. 🕐 **DEFERRED** — not required for the SAVES rollout; revisit once the local layers are
verified.

---

## 8 · Recovery quick reference

| Situation | Go to |
|---|---|
| Mangled one note, minutes ago | **Obsidian File Recovery** (Layer 1) |
| Deleted a note today | File Recovery, then `.trash` in the vault |
| Need a note as it was last week | Synology Drive version history (Layer 3) |
| Many notes wrong / mass corruption | **NAS snapshot** — restore the whole share to a point in time (Layer 4) |
| Workstation is gone | Restore the vault from the NAS backup share |
| A SAVES re-save replaced a note | The old one is beside it as `<name>.md.bak` — never deleted |

---

## 9 · Related

- `docs/PROD_ROLLOUT.md` — the workstation deployment this protects
- `CLAUDE.md` → Hard Constraints — the zero-delete rule verified in §4
- `docs/DOCUMENTATION_SOP.md` — why §4's claims carry commands and dates rather than assurances

**Sources consulted 2026-08-07:**
[Drive Client backup tutorial](https://kb.synology.com/en-au/DSM/tutorial/How_to_back_up_data_on_my_computer_using_Drive) ·
[Drive specs / versioning](https://www.synology.com/en-global/dsm/7.4/software_spec/synology_drive) ·
[Snapshot Replication](https://www.synology.com/en-us/dsm/feature/snapshot_replication) ·
[Immutable WORM snapshots, DSM 7.2](https://ifeeltech.com/blog/synology-snapshots-explained) ·
[Btrfs data protection](https://www.synology.com/en-br/dsm/Btrfs) ·
[Obsidian: never run two sync services on one vault](https://huggingface.co/spaces/anpigon/obsidian-qa-bot/blob/f7189e0d855ccced9dc51b88508739e4e83dfc63/docs/obsidian-help/Getting%20started/Sync%20your%20notes%20across%20devices.md)
