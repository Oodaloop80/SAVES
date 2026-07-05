# SAVES — User Guide (the nuances)

This is the "how do I actually use it" doc — every non-obvious behavior, gotcha, and
recovery path in one place. If you ever think *"wait, why did it do that?"*, the answer
should be here. (Architecture lives in `ARCHITECTURE.md`; the canonical system map in
`CLAUDE.md`; plans in `ROADMAP.md`.)

> **Rule (see CLAUDE.md → Documentation discipline):** every user-facing nuance — slash
> command, button, surprising default, recovery path — gets documented HERE in the same
> commit that creates or changes it.

---

## Saving things

- **The inbox is one file:** `0 - INBOX/SAVES.md` in the vault. Paste URLs there (one per
  line); the watcher picks them up within ~3 seconds. Nothing else in the vault triggers
  processing.
- **Everything is immediate.** Extraction, AI analysis, and the Discord approval card fire
  the moment a URL lands. Nothing is batched or deferred (locked decision — ROADMAP
  "Decisions locked").
- **URLs are processed one at a time**, in order. A slow save (video transcription,
  web-search fact-check) delays the ones behind it, not the bot's buttons.
- **The URL stays in the inbox until you approve.** Approval (✅) writes the note, saves the
  learned folder preference, and removes the line from the inbox. If a line vanishes
  *without* an approval, it was a duplicate (see below).

## Duplicates — the #1 gotcha

**The source of truth for "already saved?" is `processing_state.json`, NOT the vault.**
Deleting a note in Obsidian does *not* make SAVES forget the URL — re-pasting it will be
skipped as a duplicate, forever, until you tell the state file to forget it.

When you paste an already-saved URL:

1. A **"🔁 Duplicate — already saved"** notice appears in `#SAVES-approvals` (with the
   existing note's path) — it lives with the normal approval cards because it carries a
   decision. The line is cleared from the inbox, and **no tokens are spent**.
2. The notice has two buttons:
   - **🔁 Re-save** — forgets the URL, **renames the old note to `<name>.md.bak`** (never
     deleted — zero-delete policy; a second re-save gets a timestamped `.bak` so nothing is
     overwritten), and requeues the URL through the normal pipeline. You get a fresh
     approval card, and the new note takes the original filename.
   - **✖ Dismiss** — keeps the existing note; nothing changes. Changed your mind later?
     Just paste the URL again — you get a fresh notice every time (it works the same
     whether the original save happened this session or long ago).
3. **Buttons on old notices die when the bot restarts** (they're in-memory only). The
   fallback is the `/forget` command + re-pasting the URL — identical effect, minus the
   automatic `.bak` rename (retire the old note yourself if you care about the filename).

## Slash commands — where and how

Slash commands are typed **in the message box of any text channel** in the "Bora's AI Ops"
server (e.g. `#SAVES-logs`): type `/` and Discord pops up a command picker; keep typing
(`/forget`, `/tag`) to filter, then **pick the command from the popup** — sending the text
`/forget ...` as a plain message does nothing.

**If the commands don't appear in the picker at all:**
1. **Restart the bot** (`python src\main.py`). Commands register at startup and sync to the
   server in `on_ready` — a bot process started *before* a command existed will never show
   it. This is the most common cause.
2. Check the log for `Slash commands synced to N guild(s)`. If you instead see
   `Slash-command sync failed`, the bot was invited without the **`applications.commands`**
   OAuth scope — re-invite it from the Discord developer portal with both `bot` and
   `applications.commands` scopes checked (no need to kick it first).
3. Guild-scoped sync is instant; if you *just* restarted, give it a few seconds and reopen
   the picker.

### `/forget <url>` — make SAVES forget a URL so it can be saved again

- Drops the URL from `processing_state.json` and the in-session dedup sets. After it, the
  URL is a brand-new save: paste it into the inbox and it reprocesses from scratch.
- **Autocompletes over your saved history** — start typing any part of the URL and pick
  from the list. Offers `done` entries and permanently-failed ones (shown as `[failed]`);
  URLs over 100 characters can't be offered (Discord limit) — paste those in full.
- Use it when: the note came out wrong and you want a re-run, you changed cookies/prompts
  and want a fresh pass, or you deleted a note in Obsidian and want the URL saveable again.
- Does **not** touch the vault: the old note (if any) stays where it is. The duplicate
  notice's 🔁 Re-save button is the "also retire the old note" variant.

### `/tag add <tag> [item]` — add a tag with real autocomplete

- The **only** place with search-as-you-type over the vault's existing tags (Discord
  modals — the 🏷️ Add Tags button — cannot autocomplete; platform limitation).
  Suggestions show usage counts, most-used first.
- The tag index counts *everything Obsidian counts*: frontmatter `tags:`/`tag:` **and**
  inline body `#tags` — including tags you typed by hand in Obsidian. New tags become
  searchable within ~5 minutes (index TTL), or immediately if SAVES wrote the note.
- `item` picks which pending save to tag (autocompletes by title); default is the newest.

## Approval cards — button nuances

- **The card always shows exactly what will be written.** Every mutation (Add/Remove tags,
  `/tag add`, Change Path, NL Edit, near-duplicate swap) re-renders the original card.
- **🏷️ Add Tags** — type tags space/comma-separated, no `#` needed. Add-only. Typed tags
  are checked against the vault: near-duplicates (airfryer vs air-fryer) get a one-tap
  "Use existing" swap offer.
- **Tags are always lowercase.** Whatever you type (`BBQ`, `#Air-Fryer`) — and whatever
  the AI generates — is normalized to lowercase at every entry point (Add Tags, `/tag
  add`, NL Edit, swaps) before it reaches the card or the note, so case-variant duplicate
  tags can't happen.
- **🗑️ Remove Tags** — one ✖ button per tag, tap to remove instantly. Removed one by
  accident? **↩ Undo All** restores the tag list exactly as it was when you opened the
  remover, so you can start over. The message names the save it belongs to and has a
  **jump to card** link — Discord always drops these ephemeral messages at the bottom of
  the channel (they can't be pinned under the card), so with several saves queued, check
  the header/link to stay oriented.
- **📁 Change Path** — prepopulated with the current path; tweak, don't retype. Whatever
  you enter is normalized to ALL CAPS + forward slashes automatically.
- **✏️ NL Edit** — plain English; costs one extra Claude call. One instruction can do
  several things ("move this to cooking/bbq and add a smoker tag"), and it can reference
  the note's **content**, not just its metadata ("tag every coffee type mentioned in the
  summary" works — the summary and takeaways are sent along). If it replies "cancelled",
  it includes the reason; if it replies "couldn't map that instruction", nothing changed —
  rephrase and try again (the edit session stays open).
- **🔍 Deep fact-check** — on-demand, web-searched; slow (1–3 min) on health/finance.
- **⚠️ Approve + Include Warning** — only appears when a fact-check/location dispute was
  found; writes the note with a `> [!warning]` callout.
- Approving is safe to double-click: the second click is a no-op (idempotency guard).
- **Cards survive bot restarts** — buttons on old approval cards keep working (unlike
  duplicate-notice buttons).

## Recovery cheat-sheet

| Symptom | Fix |
|---|---|
| Re-pasted URL does nothing / "duplicate" notice | That's dedup. Click **🔁 Re-save** on the notice, or `/forget` + re-paste. |
| Deleted a note in Obsidian, URL won't re-save | Same as above — the vault is not the authority, `processing_state.json` is. |
| `/forget` or `/tag` not in the command picker | Restart the bot; see "Slash commands" above. |
| Buttons on an old duplicate notice do nothing | Bot restarted since the notice — use `/forget` + re-paste. |
| Instagram/TikTok/Facebook extraction failing | Cookies expired — `#SAVES-alerts` warns beforehand. Re-export with "Get cookies.txt LOCALLY" into `cookies/`. |
| Transcripts missing | Whisper server not running on the workstation: `python scripts\whisper_server.py --model large-v3-turbo`. |
| A `.bak` file appeared next to a note | A re-save retired the old version (zero-delete: renamed, never deleted). Keep or clean up manually. |
| Note in the wrong folder after approval | Approve then fix in Obsidian this once — but next time use 📁 Change Path *before* approving, so the learned preference stays correct. |
