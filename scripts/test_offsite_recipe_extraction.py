"""Regression checks for accurate off-site recipe extraction + full-page reproduction —
pure logic, no network / Claude / Discord.

Run: python scripts/test_offsite_recipe_extraction.py
Exits non-zero on the first failed assertion.

Covers the Phase 4 accuracy work: when a "recipe in bio" post is followed to a food blog we
now (1) parse the page's schema.org Recipe JSON-LD for EXACT ingredients/quantities/steps,
(2) deterministically backfill the note's recipe_* fields from it, (3) feed it to Claude as
the authoritative source, and (4) reproduce the source page (text + images + formatting) in
the note like a direct web-clipper paste.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.extractors.base import ExtractedContent  # noqa: E402
from src.notes.formatter import format_note  # noqa: E402
from src.utils.recipe_data import (  # noqa: E402
    apply_structured_recipe,
    extract_recipe_jsonld,
    format_recipe_data_for_prompt,
    iso8601_duration_to_human,
)

CFG = {"notes": {}}
HEADING = "## 🔗 Recipe from Bio / Off-Site Link"
_checks = 0

# A realistic WordPress-Recipe-Maker-style page: JSON-LD inside an @graph, HowToStep +
# HowToSection instructions, HTML entities and inline tags in the text, ISO-8601 durations,
# and a two-element recipeYield. This is exactly the shape drveganblog.com emits.
SAMPLE_HTML = """
<html><head>
<script type="application/ld+json">
{"@context":"https://schema.org","@graph":[
  {"@type":"WebSite","name":"Dr Vegan Blog"},
  {"@type":"Recipe","name":"Garlic Confit Pasta",
   "recipeYield":["4","4 servings"],
   "prepTime":"PT10M","cookTime":"PT45M","totalTime":"PT55M",
   "recipeIngredient":[
     "1 cup olive oil",
     "2 heads garlic, cloves peeled",
     "250 g spinach",
     "400 g spaghetti",
     "1 tsp chili flakes",
     "Salt to taste"],
   "recipeInstructions":[
     {"@type":"HowToStep","text":"Add the garlic cloves and olive oil to a small pan."},
     {"@type":"HowToStep","text":"Confit over low heat for 40&ndash;45 minutes until golden."},
     {"@type":"HowToSection","name":"Assembly","itemListElement":[
        {"@type":"HowToStep","text":"Cook the spaghetti until al dente."},
        {"@type":"HowToStep","text":"Toss pasta with <b>garlic confit</b> and spinach."}
     ]}
   ]}
]}
</script>
</head><body><p>story</p></body></html>
"""

EXPECTED_INGREDIENTS = [
    "1 cup olive oil",
    "2 heads garlic, cloves peeled",
    "250 g spinach",
    "400 g spaghetti",
    "1 tsp chili flakes",
    "Salt to taste",
]
EXPECTED_STEPS = [
    "Add the garlic cloves and olive oil to a small pan.",
    "Confit over low heat for 40–45 minutes until golden.",  # &ndash; -> –
    "Cook the spaghetti until al dente.",
    "Toss pasta with garlic confit and spinach.",  # <b> stripped
]


def check(cond: bool, msg: str) -> None:
    global _checks
    _checks += 1
    if not cond:
        print(f"FAIL: {msg}")
        sys.exit(1)
    print(f"  ok: {msg}")


def _content(meta: dict) -> ExtractedContent:
    return ExtractedContent(
        url="https://www.instagram.com/reels/DaQrpDtJAkA/", platform="instagram",
        title="Garlic confit pasta", author="dr.vegan",
        body_text="Garlic confit pasta — FULL RECIPE ON MY BLOG (link in bio)",
        metadata=meta,
    )


def test_jsonld_parse_exact():
    data = extract_recipe_jsonld(SAMPLE_HTML)
    check(data is not None, "schema.org Recipe JSON-LD is found inside an @graph")
    check(data["name"] == "Garlic Confit Pasta", "recipe name parsed")
    check(data["ingredients"] == EXPECTED_INGREDIENTS,
          "every ingredient parsed verbatim with exact quantities")
    check(data["instructions"] == EXPECTED_STEPS,
          "HowToStep + HowToSection steps flattened in order, entities/tags cleaned")
    check(data["yield"] == "4 servings", "recipeYield prefers the descriptive value")
    check(data["prep_time"] == "10 min" and data["cook_time"] == "45 min"
          and data["total_time"] == "55 min", "ISO-8601 times humanized")
    check(data["time_str"] == "Prep: 10 min · Cook: 45 min · Total: 55 min",
          "combined time string assembled")


def test_jsonld_absent():
    check(extract_recipe_jsonld("<html><body>no recipe here</body></html>") is None,
          "returns None when the page has no Recipe JSON-LD")
    check(extract_recipe_jsonld("") is None, "returns None on empty HTML")


def test_iso_durations():
    check(iso8601_duration_to_human("PT1H30M") == "1 hr 30 min", "PT1H30M -> 1 hr 30 min")
    check(iso8601_duration_to_human("PT45M") == "45 min", "PT45M -> 45 min")
    check(iso8601_duration_to_human("PT2H") == "2 hr", "PT2H -> 2 hr")
    check(iso8601_duration_to_human("PT30S") == "1 min", "sub-minute rounds up so it isn't lost")
    check(iso8601_duration_to_human("P0D") is None, "zero duration -> None")
    check(iso8601_duration_to_human("garbage") is None, "unparseable -> None")
    check(iso8601_duration_to_human(None) is None, "None input -> None")


def test_apply_override_english():
    data = extract_recipe_jsonld(SAMPLE_HTML)
    ai = {  # what a truncated Claude pass might return: wrong quantities, missing steps
        "note_type": "instagram_reel", "source_language": "English",
        "recipe_ingredients": ["garlic", "pasta"],
        "recipe_instructions": ["make it"],
    }
    ai = apply_structured_recipe(ai, _content({"followed_recipe_data": data}))
    check(ai["recipe_ingredients"] == EXPECTED_INGREDIENTS,
          "English source: ingredients replaced with the exact JSON-LD list")
    check(ai["recipe_instructions"] == EXPECTED_STEPS,
          "English source: all instruction steps replaced from JSON-LD")
    check(ai["recipe_servings"] == "4 servings", "servings backfilled when missing")
    check(ai["recipe_time"].startswith("Prep:"), "time backfilled when missing")


def test_apply_keeps_translation_for_non_english():
    data = extract_recipe_jsonld(SAMPLE_HTML)
    ai = {
        "note_type": "instagram_reel", "source_language": "Spanish",
        "recipe_ingredients": ["2 dientes de ajo", "250 g de espinacas"],
        "recipe_instructions": ["Confitar el ajo"],
        "recipe_servings": "",
    }
    ai = apply_structured_recipe(ai, _content({"followed_recipe_data": data}))
    check(ai["recipe_ingredients"] == ["2 dientes de ajo", "250 g de espinacas"],
          "non-English: Claude's translated ingredients are preserved (not overwritten)")
    check(ai["recipe_instructions"] == ["Confitar el ajo"],
          "non-English: translated instructions preserved")
    check(ai["recipe_servings"] == "4 servings",
          "non-English: neutral servings still backfilled from JSON-LD")


def test_apply_direct_recipe_data_key():
    data = extract_recipe_jsonld(SAMPLE_HTML)
    ai = {"note_type": "web_recipe", "recipe_ingredients": [], "recipe_instructions": []}
    ai = apply_structured_recipe(ai, _content({"recipe_data": data}))
    check(ai["recipe_ingredients"] == EXPECTED_INGREDIENTS,
          "direct recipe-page paste (recipe_data key) is also backfilled")


def test_apply_noop_without_data():
    ai = {"recipe_ingredients": ["a"], "recipe_instructions": ["b"]}
    out = apply_structured_recipe(dict(ai), _content({"offsite_recipe_detected": True}))
    check(out["recipe_ingredients"] == ["a"] and out["recipe_instructions"] == ["b"],
          "no structured data -> ai_result left untouched")


def test_prompt_formatting():
    data = extract_recipe_jsonld(SAMPLE_HTML)
    text = format_recipe_data_for_prompt(data)
    check("Ingredients:" in text and "- 250 g spinach" in text,
          "prompt block lists ingredients as a bulleted list")
    check("Instructions:" in text and "1. Add the garlic" in text,
          "prompt block numbers the instruction steps")
    check("Yield: 4 servings" in text, "prompt block includes the yield")


def test_full_page_reproduced_in_note():
    data = extract_recipe_jsonld(SAMPLE_HTML)
    # Article markdown as it looks AFTER image localization: an EmbedRelativeTo media block.
    article_md = (
        "## Garlic Confit Pasta\n\n"
        "\n```EmbedRelativeTo\nmedia://instagram/drveganblog-hero.jpg\n```\n\n"
        "A silky, garlicky weeknight pasta.\n\n"
        "### Notes from the chef\n\nUse good olive oil.\n"
    )
    ai = {"note_type": "instagram_reel", "title": "Garlic confit pasta", "summary": "x",
          "tags": ["pasta"], "source_language": "English"}
    meta = {
        "author_handle": "dr.vegan",
        "offsite_recipe_detected": True,
        "followed_recipe_url": "https://drveganblog.com/garlic-confit-pasta/",
        "followed_recipe_data": data,
        "followed_recipe_article_markdown": article_md,
    }
    content = _content(meta)
    ai = apply_structured_recipe(ai, content)
    note = format_note(ai, content, [], None, CFG)

    check(HEADING in note, "bio section heading present")
    # Note: convert_measurements annotates units, so "250 g" and "spinach" are no longer
    # contiguous in the rendered note (e.g. "250 g (≈8.8 oz) spinach").
    check("### Ingredients" in note and "250 g" in note and "spinach" in note,
          "accurate structured recipe (from JSON-LD) rendered in the note")
    check(note.count("### Ingredients") == 1, "recipe appears exactly once (no duplicate)")
    check("### 📄 Full recipe page" in note, "source page is reproduced under its own heading")
    check("media://instagram/drveganblog-hero.jpg" in note,
          "the page's image survives into the reproduced page")
    check("Notes from the chef" in note, "the page's formatting/sections are preserved")
    check("reproduced beneath it" in note, "banner tells the reader the full page follows")
    check("drveganblog.com/garlic-confit-pasta" in note, "source URL cited")


def test_page_reproduced_even_without_structured_recipe():
    article_md = "## Some Dish\n\nBody text with no schema.org recipe.\n"
    ai = {"note_type": "instagram_reel", "title": "Some dish", "summary": "x", "tags": ["food"]}
    meta = {
        "offsite_recipe_detected": True,
        "followed_recipe_url": "https://example.com/some-dish/",
        "followed_recipe_article_markdown": article_md,
    }
    note = format_note(ai, _content(meta), [], None, CFG)
    check("### 📄 Full recipe page" in note,
          "page still reproduced when no structured recipe could be parsed")
    check("could not parse a clean structured recipe" in note,
          "honest banner when the page had no machine-readable recipe")
    check("Body text with no schema.org recipe." in note, "the followed page body is included")


def main():
    print("Off-site recipe extraction + reproduction checks")
    for fn in (
        test_jsonld_parse_exact,
        test_jsonld_absent,
        test_iso_durations,
        test_apply_override_english,
        test_apply_keeps_translation_for_non_english,
        test_apply_direct_recipe_data_key,
        test_apply_noop_without_data,
        test_prompt_formatting,
        test_full_page_reproduced_in_note,
        test_page_reproduced_even_without_structured_recipe,
    ):
        print(f"\n[{fn.__name__}]")
        fn()
    print(f"\n{_checks} checks passed.")


if __name__ == "__main__":
    main()
