"""Regression checks for TikTok caption line-break restoration.

TikTok's metadata `description` (via yt-dlp) flattens the author's newlines to runs of 2+ spaces,
so a recipe caption arrives as one wall of text. `restore_caption_linebreaks` turns those runs
back into line breaks so the caption renders with its original per-line structure. Pure logic —
no network / yt-dlp.

Run: python scripts/test_tiktok_caption.py
Exits non-zero on the first failed assertion.
"""
import os
import sys

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.extractors.base import ExtractedContent  # noqa: E402
from src.extractors.tiktok import restore_caption_linebreaks  # noqa: E402
from src.notes.formatter import format_note  # noqa: E402

_checks = 0


def check(cond: bool, msg: str) -> None:
    global _checks
    _checks += 1
    if not cond:
        print(f"FAIL: {msg}")
        sys.exit(1)
    print(f"  ok: {msg}")


# Mirrors what yt-dlp actually returns for the seafood-stew video: single spaces inside a line,
# 3-space runs where the author pressed Enter. Two colon-header spots (`For the Creamy Sauce:`)
# were collapsed to a single space by TikTok and are expected to stay glued.
FLAT = (
    "creamy seafood stew 🦐🦞 INGREDIENTS: For the Seafood: 300 g shrimp, peeled and deveined"
    "   300 g lobster or firm white fish chunks"
    "   2 tablespoons olive oil"
    "   For the Creamy Sauce: 2 tablespoons butter"
    "   1 cup heavy cream"
    "   #seafoodstew #creamyrecipes"
)


def test_runs_become_line_breaks():
    out = restore_caption_linebreaks(FLAT)
    lines = out.split("\n")
    check(len(lines) == 6, f"6 lines restored from the 3-space runs (got {len(lines)})")
    check(lines[1] == "300 g lobster or firm white fish chunks", "second ingredient on its own line")
    check(lines[4] == "1 cup heavy cream", "later ingredient line intact")
    check(lines[5] == "#seafoodstew #creamyrecipes", "hashtag line preserved")


def test_within_line_single_spaces_preserved():
    out = restore_caption_linebreaks(FLAT)
    check("300 g shrimp, peeled and deveined" in out, "single spaces inside a line are NOT broken")
    check("2 tablespoons olive oil" in out, "normal word gaps stay word gaps")
    # A break TikTok collapsed to one space is indistinguishable from a word gap -> stays glued.
    check("For the Creamy Sauce: 2 tablespoons butter" in out,
          "single-space colon-header glue is intentionally left alone")


def test_idempotent_and_noops():
    once = restore_caption_linebreaks(FLAT)
    check(restore_caption_linebreaks(once) == once, "idempotent: restoring twice changes nothing")
    already = "line one\nline two\nline three"
    check(restore_caption_linebreaks(already) == already,
          "text that already has newlines is left untouched (nothing was flattened)")
    plain = "just a normal caption with single spaces only"
    check(restore_caption_linebreaks(plain) == plain, "no multi-space runs -> unchanged")
    check(restore_caption_linebreaks("") == "", "empty string -> empty string")
    check(restore_caption_linebreaks(None) == "", "None -> empty string")


def test_formatter_renders_caption_multiline():
    # Simulate the post-extractor state: body_text already carries the restored newlines.
    restored = restore_caption_linebreaks(FLAT)
    content = ExtractedContent(
        url="https://www.tiktok.com/@therecipecollector/video/7620630606224968990",
        platform="tiktok", title="Creamy Seafood Stew", author="therecipecollector",
        body_text=restored, metadata={},
    )
    ai = {"note_type": "tiktok_video", "title": "Creamy Seafood Stew", "summary": "s", "tags": ["x"]}
    note = format_note(ai, content, [], None, {"notes": {}})
    check("> 300 g lobster or firm white fish chunks" in note,
          "caption renders each restored line as its own quoted line")
    check("> 1 cup heavy cream" in note, "every ingredient line makes it into the Caption box")
    check(note.count("> ") >= 6, "Caption box is no longer a single wall-of-text line")


def main():
    print("TikTok caption line-break restoration checks")
    for fn in (
        test_runs_become_line_breaks,
        test_within_line_single_spaces_preserved,
        test_idempotent_and_noops,
        test_formatter_renders_caption_multiline,
    ):
        print(f"\n[{fn.__name__}]")
        fn()
    print(f"\n{_checks} checks passed.")


if __name__ == "__main__":
    main()
