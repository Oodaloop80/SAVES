"""Regression checks for off-site recipe-pointer detection (src/extractors/profile_recipe.py).

Run: python scripts/test_offsite_detection.py
Exits non-zero on the first failed assertion. Pure logic, no network.

Background: wants_offsite_recipe() gates the "follow the recipe off-platform" machinery. It used
to fire ONLY on bio/profile phrasing ("recipe in bio", "on my profile", 🔗), so a caption that
named the destination outright — "Find the full recipe on lilsipper.com" (a real TikTok that
reconstructed its recipe from the spoken transcript instead of the site) — never triggered a
follow. These cases lock in the broadened rule: an explicit off-platform domain now counts as a
pointer too, but only alongside the LITERAL recipe words, so an ordinary cooking post that merely
mentions some website doesn't get followed, and the poster's own platform is never followed.
"""
import os
import sys

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.extractors.profile_recipe import (  # noqa: E402
    _caption_offsite_links,
    wants_offsite_recipe,
)

_checks = 0


def check(cond: bool, msg: str) -> None:
    global _checks
    _checks += 1
    if not cond:
        print(f"FAIL: {msg}")
        sys.exit(1)
    print(f"  ok: {msg}")


def test_explicit_domain_now_fires():
    # The motivating case: recipe words + a bare domain, no bio phrasing at all.
    check(wants_offsite_recipe("Find the full recipe on lilsipper.com ✨ #blueberries #gummies"),
          "recipe + explicit domain ('lilsipper.com') is detected as an off-site pointer")


def test_bio_phrasing_still_fires():
    # No regression on the original signal.
    check(wants_offsite_recipe("Full recipe on my profile!"),
          "classic 'on my profile' phrasing still fires")
    check(wants_offsite_recipe("recipe \U0001f449 link in bio"),
          "arrow + 'link in bio' still fires")
    check(wants_offsite_recipe("Ready to bake this dough mix — link in bio"),
          "loose cooking cue + bio phrasing still fires (bio path needs no strong wording)")


def test_spelled_out_domain_fires():
    check(wants_offsite_recipe("Drveganblog dot com for the full recipe"),
          "domain spelled 'X dot com' + recipe words fires")


def test_weak_cue_plus_unrelated_domain_does_not_fire():
    # "baked" is a loose cue, not the literal word "recipe"; a stray domain must NOT follow.
    check(not wants_offsite_recipe("I baked these cookies on my sony.com camera today"),
          "loose cooking cue + unrelated domain does NOT fire (guarded by strong recipe wording)")


def test_strong_recipe_plus_food_domain_fires():
    check(wants_offsite_recipe("Get the recipe at seriouseats.com"),
          "strong recipe wording + real food domain fires")


def test_recipe_without_pointer_does_not_fire():
    check(not wants_offsite_recipe("recipe below ⬇️ no link"),
          "recipe words but no off-site destination does NOT fire")
    check(not wants_offsite_recipe("Best pasta ever, so good!"),
          "no recipe and no pointer does NOT fire")


def test_own_platform_domain_is_ignored():
    # A link back to the same social network is not an off-site recipe.
    check(not wants_offsite_recipe("follow me on instagram.com/foo for the recipe"),
          "recipe + own-platform domain (instagram.com) does NOT fire")


def test_caption_offsite_links_shape():
    links = _caption_offsite_links("Find the full recipe on lilsipper.com")
    check(links == [("https://lilsipper.com", "")],
          "the followed candidate resolves to https://lilsipper.com")
    check(_caption_offsite_links("see tiktok.com/@x and instagram.com/y") == [],
          "social hosts are filtered out of the followable candidates")


def test_empty_and_none_safe():
    check(wants_offsite_recipe(None) is False, "None caption is safe -> False")
    check(wants_offsite_recipe("") is False, "empty caption is safe -> False")


for _name, _fn in sorted(globals().items()):
    if _name.startswith("test_") and callable(_fn):
        _fn()

print(f"\nAll {_checks} checks passed.")
