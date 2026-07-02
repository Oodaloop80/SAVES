"""Regression checks for the "Recipe from Bio / Off-Site Link" note section — pure logic,
no network/Discord/Claude.

Run: python scripts/test_bio_recipe_section.py
Exits non-zero on the first failed assertion.

Covers the four provenance states the formatter must render for a post whose recipe lives
behind a "link in bio" / "recipe on my profile" pointer:
  1. followed the link AND extracted a recipe   -> recipe shown under the bio heading
  2. followed a page but found no real recipe    -> honest "couldn't find a recipe" + source
  3. found the bio link but couldn't open it      -> "couldn't open it" + the shared link
  4. detected the pointer but resolved nothing     -> "couldn't locate or extract" message
Plus negatives: an ordinary post gets no bio section, and a caption-only recipe still uses
the plain "## Recipe" heading.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.extractors.base import ExtractedContent  # noqa: E402
from src.notes.formatter import format_note  # noqa: E402

HEADING = "## 🔗 Recipe from Bio / Off-Site Link"
CFG = {"notes": {}}
_checks = 0


def check(cond: bool, msg: str) -> None:
    global _checks
    _checks += 1
    if not cond:
        print(f"FAIL: {msg}")
        sys.exit(1)
    print(f"  ok: {msg}")


def _content(meta: dict) -> ExtractedContent:
    return ExtractedContent(
        url="https://www.instagram.com/reels/X/", platform="instagram",
        title="Garlic confit pasta", author="dr.vegan",
        body_text="Garlic confit pasta — FULL RECIPE ON MY BLOG (link in my bio)",
        metadata=meta,
    )


def test_followed_and_extracted():
    ai = {
        "note_type": "instagram_reel", "title": "Garlic confit pasta", "summary": "x",
        "tags": ["pasta"],
        "recipe_ingredients": ["2 heads garlic", "250 g spinach"],
        "recipe_instructions": ["Confit the garlic", "Toss with pasta"],
    }
    note = format_note(ai, _content({
        "author_handle": "dr.vegan",
        "offsite_recipe_detected": True,
        "followed_recipe_url": "https://drveganblog.com/garlic-confit-pasta/",
        "followed_recipe_markdown": "# Garlic Confit Pasta",
    }), [], None, CFG)
    check(HEADING in note, "bio heading present when a recipe was followed")
    check("drveganblog.com/garlic-confit-pasta" in note, "provenance banner cites the source URL")
    check("2 heads garlic" in note and "### Ingredients" in note,
          "extracted recipe is rendered inside the bio section")
    check("oz)" in note, "unit conversion still applied inside the bio section")
    check(note.count("## Recipe\n") == 0, "no duplicate generic '## Recipe' heading")
    check(note.count("### Ingredients") == 1, "ingredients appear exactly once")


def test_followed_but_no_recipe():
    ai = {"note_type": "instagram_reel", "title": "Pan pizza", "summary": "x", "tags": ["pizza"]}
    note = format_note(ai, _content({
        "offsite_recipe_detected": True,
        "followed_recipe_url": "https://www.thenotoriouspie.com/",
    }), [], None, CFG)
    check(HEADING in note, "bio heading present when the link had no recipe")
    check("could not find a structured recipe" in note, "states plainly that no recipe was found")
    check("thenotoriouspie.com" in note, "still links the page it followed")


def test_hint_only():
    ai = {"note_type": "instagram_reel", "title": "Pan pizza", "summary": "x", "tags": ["pizza"]}
    note = format_note(ai, _content({
        "offsite_recipe_detected": True,
        "followed_recipe_hint": "https://linktr.ee/somechef",
    }), [], None, CFG)
    check(HEADING in note, "bio heading present when only a hint link resolved")
    check("could not open it" in note, "says it could not open the link automatically")
    check("linktr.ee/somechef" in note, "shares the hint link so the reader can follow it")


def test_detected_but_nothing_resolved():
    ai = {"note_type": "instagram_reel", "title": "Pan pizza", "summary": "x", "tags": ["pizza"]}
    note = format_note(ai, _content({"offsite_recipe_detected": True}), [], None, CFG)
    check(HEADING in note, "bio heading present even on a total miss")
    check("could not locate or extract" in note,
          "explicitly says the recipe could not be extracted")


def test_ordinary_post_has_no_bio_section():
    ai = {"note_type": "instagram_reel", "title": "Sunset", "summary": "x", "tags": ["travel"]}
    note = format_note(ai, _content({"author_handle": "someone"}), [], None, CFG)
    check(HEADING not in note, "no bio section on a post with no off-site pointer")


def test_caption_recipe_uses_plain_heading():
    ai = {
        "note_type": "instagram_reel", "title": "Cookies", "summary": "x", "tags": ["dessert"],
        "recipe_ingredients": ["flour", "sugar"], "recipe_instructions": ["mix", "bake"],
    }
    note = format_note(ai, _content({"author_handle": "someone"}), [], None, CFG)
    check("## Recipe\n" in note, "an in-caption recipe still uses the plain '## Recipe' heading")
    check(HEADING not in note, "no bio section when the recipe was in the caption itself")


def main():
    print("Bio-link recipe section checks")
    for fn in (
        test_followed_and_extracted,
        test_followed_but_no_recipe,
        test_hint_only,
        test_detected_but_nothing_resolved,
        test_ordinary_post_has_no_bio_section,
        test_caption_recipe_uses_plain_heading,
    ):
        print(f"\n[{fn.__name__}]")
        fn()
    print(f"\n{_checks} checks passed.")


if __name__ == "__main__":
    main()
