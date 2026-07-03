"""Regression checks for source-published nutrition → model-supplemented Nutrition Facts label.
Pure logic — no network/Claude/Discord.

Run: python scripts/test_nutrition_merge.py
Exits non-zero on the first failed assertion.

Behaviour under test (what Bora asked for): the source's published per-serving nutrition is a
FLOOR, not a replacement. We parse schema.org NutritionInformation off the page, feed it to the
model as authoritative, and after analysis deterministically re-assert the source's exact numbers
over the model's estimate while KEEPING the nutrients the source omitted (omegas, added sugars,
vitamins). The label then captions itself honestly ("published … supplemented with estimates").
"""
import os
import sys
import types

# Emit UTF-8 so the emoji in note output don't crash on Windows' cp1252 console.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.notes.formatter import _nutrition_label  # noqa: E402
from src.utils.recipe_data import (  # noqa: E402
    apply_structured_recipe,
    extract_recipe_jsonld,
)

_checks = 0


def check(cond: bool, msg: str) -> None:
    global _checks
    _checks += 1
    if not cond:
        print(f"FAIL: {msg}")
        sys.exit(1)
    print(f"  ok: {msg}")


_JSONLD_PAGE = """
<html><head>
<script type="application/ld+json">
{"@context":"https://schema.org","@type":"Recipe","name":"Test Pasta",
 "recipeIngredient":["200 g pasta","2 cloves garlic"],
 "recipeInstructions":[{"@type":"HowToStep","text":"Boil the pasta."}],
 "recipeYield":"4 servings",
 "nutrition":{"@type":"NutritionInformation",
   "calories":"240 calories","proteinContent":"9 grams","fatContent":"11 g",
   "saturatedFatContent":"2 grams","sodiumContent":"480 milligrams",
   "carbohydrateContent":"30 grams","fiberContent":"3 grams","sugarContent":"2 grams",
   "cholesterolContent":"15","servingSize":"1 plate"}}
</script>
</head><body>...</body></html>
"""


def test_parse_normalizes_units():
    data = extract_recipe_jsonld(_JSONLD_PAGE)
    check(data is not None, "recipe with JSON-LD nutrition parses")
    nut = data.get("nutrition")
    check(isinstance(nut, dict) and nut, "nutrition dict extracted from NutritionInformation")
    check(nut.get("calories") == "240 kcal", "'240 calories' → '240 kcal'")
    check(nut.get("protein") == "9 g", "'9 grams' → '9 g'")
    check(nut.get("total_fat") == "11 g", "'11 g' passes through as '11 g'")
    check(nut.get("saturated_fat") == "2 g", "'2 grams' → '2 g'")
    check(nut.get("sodium") == "480 mg", "'480 milligrams' → '480 mg'")
    check(nut.get("total_carbs") == "30 g", "carbohydrateContent → total_carbs '30 g'")
    check(nut.get("dietary_fiber") == "3 g", "fiberContent → dietary_fiber '3 g'")
    check(nut.get("total_sugars") == "2 g", "sugarContent → total_sugars '2 g'")
    check(nut.get("cholesterol") == "15 mg", "bare '15' cholesterol → conventional '15 mg'")
    check(nut.get("serving_size") == "1 plate", "servingSize passes through verbatim")


def test_parse_absent_nutrition_is_none():
    html = ('<script type="application/ld+json">{"@context":"https://schema.org",'
            '"@type":"Recipe","recipeIngredient":["1 egg"],'
            '"recipeInstructions":[{"@type":"HowToStep","text":"Fry it."}]}</script>')
    data = extract_recipe_jsonld(html)
    check(data is not None, "recipe with no nutrition still parses")
    check(data.get("nutrition") is None, "missing NutritionInformation → nutrition is None")


def _content(meta):
    return types.SimpleNamespace(metadata=meta)


_SOURCE_NUT = {
    "calories": "240 kcal", "protein": "9 g", "total_fat": "11 g", "sodium": "480 mg",
    "total_carbs": "30 g", "serving_size": "1 plate",
}


def test_merge_source_wins_and_supplements_kept():
    ai = {"nutrition": {
        "calories": "300 kcal",   # model guess — must be overridden by the source
        "protein": "8 g",         # model guess — overridden
        "omega_3": "0.3 g",       # supplement — must survive
        "added_sugars": "1 g",    # supplement — must survive
        "micros": [{"name": "Iron", "amount": "2 mg", "dv": "11%"}],
    }}
    out = apply_structured_recipe(ai, _content({"recipe_data": {"nutrition": dict(_SOURCE_NUT)}}))
    nut = out["nutrition"]
    check(nut["calories"] == "240 kcal", "source calories overrides the model's guess")
    check(nut["protein"] == "9 g", "source protein overrides the model's guess")
    check(nut["total_fat"] == "11 g", "source-only field is added to the label")
    check(nut["omega_3"] == "0.3 g", "model omega-3 supplement is preserved")
    check(nut["added_sugars"] == "1 g", "model added-sugars supplement is preserved")
    check(nut.get("micros") and nut["micros"][0]["name"] == "Iron", "model micros preserved")
    sk = nut.get("_source_keys")
    check(isinstance(sk, list) and "calories" in sk and "protein" in sk,
          "_source_keys records the published fields")
    check("omega_3" not in sk and "added_sugars" not in sk,
          "_source_keys does NOT claim the model's supplements as published")
    check(sk == sorted(sk), "_source_keys is sorted (stable output)")


def test_merge_runs_for_non_english():
    # Nutrition is number-based, so unlike ingredients it must merge regardless of language.
    ai = {"source_language": "Spanish", "nutrition": {"calories": "999 kcal"}}
    out = apply_structured_recipe(ai, _content({"recipe_data": {"nutrition": dict(_SOURCE_NUT)}}))
    check(out["nutrition"]["calories"] == "240 kcal", "source nutrition merges even for Spanish")
    check("_source_keys" in out["nutrition"], "_source_keys set for non-English source too")


def test_merge_source_only_when_model_gave_none():
    ai = {"summary": "x"}  # model produced no nutrition object at all
    out = apply_structured_recipe(ai, _content({"recipe_data": {"nutrition": dict(_SOURCE_NUT)}}))
    check(isinstance(out.get("nutrition"), dict), "source-only nutrition still yields a label dict")
    check(out["nutrition"]["sodium"] == "480 mg", "source values populate a label with no model input")
    check(out["nutrition"].get("_source_keys"), "_source_keys present in the source-only case")


def test_merge_noop_without_source():
    ai = {"nutrition": {"calories": "300 kcal"}}
    out = apply_structured_recipe(ai, _content({}))
    check(out["nutrition"] == {"calories": "300 kcal"}, "no source data → nutrition untouched")
    check("_source_keys" not in out["nutrition"], "no _source_keys when nothing was published")


def test_label_disclaimer_and_dv():
    merged = dict(_SOURCE_NUT)
    merged["_source_keys"] = sorted(_SOURCE_NUT.keys())
    label = _nutrition_label(merged)
    check("published by the source" in label, "source-backed label flips to the 'published' caption")
    check("21%" in label, "%DV still computed deterministically for a source value (480 mg Na → 21%)")
    check("_source_keys" not in label, "the private _source_keys marker never renders as a row")


def test_label_default_disclaimer_when_all_estimated():
    label = _nutrition_label({"calories": "200 kcal", "protein": "5 g"})
    check("🤖 AI estimate from the recipe" in label, "pure-estimate label keeps the AI disclaimer")
    check("published by the source" not in label, "no 'published' caption without _source_keys")


def main():
    print("Source-nutrition merge + label checks")
    for fn in (
        test_parse_normalizes_units,
        test_parse_absent_nutrition_is_none,
        test_merge_source_wins_and_supplements_kept,
        test_merge_runs_for_non_english,
        test_merge_source_only_when_model_gave_none,
        test_merge_noop_without_source,
        test_label_disclaimer_and_dv,
        test_label_default_disclaimer_when_all_estimated,
    ):
        print(f"\n[{fn.__name__}]")
        fn()
    print(f"\n{_checks} checks passed.")


if __name__ == "__main__":
    main()
