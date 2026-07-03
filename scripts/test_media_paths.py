"""Regression checks for the per-source media directory (src/media/downloader.py).

Run: python scripts/test_media_paths.py
Exits non-zero on the first failed assertion.

The bug these lock down: yt-dlp names a download from the video's title, and Facebook reels
(among others) all report the generic title "Video". With a flat ``{media_root}/{platform}``
directory every reel resolved to the same ``facebook/Video.mp4``; yt-dlp skipped the
"already downloaded" file, so several unrelated notes embedded — and transcribed, and OCR'd —
whichever clip landed there first (a stray MMA video, in the report that surfaced this).
``_source_subdir`` gives each post its own folder keyed on the source URL, so the collision is
impossible; this test proves two different reels with the SAME title get DIFFERENT paths, that
re-saving one URL is idempotent, and that the folder name stays filesystem-safe.
"""
import os
import re
import sys

# Emit UTF-8 so any emoji in output don't crash on Windows' cp1252 console.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.media.downloader import (  # noqa: E402
    _source_subdir,
    abs_to_obsidian_embed,
)

_checks = 0


def check(cond: bool, msg: str) -> None:
    global _checks
    _checks += 1
    if not cond:
        print(f"FAIL: {msg}")
        sys.exit(1)
    print(f"  ok: {msg}")


# The exact scenario from the bug report: two different Facebook reels, both titled "Video".
BARPIE = ("The Notorious PIE", "Video", "https://www.facebook.com/reel/2776694502669840")
VILLANI = ("Where to Eat Charlotte", "Video", "https://www.facebook.com/reel/2058948024837207")


def test_same_title_different_source_gives_different_dir():
    a = _source_subdir(*BARPIE)
    b = _source_subdir(*VILLANI)
    check(a != b, "two 'Video'-titled reels get DIFFERENT subdirs (the core collision fix)")


def test_same_source_is_idempotent():
    check(_source_subdir(*BARPIE) == _source_subdir(*BARPIE),
          "same source URL always maps to the same subdir (re-save reuses the folder)")


def test_embed_paths_diverge():
    # End-to-end: the note embed is abs_to_obsidian_embed(<save_dir>/Video.mp4).
    media_root = os.path.join("N:", "NAS", "MEDIA", "SAVES")
    a = abs_to_obsidian_embed(
        os.path.join(media_root, "facebook", _source_subdir(*BARPIE), "Video.mp4"),
        media_root, "vault")
    b = abs_to_obsidian_embed(
        os.path.join(media_root, "facebook", _source_subdir(*VILLANI), "Video.mp4"),
        media_root, "vault")
    check(a != b, "the two notes now embed DIFFERENT media:// paths")
    check(a.startswith("facebook/") and a.endswith("/Video.mp4"),
          "embed path stays under facebook/<subdir>/Video.mp4")


def test_author_slug_present_and_safe():
    d = _source_subdir(*BARPIE)
    check(d.startswith("the-notorious-pie-"), "subdir is prefixed with the slugified author")
    check(re.fullmatch(r"[a-z0-9-]+", d) is not None,
          "subdir is filesystem/URI-safe (lowercase, digits, hyphens only)")
    check(" " not in d and "#" not in d, "no spaces or '#' that would break the media:// URI")


def test_unknown_author_is_hash_only():
    d = _source_subdir("unknown", "Video", "https://www.facebook.com/reel/999")
    check(not d.startswith("unknown"), "'unknown' author is not used as a directory prefix")
    check(re.fullmatch(r"[0-9a-f]{10}", d) is not None, "falls back to a bare 10-char hash")


def test_blank_author_is_hash_only():
    d = _source_subdir("", "Video", "https://www.facebook.com/reel/1000")
    check(re.fullmatch(r"[0-9a-f]{10}", d) is not None, "blank author -> bare hash, no leading '-'")
    check(not d.startswith("-"), "no dangling leading hyphen when author is empty")


def test_different_platforms_same_id_still_unique():
    # Distinct full URLs (different hosts) must not alias even if a numeric id repeats.
    tt = _source_subdir("creator", "Video", "https://www.tiktok.com/@creator/video/123")
    fb = _source_subdir("creator", "Video", "https://www.facebook.com/reel/123")
    check(tt != fb, "same author+title on different source URLs stay distinct")


for _name, _fn in sorted(globals().items()):
    if _name.startswith("test_") and callable(_fn):
        _fn()

print(f"\nAll {_checks} checks passed.")
