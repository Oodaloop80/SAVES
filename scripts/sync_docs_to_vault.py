"""Mirror the SAVES documentation into the Obsidian vault (one-way, repo -> vault).

WHY THIS EXISTS
    The docs are searchable, linkable and readable on the phone when they live in the vault.
    But they must stay *versioned alongside the code they describe* — the DOCUMENTATION_SOP
    contract requires a doc change in the same commit as the behaviour change, which is only
    enforceable in git. So: the repo is canonical, the vault gets a generated mirror.

WHY ONE-WAY, AND WHY THE BANNER
    Two editable copies of one document has already failed on this project. `FORGEJO.md` was
    maintained outside the repo and two paste-overs silently dropped an appendix and a
    requested rename (Locked Decision 15, 2026-08-06). Every mirrored file therefore opens
    with a "do not edit here" banner and is overwritten on every run.

NOT MIRRORED — these live in the vault as their canonical home (moved 2026-08-07):
    OKAYNET/SOP/sop_synology_service_accounts.md   (NAS-wide infrastructure SOP)
    OKAYNET/SELF HOST/forgejo.md                   (the git forge)
    Both are infrastructure, not application docs, and apply to every project — not just SAVES.

USAGE
    python scripts/sync_docs_to_vault.py            # mirror
    python scripts/sync_docs_to_vault.py --check    # report drift, write nothing (exit 1 if stale)
"""

import argparse
import datetime
import glob
import io
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VAULT_DOCS = r"C:\Users\Bora\Documents\OBSIDIAN\REMOTE VAULT\OKAYNET\AI APPS\SAVES"

# Live in the vault as their canonical copy — mirroring them would create a second editable
# copy, which is the exact failure this script's banner exists to prevent.
NOT_MIRRORED = {"FORGEJO.md", "NAS_SERVICE_ACCOUNTS.md"}

BANNER = """> [!warning] Generated mirror — do not edit here
> The authoritative copy lives in the SAVES repo at `{repo_rel}`.
> Edits made in this vault will be **overwritten** on the next sync. Change it in the repo,
> where it is versioned alongside the code it describes.
> *Mirrored {date} by `scripts/sync_docs_to_vault.py`.*

"""


def sources():
    """(basename, repo-relative path) for every doc that gets mirrored."""
    out = [("CLAUDE.md", "CLAUDE.md")]
    for path in sorted(glob.glob(os.path.join(REPO, "docs", "*.md"))):
        base = os.path.basename(path)
        if base not in NOT_MIRRORED:
            out.append((base, "docs/" + base))
    return out


def render(repo_rel: str) -> str:
    body = io.open(os.path.join(REPO, repo_rel), encoding="utf-8").read()
    date = datetime.date.today().isoformat()
    return BANNER.format(repo_rel=repo_rel.replace("/", "\\"), date=date) + body


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true",
                    help="report which mirrors are stale; write nothing")
    args = ap.parse_args()

    if not args.check:
        os.makedirs(VAULT_DOCS, exist_ok=True)
    elif not os.path.isdir(VAULT_DOCS):
        print(f"vault docs dir missing: {VAULT_DOCS}")
        return 1

    stale, written = [], 0
    for base, repo_rel in sources():
        dest = os.path.join(VAULT_DOCS, base)
        new = render(repo_rel)

        # Compare ignoring the banner's date line, so an unchanged doc isn't "stale"
        # just because a day passed.
        def strip_date(t):
            return "\n".join(l for l in t.splitlines() if not l.startswith("> *Mirrored "))

        old = io.open(dest, encoding="utf-8").read() if os.path.exists(dest) else ""
        if strip_date(old) == strip_date(new):
            continue

        if args.check:
            stale.append(base)
        else:
            # Not an atomic replace: this is a generated mirror, and a torn write is fixed by
            # re-running. The zero-delete rule (CLAUDE.md Hard Constraint #1) governs the
            # pipeline's writes to user content; nothing here deletes anything.
            io.open(dest, "w", encoding="utf-8", newline="\n").write(new)
            print(f"  mirrored  {base}")
            written += 1

    if args.check:
        if stale:
            print("STALE mirrors (run without --check to update):")
            for b in stale:
                print(f"  {b}")
            return 1
        print("vault mirror is up to date")
        return 0

    print(f"\n{written} file(s) written to {VAULT_DOCS}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
