# `/save <url>` — process, QA, and commit a single save

Process a single URL through the full SAVES pipeline, review the output, and optionally commit
the resulting note to the repo.

## How it works

1. **Extract & pipeline**: run `python scripts/process_one.py "<url>" --dry-run` to run the full
   extraction → download → transcribe → AI → note-format pipeline. Print the generated note.

2. **Auto-QA**: inspect the note for correctness:
   - Is media embedded (images/video/audio present and properly linked)?
   - Is transcript present when expected (for video/audio/reel saves)?
   - Is recipe section present if the content is food-related?
   - Are there any "> [!warning] Media unavailable" or other error callouts?
   - Is the title and folder_path sensible?

3. **Report**: print a tight summary: either "✅ PASS" or "⚠️ ISSUES:" with a bullet list.

4. **On PASS**: 
   - Ask for confirmation to write the note for real (re-run without `--dry-run`).
   - Commit: `git add -A && git commit -m "Archive: <note title from the frontmatter>"`.
   - Push: `git push origin main`.

5. **On ISSUES**: 
   - List each issue (e.g., "Media failed to download: Instagram cookies expired").
   - Ask whether to: (a) fix the issue and re-run, or (b) skip this URL for now.
   - If (a): suggest the fix (e.g., "refresh cookies, then try again"), but don't auto-fix.
   - If (b): don't commit or push.

## Error handling

- If `process_one.py` itself errors (import error, network timeout, malformed URL), report the
  error and stop. Don't speculate on fixes.
- If the note generation succeeds but QA finds issues, surface them clearly and ask the user
  to decide.

## Example flow

```
/save https://www.reddit.com/r/smoking/comments/abc123/...

→ [runs process_one.py --dry-run, prints note]

→ ✅ PASS
   - Media: 3 images embedded
   - Transcript: none (text post, expected)
   - Recipe: none (not food, correct)
   - No warnings

Commit? (y/n): y

→ [re-runs without --dry-run; writes to vault]
→ git add -A
→ git commit -m "Archive: Smoked Brisket Stall Guide"
→ git push origin main

Done. Note written to SAVES/COOKING/BBQ/smoked-brisket-stall-guide.md
```

## Notes

- This command lives in `.claude/commands/save.md` and is invoked via `/save <url>` from the CLI.
- The user may provide a URL with special characters; properly quote it in shell commands.
- The note title comes from the AI-generated frontmatter `title:` field in the note markdown.
- If the user says "skip" or "no", don't commit or push — just return to the prompt.
