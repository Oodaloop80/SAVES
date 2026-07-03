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

# Emit UTF-8 so the emoji in note output don't crash on Windows' cp1252 console.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

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
BIO_TITLE = "🔗 Recipe from Bio / Off-Site Link"   # now a styled callout title, not an '##' heading
RECIPE_CALLOUT = "> [!example]+ 🍽️ Recipe"          # the styled Recipe section
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

# A WordPress-Recipe-Maker card: ingredient GROUPS with section titles, an affiliate <a> in an
# ingredient name, and instruction steps with <strong> bold lead-ins — the structure JSON-LD
# flattens away. A minimal JSON-LD Recipe is present too (WPRM pages always emit both) to supply
# name/yield/time. This is the shape drveganblog.com actually serves.
WPRM_HTML = """
<html><head>
<script type="application/ld+json">
{"@context":"https://schema.org","@type":"Recipe","name":"Garlic Confit Pasta",
 "recipeYield":"2 servings","totalTime":"PT25M",
 "recipeIngredient":["2 cloves garlic","120 ml olive oil","salt to taste"],
 "recipeInstructions":[{"@type":"HowToStep","text":"Confit the garlic. Cook gently."},
                       {"@type":"HowToStep","text":"Blend. Until smooth."}]}
</script>
</head><body>
<div class="wprm-recipe-ingredient-group">
  <h4 class="wprm-recipe-ingredient-group-name">For the garlic confit</h4>
  <ul class="wprm-recipe-ingredients">
    <li class="wprm-recipe-ingredient"><span class="wprm-recipe-ingredient-amount">2</span>&#32;<span class="wprm-recipe-ingredient-name">cloves garlic</span></li>
    <li class="wprm-recipe-ingredient"><span class="wprm-recipe-ingredient-amount">120</span>&#32;<span class="wprm-recipe-ingredient-unit">ml</span>&#32;<span class="wprm-recipe-ingredient-name"><a href="https://amzn.to/aff">extra virgin olive oil</a></span></li>
  </ul>
</div>
<div class="wprm-recipe-ingredient-group">
  <h4 class="wprm-recipe-ingredient-group-name">To finish</h4>
  <ul class="wprm-recipe-ingredients">
    <li class="wprm-recipe-ingredient"><span class="wprm-recipe-ingredient-name">fine sea salt</span>&#32;<span class="wprm-recipe-ingredient-notes">to taste</span></li>
  </ul>
</div>
<div class="wprm-recipe-instruction-group">
  <ol class="wprm-recipe-instructions">
    <li id="wprm-recipe-1-step-0-0" class="wprm-recipe-instruction"><div class="wprm-recipe-instruction-text"><span><strong>Confit the garlic.</strong> Cook the cloves gently in the oil.</span></div></li>
    <li id="wprm-recipe-1-step-0-1" class="wprm-recipe-instruction"><div class="wprm-recipe-instruction-text"><span><strong>Blend.</strong> Blitz until smooth.</span></div></li>
  </ol>
</div>
</body></html>
"""


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

    check(BIO_TITLE in note, "bio provenance callout present")
    check(RECIPE_CALLOUT in note, "recipe rendered as its own styled '🍽️ Recipe' callout")
    # This sample is JSON-LD only (no WPRM markup), so ingredients render as a flat list inside
    # the callout. convert_measurements annotates units, so "250 g" and "spinach" are no longer
    # contiguous in the rendered note (e.g. "250 g (≈8.8 oz) spinach").
    check("🧺 Ingredients" in note and "250 g" in note and "spinach" in note,
          "accurate structured recipe (from JSON-LD) rendered in the note")
    check(note.count(RECIPE_CALLOUT) == 1, "recipe appears exactly once (no duplicate)")
    # The Recipe section comes BEFORE the bio/off-site provenance section.
    check(note.index(RECIPE_CALLOUT) < note.index(BIO_TITLE),
          "styled Recipe section is ordered before the bio / off-site link section")
    check("## 📄 Full Web Clipping" in note,
          "source page is reproduced as its own clearly-delineated (##) section")
    check("> [!info] 📎 Reproduced in full from" in note,
          "reproduction carries a source-attribution callout")
    check("#### Notes from the chef" in note,
          "the page's own headings are demoted so they nest under the section header")
    check("media://instagram/drveganblog-hero.jpg" in note,
          "the page's image survives into the reproduced page")
    check("Notes from the chef" in note, "the page's formatting/sections are preserved")
    check("reproduced below" in note, "banner tells the reader the full page follows")
    check("drveganblog.com/garlic-confit-pasta" in note, "source URL cited")


def test_wprm_groups_parsed():
    data = extract_recipe_jsonld(WPRM_HTML)
    check(data is not None, "WPRM page parsed")
    ig = data["ingredient_groups"]
    check([g["name"] for g in ig] == ["For the garlic confit", "To finish"],
          "ingredient section titles preserved from the WPRM markup")
    check(ig[0]["items"][0] == "2 cloves garlic", "amount + name assembled from spans")
    check(ig[0]["items"][1] == "120 ml extra virgin olive oil",
          "amount + unit + name assembled; affiliate <a> reduced to plain text")
    check("amzn.to" not in " ".join(ig[0]["items"]), "affiliate link URL dropped")
    check(ig[1]["items"][0] == "fine sea salt, to taste", "ingredient notes appended after a comma")
    mg = data["instruction_groups"]
    check(mg[0]["steps"][0] == "**Confit the garlic.** Cook the cloves gently in the oil.",
          "instruction bold lead-in preserved as Markdown (**…**)")
    # Flat lists are kept in sync with the groups.
    check(data["ingredients"][0] == "2 cloves garlic", "flat ingredients derived from the groups")


def test_wprm_groups_rendered_in_note():
    data = extract_recipe_jsonld(WPRM_HTML)
    ai = {"note_type": "instagram_reel", "title": "Garlic confit pasta", "summary": "x",
          "tags": ["pasta"], "source_language": "English"}
    content = _content({
        "offsite_recipe_detected": True,
        "followed_recipe_url": "https://drveganblog.com/garlic-confit-pasta/",
        "followed_recipe_data": data,
    })
    ai = apply_structured_recipe(ai, content)
    check(ai["recipe_ingredient_groups"] is not None, "grouped fields set on ai_result")
    note = format_note(ai, content, [], None, CFG)
    check(RECIPE_CALLOUT in note, "styled recipe callout rendered")
    check("**For the garlic confit**" in note and "**To finish**" in note,
          "ingredient section titles are shown as bold sub-headers in the note")
    check("**Confit the garlic.**" in note, "method step bold lead-in rendered in the note")
    check("🍳 Method" in note, "method sub-section labeled")


def test_page_reproduced_even_without_structured_recipe():
    article_md = "## Some Dish\n\nBody text with no schema.org recipe.\n"
    ai = {"note_type": "instagram_reel", "title": "Some dish", "summary": "x", "tags": ["food"]}
    meta = {
        "offsite_recipe_detected": True,
        "followed_recipe_url": "https://example.com/some-dish/",
        "followed_recipe_article_markdown": article_md,
    }
    note = format_note(ai, _content(meta), [], None, CFG)
    check("## 📄 Full Web Clipping" in note,
          "page still reproduced when no structured recipe could be parsed")
    check("could not parse a clean structured recipe" in note,
          "honest banner when the page had no machine-readable recipe")
    check("Body text with no schema.org recipe." in note, "the followed page body is included")


def test_reproduction_strips_page_title_under_hero_image():
    # The image localizer prepends the feature image (an EmbedRelativeTo fence) BEFORE the page's
    # own "# Title". The reproduction must still strip that title (redundant with the note title
    # and the section header) and demote the page's sections so they nest under the ## header,
    # rather than leaving a "## Title" competing with "## 📄 Full Web Clipping".
    article_md = (
        "```EmbedRelativeTo\nmedia://instagram/hero.jpg\n```\n\n"
        "# Creamy Garlic Confit Pasta\n\n"
        "Intro paragraph.\n\n"
        "## Why You Will Love It\n\nBecause garlic.\n\n"
        "### The Confit\n\nLow and slow.\n"
    )
    ai = {"note_type": "instagram_reel", "title": "Creamy Garlic Confit Pasta",
          "summary": "x", "tags": ["pasta"]}
    meta = {
        "offsite_recipe_detected": True,
        "followed_recipe_url": "https://drveganblog.com/garlic-confit-pasta/",
        "followed_recipe_article_markdown": article_md,
    }
    note = format_note(ai, _content(meta), [], None, CFG)
    check("## 📄 Full Web Clipping" in note, "clipping section header present")
    check("media://instagram/hero.jpg" in note, "hero feature image retained in the clipping")
    check("## Creamy Garlic Confit Pasta" not in note,
          "the page's own title does not survive as an h2 competing with the section header")
    check("### Why You Will Love It" in note, "page h2 section demoted to h3 (nests under clipping)")
    check("#### The Confit" in note, "page h3 subsection demoted to h4")


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
        test_wprm_groups_parsed,
        test_wprm_groups_rendered_in_note,
        test_full_page_reproduced_in_note,
        test_page_reproduced_even_without_structured_recipe,
        test_reproduction_strips_page_title_under_hero_image,
    ):
        print(f"\n[{fn.__name__}]")
        fn()
    print(f"\n{_checks} checks passed.")


if __name__ == "__main__":
    main()
