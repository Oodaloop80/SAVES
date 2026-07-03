"""Regression checks for TikTok caption fidelity.

Two mechanisms keep a TikTok caption looking like the real post:

1. `caption_from_contents` reconstructs the creator's *verbatim* caption from the video page's
   `itemStruct.contents[]` — one array element per rendered line, with empty `desc` entries for
   the blank lines between sections. This is the literal caption the app shows in its expanded
   "...more" overlay (section headers on their own line, bullet lists, blank-line paragraph
   breaks) and is preferred over yt-dlp's `description`, which TikTok's web layer serves with
   every hard line break stripped. Pure function — fed a fixture array here, no network.
2. `restore_caption_linebreaks` is the fallback: when the rehydration blob is unavailable and we
   fall back to yt-dlp's `description`, it turns the flattening artifacts (runs of 2+ spaces,
   no-break spaces, dash-bullet lists) back into line breaks so the caption isn't one wall of
   text.

Pure logic — no live network / yt-dlp.

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
from src.extractors.tiktok import (  # noqa: E402
    _item_id_from_url,
    caption_from_contents,
    restore_caption_linebreaks,
)
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


# --- fallback: no-break-space and dash-bullet flattening --------------------------------------

# The real yt-dlp description for the Beef Wellington video (@anasofiafehn/7446182438218337567):
# its TDK article was empty, so this fallback is the only path. There are NO runs of 2+ ordinary
# spaces here — TikTok flattened this author's breaks into no-break spaces (\xa0, at section
# boundaries) and dash bullets (" -item"), which the old 2+-space-only logic couldn't see.
WELLINGTON = (
    "BEEF WELLINGTON \U0001f52a\U0001f344‍\U0001f7eb First time cooking in my new "
    "kitchen!! Ingredients For the mushroom duxelles -3 tbsp olive oil -4 cups of assorted "
    "mushrooms (some great options: cremini, porcini, portobello, chanterelles, button)\xa0 "
    "-½ tsp salt -Freshly ground black pepper\xa0 -2 to 3 tablespoons fresh thyme\xa0 "
    "For the savory chive crêpes -2 eggs -1 cup flour -¼ tsp salt -1 cup whole milk "
    "-2 tablespoons chives (adjust to preference) -Butter (to grease pan) or non-stick cooking "
    "spray\xa0 For the rest of the Wellington fixings:\xa0 -Center-cut beef tenderloin\xa0 "
    "-Salt and black pepper -1 tbsp avocado oil (or other high smoke-point oil) -3 tbsp Dijon "
    "mustard -6 slices prosciutto\xa0 -1 to 2 sheets of puff pastry (thawed if frozen)\xa0 "
    "-3 egg yolks #cooking #recipe #beefwellington #holiday "
)


def test_nbsp_becomes_line_break():
    out = restore_caption_linebreaks("Section one\xa0 Section two\xa0-item three")
    lines = out.split("\n")
    check(lines == ["Section one", "Section two", "-item three"],
          f"no-break spaces (\\xa0) restore as line breaks (got {lines!r})")
    plain = "five\xa0grams of salt in one\xa0cup"  # nbsp used as a word joiner, still split
    check("\n" in restore_caption_linebreaks(plain), "any nbsp is treated as a break (fallback path)")


def test_dash_bullets_become_lines():
    out = restore_caption_linebreaks(WELLINGTON)
    lines = out.split("\n")
    check("-3 tbsp olive oil" in lines, "first dash-bullet ingredient on its own line")
    check("-½ tsp salt" in lines, "'-½ tsp salt' split from the previous ingredient")
    check("For the savory chive crêpes" in lines,
          "nbsp-delimited section header on its own line")
    check("For the rest of the Wellington fixings:" in lines, "second section header on its own line")
    check("-Center-cut beef tenderloin" in lines,
          "internal hyphen in 'Center-cut' is NOT split (no space before it)")
    check("-1 tbsp avocado oil (or other high smoke-point oil)" in lines,
          "'smoke-point' internal hyphen is NOT split")
    check(not any(" -" in ln for ln in lines),
          "no dash bullet is left glued mid-line after restoring")
    check(lines[-1] == "-3 egg yolks #cooking #recipe #beefwellington #holiday",
          "trailing hashtags stay with the final ingredient (no signal to split them)")
    check(len(lines) == 21, f"the flattened wall becomes 21 structured lines (got {len(lines)})")


def test_dash_bullets_below_threshold_left_alone():
    # Fewer than 3 " -word" markers => an incidental prose dash, not a list. Do not split.
    prose = "Loved this recipe -so quick and the crust -wow"
    check(restore_caption_linebreaks(prose) == prose,
          "1-2 dash markers in prose are left glued (not a bullet list)")


def test_new_signals_idempotent():
    once = restore_caption_linebreaks(WELLINGTON)
    check(restore_caption_linebreaks(once) == once,
          "restoring an already-restored caption changes nothing (has real newlines)")


# --- contents[] (verbatim, line-preserved caption) --------------------------------------------

# A faithful slice of the real ``itemStruct.contents`` for the Beef Wellington video
# (@anasofiafehn/7446182438218337567): one element per rendered line, empty ``desc`` = blank line,
# and a couple of lines carry the stray trailing space TikTok pads them with (``button) `` and
# the hashtag line). Reconstructing this must reproduce the app's caption exactly.
WELLINGTON_CONTENTS = [
    {"desc": "BEEF WELLINGTON \U0001f52a\U0001f344‍\U0001f7eb First time cooking in my new kitchen!!", "textExtra": []},
    {"desc": "", "textExtra": []},
    {"desc": "Ingredients", "textExtra": []},
    {"desc": "", "textExtra": []},
    {"desc": "For the mushroom duxelles", "textExtra": []},
    {"desc": "-3 tbsp olive oil", "textExtra": []},
    {"desc": "-4 cups of assorted mushrooms (cremini, porcini, portobello, button) ", "textExtra": []},
    {"desc": "-½ tsp salt", "textExtra": []},
    {"desc": "", "textExtra": []},
    {"desc": "For the savory chive crêpes", "textExtra": []},
    {"desc": "-2 eggs", "textExtra": []},
    {"desc": "-Butter (to grease pan) or non-stick cooking spray", "textExtra": []},
    {"desc": "", "textExtra": []},
    {"desc": "#cooking #recipe #beefwellington #holiday ", "textExtra": []},
]


def test_item_id_from_url():
    check(_item_id_from_url("https://www.tiktok.com/@x/video/7620630606224968990?q=1")
          == "7620630606224968990", "video id parsed from a full URL with query string")
    check(_item_id_from_url("https://www.tiktok.com/@x/photo/12345") == "12345",
          "photo id parsed")
    check(_item_id_from_url("https://www.tiktok.com/@x") is None, "no id -> None")
    check(_item_id_from_url("") is None, "empty url -> None")


def test_contents_reconstructs_exact_caption():
    cap = caption_from_contents(WELLINGTON_CONTENTS)
    lines = cap.split("\n")
    check(lines[0] == "BEEF WELLINGTON \U0001f52a\U0001f344‍\U0001f7eb First time cooking in my new kitchen!!",
          "title on its own line 1 (not glued to 'Ingredients')")
    check(lines[1] == "", "blank line after the title (empty contents element preserved)")
    check(lines[2] == "Ingredients", "'Ingredients' on its own line")
    check(lines[3] == "", "blank line after 'Ingredients'")
    check(lines[4] == "For the mushroom duxelles", "first section header on its own line")
    check("For the savory chive crêpes" in lines, "second section header present on its own line")
    check(lines[-1] == "#cooking #recipe #beefwellington #holiday",
          "hashtags on their own final line, trailing space trimmed")


def test_contents_trims_trailing_space_and_keeps_blanks():
    cap = caption_from_contents(WELLINGTON_CONTENTS)
    check("button) \n" not in cap and "button)\n" in cap,
          "stray trailing space on a bullet is trimmed")
    check("\n\n" in cap, "blank lines between sections are kept as empty lines")
    check(not cap.startswith("\n") and not cap.endswith("\n"),
          "no leading or trailing blank lines")
    # Each of the 4 blank lines between content lines survives round-trip.
    check(cap.count("\n\n") == 4, f"exactly 4 section breaks preserved (got {cap.count(chr(10)+chr(10))})")


def test_contents_guards_return_none():
    check(caption_from_contents(None) is None, "None -> None")
    check(caption_from_contents([]) is None, "empty list -> None")
    check(caption_from_contents("nope") is None, "non-list -> None")
    check(caption_from_contents([{"desc": "only one reasonably long single caption line here"}]) is None,
          "single element -> None (not a multi-line caption)")
    check(caption_from_contents([{"desc": ""}, {"desc": ""}]) is None, "all-blank -> None")
    check(caption_from_contents([{"desc": "short"}, {"desc": "x"}]) is None,
          "too-short reconstruction -> None")
    check(caption_from_contents([{"no_desc_key": 1}, {"desc": "the other line is long enough here"}]) is None,
          "element missing 'desc' is treated as blank (no crash), and lone real line -> None")


def test_contents_formatter_renders_blank_lines_in_caption():
    cap = caption_from_contents(WELLINGTON_CONTENTS)
    content = ExtractedContent(
        url="https://www.tiktok.com/@anasofiafehn/video/7446182438218337567",
        platform="tiktok", title="Beef Wellington", author="anasofiafehn",
        body_text=cap, metadata={},
    )
    ai = {"note_type": "tiktok_video", "title": "Beef Wellington", "summary": "s", "tags": ["x"]}
    note = format_note(ai, content, [], None, {"notes": {}})
    check("> Ingredients" in note, "'Ingredients' rendered on its own quoted line")
    check("> For the mushroom duxelles" in note, "section header on its own quoted line")
    check("> #cooking #recipe #beefwellington #holiday" in note, "hashtag line rendered")
    # A blank line inside the callout is a bare quoted line ("> ") between two content lines.
    check(">\n> Ingredients" in note or "> \n> Ingredients" in note,
          "blank line before 'Ingredients' preserved as an empty quoted line in the Caption box")


def main():
    print("TikTok caption fidelity checks (contents[] reconstruction + line-break fallback)")
    for fn in (
        test_runs_become_line_breaks,
        test_within_line_single_spaces_preserved,
        test_idempotent_and_noops,
        test_formatter_renders_caption_multiline,
        test_nbsp_becomes_line_break,
        test_dash_bullets_become_lines,
        test_dash_bullets_below_threshold_left_alone,
        test_new_signals_idempotent,
        test_item_id_from_url,
        test_contents_reconstructs_exact_caption,
        test_contents_trims_trailing_space_and_keeps_blanks,
        test_contents_guards_return_none,
        test_contents_formatter_renders_blank_lines_in_caption,
    ):
        print(f"\n[{fn.__name__}]")
        fn()
    print(f"\n{_checks} checks passed.")


if __name__ == "__main__":
    main()
