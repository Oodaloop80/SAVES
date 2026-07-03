"""Regression checks for the caption-area layout: English-Translation and Bio callouts expanded
by default, translation ABOVE the caption, transcript BELOW the caption. Pure logic — no network.

Run: python scripts/test_caption_layout.py
Exits non-zero on the first failed assertion.
"""
import os
import sys

# Emit UTF-8 so the emoji in note output don't crash on Windows' cp1252 console.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.extractors.base import ExtractedContent  # noqa: E402
from src.notes.formatter import format_note  # noqa: E402

CFG = {"notes": {}}
TRANSLATION = "> [!info]+ 🌐 English Translation"
CAPTION = "> [!quote] Caption"
TRANSCRIPT = "Full Transcript"
_checks = 0


def check(cond: bool, msg: str) -> None:
    global _checks
    _checks += 1
    if not cond:
        print(f"FAIL: {msg}")
        sys.exit(1)
    print(f"  ok: {msg}")


def _content(platform="instagram", body="La leyenda del reel", meta=None):
    return ExtractedContent(
        url=f"https://www.{platform}.com/reels/X/", platform=platform,
        title="T", author="a", body_text=body, metadata=meta or {},
    )


def test_reel_translation_above_caption_transcript_below():
    ai = {
        "note_type": "instagram_reel", "title": "T", "summary": "s", "tags": ["x"],
        "translation": "Hola mundo\nsegunda linea", "source_language": "Spanish",
    }
    note = format_note(ai, _content(), [], "This is the spoken transcript.", CFG)
    check(TRANSLATION in note, "translation callout renders expanded ('[!info]+')")
    check(note.count("🌐 English Translation") == 1, "translation appears exactly once (no top dup)")
    check(CAPTION in note and TRANSCRIPT in note, "caption and transcript both present")
    check(note.index(TRANSLATION) < note.index(CAPTION), "translation sits ABOVE the caption")
    check(note.index(CAPTION) < note.index(TRANSCRIPT), "transcript sits BELOW the caption")


def test_reel_english_has_no_translation_but_transcript_below_caption():
    ai = {"note_type": "instagram_reel", "title": "T", "summary": "s", "tags": ["x"]}
    note = format_note(ai, _content(body="just the caption"), [], "spoken words", CFG)
    check("🌐 English Translation" not in note, "English reel gets no translation box")
    check(note.index(CAPTION) < note.index(TRANSCRIPT),
          "transcript still moves below the caption even with no translation")


def test_tiktok_and_facebook_same_layout():
    for platform, nt in (("tiktok", "tiktok_video"), ("facebook", "facebook_video")):
        ai = {
            "note_type": nt, "title": "T", "summary": "s", "tags": ["x"],
            "translation": "traducción", "source_language": "Spanish",
        }
        note = format_note(ai, _content(platform=platform), [], "spoken", CFG)
        check(note.index(TRANSLATION) < note.index(CAPTION) < note.index(TRANSCRIPT),
              f"{nt}: translation < caption < transcript")


def test_non_caption_type_keeps_translation_at_top():
    # YouTube has no caption box; its renderer doesn't emit translation, so the top-level
    # fallback must still surface it (expanded) — proving the `not in body` guard's else path.
    ai = {
        "note_type": "youtube_video", "title": "T", "summary": "s", "tags": ["x"],
        "translation": "traducción del video", "source_language": "Spanish",
    }
    note = format_note(ai, _content(platform="youtube", body=""), [], "captions here", CFG)
    check(TRANSLATION in note, "youtube (no caption) still shows the translation, expanded")
    check(note.count("🌐 English Translation") == 1, "no duplicate translation for non-caption types")


def test_bio_callout_expanded():
    ai = {
        "note_type": "instagram_reel", "title": "T", "summary": "s", "tags": ["pasta"],
        "recipe_ingredients": ["2 heads garlic"], "recipe_instructions": ["Confit it"],
    }
    note = format_note(ai, _content(meta={
        "author_handle": "dr.vegan",
        "offsite_recipe_detected": True,
        "followed_recipe_url": "https://drveganblog.com/garlic-confit-pasta/",
    }), [], None, CFG)
    check("> [!success]+ 🔗 Recipe from Bio / Off-Site Link" in note,
          "Bio / Off-Site Link callout renders expanded ('[!success]+')")


def main():
    print("Caption-area layout checks")
    for fn in (
        test_reel_translation_above_caption_transcript_below,
        test_reel_english_has_no_translation_but_transcript_below_caption,
        test_tiktok_and_facebook_same_layout,
        test_non_caption_type_keeps_translation_at_top,
        test_bio_callout_expanded,
    ):
        print(f"\n[{fn.__name__}]")
        fn()
    print(f"\n{_checks} checks passed.")


if __name__ == "__main__":
    main()
