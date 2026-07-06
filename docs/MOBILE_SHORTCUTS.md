# Saving from your phone (iOS + Android)

Both phones use the **same mechanism**: a share-sheet action appends the shared URL to the
inbox file **in the local Obsidian vault**, then **Obsidian Sync** carries it to the NAS. You
never touch the NAS directly — no SMB, no VPN, no Tailscale, works on cellular.

## The round trip (why this works off-network)

```
 Phone: Share → shortcut appends URL to  0 - INBOX/SAVES.md  (local vault copy)
    │
    ▼  Obsidian Sync (cloud)
 NAS vault:  0 - INBOX/SAVES.md  gains the line   ← a running Obsidian client bridges Sync→NAS
    │
    ▼  SAVES container (watchdog, 3 s)
 Pipeline runs → Discord card → you approve → note written to the NAS vault,
    URL removed from the inbox
    │
    ▼  Obsidian Sync (cloud)
 Phone:  the note appears, the inbox line disappears
```

> **One dependency to keep in mind:** the SAVES container watches the **NAS** copy of the file.
> That copy only updates when an Obsidian Sync client that has the NAS vault open applies the
> change (your desktop Obsidian, whichever client bridges Sync ↔ the NAS folder). If that
> bridging client is closed, phone saves queue in the cloud and only flow once it's back. This
> is your existing sync setup — nothing new to build, just don't let the bridge sit off for days.
>
> **Append one URL at a time and let it round-trip.** Both the phone (append) and SAVES (remove
> the line on approval) edit `SAVES.md` through Sync. One-at-a-time avoids Obsidian "sync
> conflict" copies. Firing several in a burst is usually fine — Sync merges by line — but if you
> ever see a `SAVES (conflict …).md`, that's why.

---

## One-time prerequisites (both platforms)

1. **Obsidian mobile** installed, signed into **Obsidian Sync**, with your vault synced locally.
2. **Advanced URI** community plugin enabled **in that vault**:
   - Easiest: enable it once on **desktop** (Settings → Community plugins → turn off Restricted
     mode → Browse → *Advanced URI* → Install → Enable). If "Sync plugins" is on in your Obsidian
     Sync settings, it appears on the phone automatically. Otherwise install it on the phone the
     same way (Settings → Community plugins).
3. **Know two values** (you'll paste them into the shortcut):
   - **Vault name** — exactly as Obsidian shows it in the vault switcher (e.g. `Remote Vault`).
   - **Inbox path** — `0 - INBOX/SAVES.md` (already the case for this system).

The shortcut fires this URL (the only dynamic part is the shared link):

```
obsidian://advanced-uri?vault=<VAULT>&filepath=0%20-%20INBOX%2FSAVES.md&mode=append&data=%0A<URL>
```

- `%20` = space, `%2F` = `/`, `%0A` = newline (puts each URL on its own line). If your **vault
  name has a space**, encode it too (`Remote%20Vault`).
- `mode=append` adds to the end of the file; Advanced URI creates the file if it's missing.
- `<URL>` **must be URL-encoded** so a link with `?`/`&`/`#` query params doesn't break the
  `advanced-uri` query string. Both recipes below do this.

Opening the URI briefly foregrounds Obsidian while it writes, then you can switch back. That's
expected — there's no background write into a Sync'd vault on either OS.

---

## iOS — Shortcuts (Share Sheet)

Create one shortcut, then it lives in the Share Sheet for Safari, YouTube, Instagram, etc.

1. **Shortcuts app → + → rename** it e.g. "Save to SAVES".
2. **Shortcut settings (ⓘ) → Show in Share Sheet ON.** Set **Share Sheet Types → URLs** (add
   Text too if you want to share plain-text links).
3. Add these actions in order:
   1. **Receive** *URLs and Text* from Share Sheet (top of the editor) — if there's no input,
      set "otherwise **Ask For** URL".
   2. **URL Encode** → input = **Shortcut Input**. (Text category → "URL Encode".)
   3. **Text** action, paste exactly (swap in your vault name; keep the `%0A`):
      ```
      obsidian://advanced-uri?vault=Remote%20Vault&filepath=0%20-%20INBOX%2FSAVES.md&mode=append&data=%0A
      ```
      then, with the cursor at the very end, insert the **URL Encoded** variable from step ii.
   4. **Open URLs** → input = the **Text** from step iii.
4. **Test:** open a link in Safari → Share → "Save to SAVES". Obsidian flips open for a moment;
   the URL is now the last line of `0 - INBOX/SAVES.md`. Switch back to your app.

---

## Android — Tasker (reliable) or MacroDroid (friendlier)

Android has no built-in Shortcuts app, so use a small automation app to catch the share and open
the same `obsidian://` URI.

### Option A — Tasker

1. **Profile → Event → Plugin/Intent… →** use **Event: Sharing** (or an *Intent Received* on
   `android.intent.action.SEND`, MIME `text/*`). The shared URL arrives as **`%astext`**.
2. **Task → Add action:**
   - **Variables → Variable Convert:** Name `%astext`, Function **URL Encode** → gives you the
     safe URL in `%astext`.
   - **Net → Browse URL**, URL:
     ```
     obsidian://advanced-uri?vault=Remote%20Vault&filepath=0%20-%20INBOX%2FSAVES.md&mode=append&data=%0A%astext
     ```
3. Now "Tasker" appears as a share target. Share a link → pick Tasker → it appends + Obsidian
   opens briefly.

### Option B — MacroDroid (no scripting)

1. **New Macro → Trigger:** *Sharing* (registers MacroDroid in the share sheet). The shared text
   lands in the built-in variable for the trigger.
2. **Action:** *Open Website/URL* with:
   ```
   obsidian://advanced-uri?vault=Remote%20Vault&filepath=0%20-%20INBOX%2FSAVES.md&mode=append&data=%0A{shared_url}
   ```
   Wrap the shared-text magic-text in MacroDroid's **URL-encode** formatter if the app offers it
   (recommended so query-string links survive); most social links share clean and work as-is.
3. Share a link → **MacroDroid** → it opens the URI and appends.

---

## Verifying end to end

1. Make sure the **SAVES container is running** on the NAS and the **bridging Obsidian client**
   (the one keeping the NAS vault synced) is up.
2. Share a link from the phone. Within a few seconds of it reaching the NAS file you get a
   **Discord approval card** in **#SAVES-approvals**.
3. Approve → the note is written and the inbox line is removed on the NAS.
4. Obsidian Sync brings the **note** and the **cleared inbox** back to the phone.

If a save never produces a card: confirm the line actually reached the **NAS** copy of
`0 - INBOX/SAVES.md` (open it in the bridging client) — if it's only on the phone, the Sync
bridge is off (see the dependency note above).
