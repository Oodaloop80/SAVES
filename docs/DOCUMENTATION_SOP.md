# SOP · Documentation — Nothing Gets Lost

> **Standing rule (Bora, 2026-08-07):** *"Document EVERYTHING as you go. Nothing can be lost
> because I will not remember. What the current state is, why it is that, how we got there,
> how to set it up, and all the whys."*
>
> This SOP is the contract that makes that true. It applies to **every** document in this
> repo and to every future project on this infrastructure. It is not about writing more — it
> is about writing the *specific things* that are otherwise lost.

**Scope:** SAVES, Forgejo, the NAS, and anything built alongside them.
**Enforcement:** `CLAUDE.md` → "Documentation discipline" makes this mandatory per-commit.

---

## 1 · Why this exists

The failure mode is not bad decisions. It is a **good decision quietly evaporating** because
the reasoning behind it was never written down. Six weeks later nobody remembers *why* the
vault is read-only at the root, so someone "fixes" it, and the tightening is gone.

Three specific losses this SOP prevents, all of which actually happened here:

| Loss | Real example |
|---|---|
| **The why disappears** | `users` (GID 100) was rejected as the vault group. Without the reason recorded, the next permission problem gets "solved" by granting `users` again. |
| **The rejected option gets re-proposed** | An earlier session drifted toward inventing new groups and `group_add` sprawl — an approach that had already been considered and rejected. |
| **A hard-won fix is forgotten** | The `/etc/passwd` mount that unblocks the Forgejo installer. It was solved once in July, forgotten, and cost a full debugging session to rediscover on 2026-08-06. |

> A doc that records *what* without *why* is a trap: it looks authoritative, so it gets
> followed, and nobody can tell when it stopped being true.

---

## 2 · The Six Questions — every documented thing answers all of them

This is the checklist. If a doc section cannot answer these, it is incomplete.

| # | Question | What it looks like |
|---|---|---|
| 1 | **WHAT is true now?** | Exact values, paths, UIDs, versions, commands. No "should be" — what *is*. |
| 2 | **WHY is it that way?** | The reasoning. **Including what was rejected and why.** |
| 3 | **HOW did we get here?** | The failure, decision, or discovery that produced it — **dated and attributed**. |
| 4 | **HOW do I rebuild it?** | Copy-pasteable procedure, from nothing, in order. |
| 5 | **HOW do I verify it?** | The command that proves it, and the expected output. |
| 6 | **WHAT would break it?** | Failure modes, traps, and what *looks* like a fix but isn't. |

### Worked example — all six, compactly

> **Vault root is read-only for `sa_saves`** ①.
> `tag_index` does `os.walk(vault_root)` across the whole vault but writes only inside two
> subfolders, so read is the least verb that works ②. Established 2026-08-06 when scoping the
> ACL grants; an earlier draft granted RW at the vault root, which was more than the code
> needs ③.
> ```bash
> sudo synoacltool -add "$VAULT" "user:sa_saves:allow:r-x---a-R-c--:fd--"   # ④
> sudo docker run --rm -u 1031:65536 -v "$VAULT:/t" alpine \
>   sh -c 'touch /t/.x 2>/dev/null && echo "TOO BROAD" || echo "READ ONLY (correct)"'  # ⑤
> ```
> If this is denied entirely, `/tag add` silently stops autocompleting and the analyzer
> invents near-duplicate tags — **it fails quietly, not loudly** ⑥.

---

## 3 · Evidence labels — never blur measured and assumed

The single most valuable habit from the 2026-08-06 session. Every non-obvious claim carries
its evidence status:

| Label | Means | Must include |
|---|---|---|
| ✅ **VERIFIED** | Actually run, on real hardware | the command, the date, the observed output |
| ⚠️ **ASSUMED** | Believed but untested | what would test it |
| ❌ **REJECTED** | Considered and rejected | why, so it is not re-proposed |
| 🕐 **DEFERRED** | Correct but postponed | the trigger that revisits it |

```markdown
> ✅ **VERIFIED 2026-08-06.** `docker run -u 1031:65536 -v /volume1/MEDIA/SAVES:/t alpine
> touch /t/x` → **WRITE OK**. DSM ACLs bind containerized processes, matched on UID.
```

**Downgrading is mandatory.** If a ✅ claim turns out to rest on inference, relabel it — do not
leave it looking measured. Two claims were downgraded this way on 2026-08-06 and both were
load-bearing.

---

## 4 · Where each kind of knowledge goes

| Document | Owns | Update when |
|---|---|---|
| `CLAUDE.md` | The canonical map — repo tree, data flow, note types, buttons, config, hard constraints | any file, command, config key, or flow is added / renamed / removed |
| `docs/HANDBOOK.md` | Rebuild-from-nothing: setup, dependencies, operations | setup or dependencies change |
| `docs/ARCHITECTURE.md` | How pieces fit; threading; §11 scaling | a component's responsibility or a scaling property changes |
| `docs/ROADMAP.md` | Phases, and **"Decisions locked"** | anything ships, or a decision constrains the future |
| `docs/PROD_ROLLOUT.md` | The guided deploy + acceptance tests + rollback | a deploy step, path, or prerequisite changes |
| `docs/USER_GUIDE.md` | User-facing behavior, gotchas, recovery paths | any user-visible behavior changes |
| `docs/COMMANDS.md` | One-line-per-entry index of commands / buttons / scripts | a command, button, or script is added / renamed / removed |
| `docs/NAS_SERVICE_ACCOUNTS.md` | **Infrastructure SOP** — accounts, groups, the ACL permission scheme | an account is added, or a permission convention changes |
| `docs/FORGEJO.md` | **Infrastructure** — the git forge: build, identity, TLS, backup, troubleshooting | the forge's config, versions, or procedures change |
| `docs/DOCUMENTATION_SOP.md` | **This file** — how we document | the documentation contract itself changes |
| Code comments / docstrings | The point-of-use record of any non-obvious contract | always, alongside the code |

**Two-place rule for decisions:** a decision that constrains future behavior goes in **both**
the point of use (code comment or the relevant doc section) **and** `ROADMAP.md` →
"Decisions locked" — in the same commit. One without the other loses it.

---

## 5 · Debugging sessions produce documentation too

**This is the rule most often skipped, and the most expensive to skip.** A session that
diagnoses a failure has produced knowledge worth more than the fix. Capture:

1. **The symptom, verbatim** — the actual error string, so a future search finds it
2. **The real cause** — often not the first thing suspected
3. **What was tried and did not work** — saves repeating it
4. **The fix**
5. **Why it broke** — what changed, or what was always wrong and only now surfaced
6. **The generalisable lesson** — the part that applies beyond this one bug

Worked example: `FORGEJO.md` → "The 2026-08-06 outage". Three causes in sequence, each
masking the next, with an ordered debug sequence. The generalisable lesson from it — *an ACL
grants access, not ownership; some daemons check ownership independently* — is now SOP §5.2
in `NAS_SERVICE_ACCOUNTS.md`, where it protects every future container, not just that one.

---

## 6 · Rules that keep docs from going stale

These are derived from defects actually found in this repo.

1. **Same commit, never "later."** Docs are part of the change. A commit that alters behavior
   and not its docs is incomplete.
2. **Propagate to every comparable place, immediately.** A rule agreed for one path applies to
   all of them *now*. On 2026-08-06 a single group change needed edits in 10 files; doing 9
   would have left a contradiction.
3. **Audit prior output when a standard changes.** Previously-committed guidance may now be
   wrong. Twice on 2026-08-06 it was — and `grep`ping for it was the valuable part, not the
   new writing.
4. **Never leave two docs disagreeing.** If two say different things, at least one is wrong
   and the reader cannot tell which. The audit found a table prescribing owners directly
   below a paragraph saying no owners should be set.
5. **Record the rejected option.** Otherwise it returns. `users`-as-vault-group and
   `group_add`-for-cross-tree both came back once already.
6. **Cite the code, not the memory.** "`write_note()` calls `os.makedirs()`" beats "notes need
   write access." Claims traced to code survive; claims traced to recollection rot.
7. **Correct in place; do not append.** A correction goes where the wrong statement was, with
   a dated note. Appending a fix at the bottom leaves the wrong version to be read first.
8. **Verify a claim before repeating it.** Stale docs are worse than none, because they are
   trusted.

**The test before committing:** *if someone read only the docs, would they describe the system
correctly and be able to rebuild it?* If not, the doc edit belongs in this commit.

---

## 7 · Commit messages are documentation

Commit messages are the only record of *how we got here* that is automatically preserved and
timestamped. In this repo they carry the reasoning, not just the change:

- **What changed**, and **why** — the problem it solves
- **What was rejected**, and why
- **What was verified**, and how (the command and its result)
- **What is still unproven**

A one-line commit message for a decision-bearing change loses the decision permanently.

---

## 8 · Checklist — before every commit

- [ ] Does every changed behavior have its doc updated **in this commit**?
- [ ] Do the **Six Questions** (§2) all have answers?
- [ ] Are claims labelled ✅ / ⚠️ / ❌ / 🕐 (§3), with no assumption dressed as a measurement?
- [ ] Does a constraining decision appear in **both** places (§4)?
- [ ] If this changed a standard: did I **grep for and fix** every other place it applies (§6.2/6.3)?
- [ ] If this was a debugging session: is the **symptom, cause, fix, and lesson** captured (§5)?
- [ ] Do any two docs now disagree?
- [ ] Does the commit message carry the reasoning (§7)?

---

## 9 · Related

- `CLAUDE.md` → "Documentation discipline" — the per-commit enforcement hook
- `docs/NAS_SERVICE_ACCOUNTS.md` — the model for an infrastructure SOP written to this standard
- `docs/FORGEJO.md` → "Troubleshooting" — the model for a debugging writeup (§5)
- `docs/ROADMAP.md` → "Decisions locked" — the decision register
