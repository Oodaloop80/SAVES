# SAVES — Deep Code Review (pre-Phase-4, DEV → PROD)

**Date:** 2026-07-04 · **Base commit:** `2d31153` · **Reviewer:** Claude (full manual read, no subagents)
**Scope:** every file in `src/` (all 40+ modules read line-by-line), `scripts/`, `docker/`, `config.yaml`, `requirements*.txt`.
**Method:** read-only review — no code was changed. Every finding below was verified against the
live source (file:line anchors), not inferred. Zero-delete policy re-verified:
`grep os.remove|os.unlink|shutil.rmtree|.unlink(` across `src/` + `scripts/` → **clean**.

---

## Verdict in one paragraph

The pipeline core is in genuinely good shape: atomic writes everywhere, the `_finalize`
idempotency guard is correctly race-safe (no `await` between the `is_done` check and
`mark_done`, so double-clicks can't interleave), restart-safe approval views, defensive
extractor parsing, and prompt caching is placed correctly. The real risk clusters exactly
where Phase 4 goes next: **the Docker deployment as shipped will not run** (two config/mount
mismatches), and there are crash/restart edges around the pending-approval lifecycle that
only bite once the app runs unattended on the NAS. Fix the two blockers before
`docker-compose up`; the High items are the first week of PROD hardening.

---

## 🔴 P0 — Deploy blockers (the NAS deploy fails as-shipped)

> **✅ Both blockers FIXED 2026-07-04** — and differently than first recommended here, to
> satisfy the portability requirement (DEV on workstation today, PROD on NAS, movable to any
> future host): `config.yaml` now holds canonical container paths (`/vault`, `/media`,
> `/app/state`); hosts map onto them via `docker/.env` (Docker) or `config.local.yaml`
> (bare metal). All three state JSONs (incl. `preferences.json`) live in one mounted state
> directory. TZ + Docker log rotation (part of finding 21) landed in the same change.
> Details: ARCHITECTURE.md §1b, HANDBOOK §6/§8.

### 1. Compose mounts don't match config paths — container can't see the vault ✅ FIXED
`docker/docker-compose.yml:11-12` mounts the vault at **`/vault`** and media at **`/media`**,
but the `config.yaml` mounted into the container (`:14`) points at
`/volume1/NAS/OBSIDIAN/Remote Vault` and `/volume1/NAS/MEDIA/SAVES` — paths that don't exist
inside the container. `validate_startup` (main.py:36) will fail fast at boot, so this is a
loud failure rather than silent data loss — but the deploy is dead on arrival.

**Fix (recommended):** mirror-mount so paths are identical inside and outside — config stays
untouched and `process_one.py` run on the NAS host would also agree:
```yaml
- "/volume1/NAS/OBSIDIAN/Remote Vault:/volume1/NAS/OBSIDIAN/Remote Vault:rw"
- "/volume1/NAS/MEDIA/SAVES:/volume1/NAS/MEDIA/SAVES:rw"
```
(Alternative: mount a container-specific config overriding `paths.*` to `/vault`/`/media` —
more moving parts, not worth it.)

### 2. Single-file bind mounts break atomic state saves (and `preferences.json` isn't mounted at all) ✅ FIXED
`docker-compose.yml:16-17` bind-mounts `processing_state.json` and `pending_approvals.json`
as **single files**. Two independent failures:

- **`os.replace` onto a bind-mounted file fails with EBUSY** — the mount pins the inode, and
  every state save (`queue_manager.py:33`, `approval.py` `_save`) does
  `tempfile.mkstemp` + `os.replace()`. The **first** `mark_pending` inside Docker crashes the
  pipeline.
- **Docker directory footgun** — if either host file doesn't exist at `up` time, Docker
  creates a *directory* with that name, and `_load` then fails on every start.

Also: `preferences.json` (`preferences.file`, config.yaml:121) has **no mount at all** — the
learned folder routing would be written to the container's ephemeral FS and silently lost on
every rebuild.

**Fix:** mount one state *directory* and point all three files into it:
```yaml
- ../state:/app/state:rw
```
```yaml
paths:
  state_file: "state/processing_state.json"
  pending_approvals_file: "state/pending_approvals.json"
preferences:
  file: "state/preferences.json"
```
`os.replace` within a mounted directory is fine — only the file-as-mountpoint case breaks.

---

## 🟠 High — fix before or immediately after go-live

### 3. Crash between `mark_pending` and the Discord card strands the URL forever
`processor` calls `state.mark_pending(url)` (queue_manager.py:47) at the start of the
pipeline; the approval record is only persisted at the end (`store.add`). A crash/restart
anywhere in between (extract, OCR, analysis, Discord send — minutes of wall time) leaves the
URL with `status: "pending"`, which `is_processed()` (queue_manager.py:37-39) treats as
processed → never re-enqueued, no approval card ever exists, and the inbox line sits there
skipped forever.

**Fix:** startup reconciliation in `main()` — any state entry with `status == "pending"`
whose URL has no matching record in `pending_approvals.json` is a crash orphan: reset its
status (e.g. `"orphaned_requeue"` → treated as unprocessed) so the initial `scan_inbox()`
re-enqueues it, and log/alert the recovery.

### 4. `_finalize` is unguarded — a vault write failure hangs the interaction silently
`bot.py:356-428` has no try/except after the button handler defers. The most likely PROD
failure — NAS volume unmounted / permission error on `write_note` (bot.py:402) — throws into
discord.py's internals: the user sees the button spin forever, nothing lands in
`#SAVES-alerts`, and the pending item's fate is unclear.

**Fix:** wrap the body from `format_note` onward; on exception →
`interaction.edit_original_response(content="❌ Save failed: … (item still pending, click
Approve to retry)")` + `send_alert(...)`. Because `mark_done` only happens after a successful
write, the item stays pending and Approve-again is a clean retry — the plumbing for safe
retry already exists, it just needs the error surfaced.

### 5. Watcher never fires on the Windows dev machine (path-separator mismatch)
`watcher.py:23`: `event.src_path.endswith(self._inbox_path.lstrip("/"))`. On Linux/NAS this
works. On Windows, config uses forward slashes (`N:/NAS/OBSIDIAN/...`) while watchdog
delivers backslash paths (`N:\NAS\OBSIDIAN\...`) → `endswith` never matches → the watcher
silently never triggers during dev. Also `_debounce_seconds` is hardcoded `3`
(watcher.py:18) while `watcher.debounce_seconds` exists in config (config.yaml:16) — unwired.

**Fix:** compare normalized paths
(`os.path.normcase(os.path.normpath(event.src_path)).endswith(...)` on a normalized inbox
path), or simply match on `os.path.basename`; wire the config key while in there. For PROD
belt-and-braces, consider a config flag to swap in watchdog's `PollingObserver` (SMB/remote-FS
event delivery can be flaky; note the startup `scan_inbox()` already provides restart-time
catch-up).

### 6. `remove_url_from_inbox` substring match can delete the wrong line(s)
`file_io.py:16`: `filtered = [line for line in lines if url not in line]`. A URL that is a
prefix of another inbox URL (e.g. `…/p/ABC` vs `…/p/ABC123`, or a bare domain vs a deep link)
removes **both** lines — silent loss of a queued save. (This was ROADMAP review finding #3,
"Noted" — upgrading it: with the duplicate-notice path now also calling this at startup,
exposure went up.)

**Fix:** per-line comparison — `extract_urls(line)` → `normalize_url` → compare equality with
the normalized target, instead of raw substring.

### 7. `build_user_prompt` leaks the entire uncapped article into the prompt as "Metadata"
`prompts.py:351-360`: the metadata dump skips a list of known-big keys, but
**`article_markdown` is not in `skip_keys`** — and for every generic-web save,
`content.metadata["article_markdown"]` holds the complete article markdown (often 20–100K
chars). It gets emitted as `  article_markdown: <everything>` — duplicating `body_text`
(which is carefully capped at 8000 chars two lines below) at full length. Every web-article
save is paying multiples of its intended prompt cost, and a huge page could approach context
limits.

**Fix:** add `article_markdown` to `skip_keys` (prompts.py:351). Consider a belt-and-braces
`str(v)[:500]` cap on any metadata value so a future big key can't do this again.

### 8. Facebook: any external link in a video post reroutes the whole post — video never archived — ✅ FIXED (2026-07-04)
`facebook.py` (embedded-article detection): finding an external URL in the post
description hands the entire save to `GenericExtractor`. A *video* post whose caption merely
links a source article gets archived as a web article — the video (the thing being saved) is
dropped.

**Fix:** only reroute when it's genuinely a link-share post — e.g. yt-dlp found no video
formats — otherwise archive the video and record the external link in metadata.
> **Fixed:** added a `has_video = bool(info.get("duration") or info.get("formats"))` gate. The
> reroute to `GenericExtractor` now fires only when `article_url and not has_video`. A video
> post with an article link in its caption is archived as the video, with the link preserved
> in metadata as `related_article_url` (not `embedded_article_url`, so `extract()` won't
> reroute). Verified by test across all three scenarios.

---

## 🟡 Medium

### 9. Reddit short-link resolution blocks the event loop — ✅ FIXED (2026-07-04)
`reddit.py:81` calls `resolve_reddit_short_url(url)` (a synchronous `requests` call,
url_parser.py:44) before handing off to `to_thread` — a slow/unreachable redirect stalls the
entire loop (Discord heartbeats included) for up to the request timeout. **Fix:** move it
inside `_extract_sync`.
> **Fixed:** `resolve_reddit_short_url(url)` moved from `extract()` into `_extract_sync()`, so
> it now runs in the `to_thread` worker, off the event loop. Verified by test.

### 10. Reddit: deleted/removed post → IndexError — ✅ FIXED (2026-07-04)
`reddit.py`: `data[0]["data"]["children"][0]["data"]` — `children` is `[]` for
removed/deleted posts → unhandled `IndexError` (marked failed with a confusing reason).
**Fix:** guard and raise a descriptive `ValueError("post deleted/removed")`.
> **Fixed:** guarded the empty `children` list before indexing; raises
> `ValueError("Reddit post has no content — likely deleted or removed: <url>")`. "removed" in
> the message routes it to the processor's permanent-failure path (logged to `_FAILED`, no
> pointless auth-retry / re-queue). Verified by test; normal-post extraction unaffected.

### 11. Remote transcription ignores `max_duration_minutes` — ✅ FIXED (2026-07-04)
The duration cap is enforced only in `_transcribe_local` (transcriber.py:87-100).
`_transcribe_remote` (transcriber.py:51-81) POSTs any file: an oversized video hits the 300s
timeout, retries ×3 with 30/60s backoff ≈ **up to ~17 minutes of serial-queue stall**, and
the transcript is still lost. **Fix:** run the same ffprobe duration check before the POST.
> **Fixed:** extracted `_exceeds_duration_cap()` and moved the check up into `transcribe()`
> (before the mode dispatch), so it now gates **both** remote and local paths. The inline
> check in `_transcribe_local` was removed (no longer needed). Fails open on a probe error.
> Verified by test — an oversized file returns `None` with no remote POST attempted.

### 12. One malformed record wipes all pending approvals on load
`approval.py:31` `_load` wraps the whole parse loop in one try/except — a single legacy/
corrupt item (e.g. after a dataclass field is added/renamed) silently drops **every** pending
approval. **Fix:** per-item try/except; construct with
`{k: v for k, v in item.items() if k in field_names}` so old records tolerate schema drift.

### 13. Startup duplicate notices are dropped (sent before the bot connects) — but the inbox line is still removed
`main.py:74` awaits `scan_inbox()` before `bot.start()` (`main.py:82`). Any duplicates found
at startup call `send_duplicate_notice` on a not-yet-connected bot → channel lookup fails →
notice lost; the line is then removed from the inbox (`main.py:64`) with no ping.
**Fix:** run the initial scan from `on_ready` (or queue notices until ready).

### 14. `write_note` doesn't sandbox `folder_path` to the vault
The AI (or an NL edit / Change-Path modal typo) can return an absolute path or `../…`;
`os.path.join(vault_root, folder_path)` then escapes the vault. Not an attack — it's your
own bot — but a hallucinated `/SAVES/...` (leading slash) writes to the container root
silently. **Fix:** `os.path.realpath` the join and require it starts with
`realpath(vault_root)`; strip leading slashes from `folder_path`.

### 15. NL-edit path has no error handling
`bot.py:319` `_handle_nl_edit` → `nl_edit()` (a live Claude call) unguarded: an API error
mid-session leaves the NL-edit session open with no reply — the bot appears to ignore the
user. **Fix:** try/except → reply with the error + keep/clear session state deliberately.

### 16. YouTube/Facebook thin-extraction fallback produces junk notes instead of failing
`youtube.py:50` (and the same pattern in facebook.py): when yt-dlp returns nothing usable,
the extractor fabricates `ExtractedContent(title=url, …)` instead of raising. The pipeline
then spends OCR/analysis tokens on an empty post and sends a junk approval card. This is
exactly the YouTube bot-check failure mode (yt-dlp gets an interstitial page). **Fix:** raise
so it lands as `mark_failed` + `#SAVES-alerts` with a clear reason.

### 17. `msg.content[0].text` can crash on non-text first blocks
`claude_client.py:105` and `verifier.py:49`. If the model returns a leading non-text block
(thinking block with `effort` set, or an empty `content` on a refusal stop), this raises
`IndexError`/`AttributeError` and the save fails with a stack trace instead of a clean retry.
**Fix:** `next((b.text for b in msg.content if b.type == "text"), "")` + explicit handling of
empty content.

### 18. Downloader "newest file" fallback can return a non-media file
`downloader.py:176`: when yt-dlp's expected output name isn't found, the fallback picks the
newest file in the save dir — which can be `.part`, `.info.json`, or a thumbnail. That path
then flows to the transcriber (guarded by extension — OK) and into the note embed (not
guarded). **Fix:** filter the fallback to media extensions and exclude `.part`/`.ytdl`.

### 19. Recipe/fact-check sections can be injected mid-article — ✅ FIXED (2026-07-04)
`formatter.py:142-148`: inserts go before the **first** `\n---\n` in the rendered body — but
`web_article` bodies embed the article markdown, which frequently contains its own `---`
(hr). Inserts (recipe, image-text, fact-check) then land mid-article instead of before the
Metadata section. **Fix:** `body.rsplit(sep, 1)` — the renderers' Metadata separator is
always the last one.
> **Fixed:** changed `body.split(sep, 1)` → `body.rsplit(sep, 1)`. Confirmed safe: `body` is
> only the renderer output (frontmatter is a separate `parts` element), and every renderer
> ends `…\n---\n<Sources & Metadata callout>`, so the last `\n---\n` is always the Metadata
> separator. Verified by test (inserts land above Metadata; article HRs preserved).

### 20. Docker image bloat: full torch/whisper stack + two Chromiums
`docker/Dockerfile` installs `requirements-whisper.txt` (faster-whisper → ctranslate2/torch —
GBs) into the NAS image although `transcription.mode: "remote"`; it also installs apt
`chromium` + `chromium-driver` **and** `playwright install chromium --with-deps` (only
Playwright's is used). On a NAS, image size = build time + RAM. **Fix:** drop the whisper
requirements from the image (keep local mode possible via an optional build arg), remove the
apt chromium pair. Also `flask` sits in the main `requirements.txt` but is only used by the
workstation's `whisper_server.py` — move it to `requirements-whisper.txt`.

### 21. Compose runtime hygiene: TZ, log rotation, healthcheck
- No `TZ` env → container runs UTC → `saved_date` rolls to the next day for evening saves.
  Add `environment: [ "TZ=America/New_York" ]`.
- No Docker log limits and `logs/*.log` are append-forever (by design — zero-delete). Add
  `logging: { driver: json-file, options: { max-size: "10m", max-file: "3" } }`; that's
  Docker-level, outside the app's zero-delete constraint. App-log rotation is an owner
  decision (rotation deletes) — flagging, not prescribing.
- No `healthcheck:`. Even a trivial `python -c "import src.config"`-style liveness check
  makes `restart: unless-stopped` meaningfully better.
- Container `/tmp` accumulates scene frames/montages/resized images (left in place per
  zero-delete). Mount `tmpfs: [/tmp]` — nothing is ever *deleted* by the app; the tmpfs
  simply doesn't persist, which keeps the policy intact and the disk flat.

---

## 🟢 Low / hygiene

22. **`lstrip("www.")` is a character-set strip, not a prefix strip** — `wired.com` →
    `ired.com`, `mmm.example` → mangled. Six sites: `url_parser.py:30` (platform detection —
    could misroute a domain starting with w/·), `generic.py:425`, `profile_recipe.py:147`,
    `:259`, `:277`, `formatter.py:379`, `:1059`. Use `.removeprefix("www.")` (3.11 ✓).
    Note `formatter.py:1131` already does it correctly — copy that.
23. **`move_note` is dead code with a latent clobber** (`file_manager.py:100`) — never called
    anywhere; its same-volume `os.rename` would overwrite an existing destination. Either
    delete it or fix the collision handling before wiring it up (ROADMAP finding #4).
24. **`send_log` fails silently** when the channel is missing (`notifications.py:124`) while
    `send_alert` warns — make them consistent (warn once).
25. **`QueueManager._queued` never shrinks** (queue_manager.py:70,111): a URL that fails
    transiently can't be re-enqueued until restart even after the inbox line is re-added.
    Deliberate-looking, but worth a `discard()` on failure or a documented note.
26. **Instagram `_gallery_dl_metadata` can run twice** per save (fallback + numeric-handle
    replacement) — redundant subprocess/network; cache the first result.
27. **`_direct_download` loads the whole file into RAM, no size cap, bare `except`**
    (`downloader.py:192`) — stream to disk with `iter_content`, honor
    `media.max_video_size_mb`, log the failure.
28. **Only the first audio candidate is transcribed** (transcriber usage in processor) —
    multi-video posts lose the other transcripts. Rare; note-only.
29. **`verifier.py` parses with strict `json.loads`** (no `_loads_lenient`) → a fenced
    ```` ```json ```` response silently drops the travel check; it also builds its own client
    without `ai.max_retries`. Reuse the lenient parser + shared client config.
30. **`process_one.py` drift vs processor:** it skips vision for *all* YouTube (processor has
    a Shorts exception) and passes no preferences hint — CLI results can differ from live
    pipeline results. Align when convenient.
31. **`watcher` never re-arms** if the inbox dir is missing at startup (watcher.py:50-57
    logs and gives up until restart). PROD nicety: retry schedule/rearm every few minutes.
32. **`main.py:28` `handlers[2]`** — magic index for the error-log handler; breaks silently
    if the handler list changes. Name the handler instead.
33. **Unpinned requirements** (`>=`) — for PROD reproducibility generate a lock
    (`pip freeze`) at deploy time or pin majors; yt-dlp especially moves fast (though fast-
    moving is also why you *want* it fresh — pin everything else, float yt-dlp consciously).
34. **Frontmatter `type: SAVES.app` vs docs** — CLAUDE.md documents `type: save`. Pick one
    (affects Obsidian queries/dataview); update the other.

---

## Config keys defined but not wired (decide: wire or delete from config)

| Key | Status |
|---|---|
| `ai.max_content_chars: 20000` | **Unused** — prompts hardcode `[:8000]`/`[:12000]` caps (prompts.py:363,407,414). HANDBOOK §8 documents it as active → doc drift. Either wire it into `build_user_prompt` or remove. |
| `watcher.debounce_seconds` | Unused — hardcoded 3 (watcher.py:18). Wire (see Finding 5). |
| `processing.concurrent_downloads` | Unused (pipeline is serial by design — remove or comment as reserved). |
| `media.download_video` / `media.download_images` (global) | Unused — only `platforms.youtube.download_video` is honored. |
| `notes.tags_min` / `tags_max` | Unused — tag count comes from the prompt text. |
| `transcription.skip_if_captions_available` | Unused — YouTube caption preference is hardcoded in its extractor. |

---

## Verified solid (things checked and found correct)

- **Zero-delete policy:** grep across `src/` + `scripts/` → no `os.remove`/`os.unlink`/
  `shutil.rmtree`/`.unlink(`. All writes are `tempfile.mkstemp` + `os.replace`.
- **`_finalize` idempotency is genuinely race-safe:** between the `is_done` guard
  (bot.py:369) and `mark_done` (bot.py:413) there is no `await` — double-clicks and
  Approve+Warning cannot interleave on the single event loop. No fix needed.
- **Dedup normalization** (queue_manager.py:98) correctly matches `ProcessingState`'s key
  space; `_reported_duplicates` prevents double Discord pings between watcher refires.
- **Single event loop invariant holds:** watcher thread → `call_soon_threadsafe` only
  (watcher.py:32); no nested `asyncio.run` anywhere.
- **`units.py`** — conservative, idempotent, well-commented; no issues.
- **`recipe_data.py`** — defensive JSON-LD/WPRM parsing throughout; no issues.
- **Prompt caching** placement (system prompt + web-search first-message tail) is correct.
- **Extraction timeout + non-fatal profile-recipe following** behave as documented.

---

## Suggested fix order

1. **Before `docker-compose up`:** Findings 1, 2 (blockers) + 21's TZ line (one compose edit).
2. **Same sitting (small, high-value):** 3, 4, 5, 6, 7 — all are <30-line changes.
3. **First PROD week:** 8–19 as they get exercised by live traffic.
4. **Background hygiene:** 20–34 + config-key cleanup, opportunistically.
