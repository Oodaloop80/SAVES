"""Regression checks for TikTok photo/slideshow-post extraction (src/extractors/tiktok.py).

Run: python scripts/test_tiktok_photo.py
Exits non-zero on the first failed assertion. Pure logic, no network, no Claude tokens — the
gallery-dl output is a captured fixture.

The bug these lock down: a TikTok *photo* post (URL ``/photo/<id>``) went to yt-dlp, which raises
``UnsupportedError`` on photo URLs. The extractor got no info.json and returned empty content
(author "unknown", no caption, no media), so Claude hallucinated a title/folder from the handle +
the ``?q=recipe`` in the URL — a "2 Days Delights Pasta Recipe" note with no information for a post
that was actually six wrap-bread recipes. Photo posts now take the gallery-dl path, which yields
the author, the verbatim line-preserved caption (the same ``contents[]`` blob videos use), and the
still-image URLs — while dropping the background-music mp3 so it never hits Whisper.
"""
import os
import sys

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.extractors.base import ExtractedContent  # noqa: E402
from src.extractors.tiktok import (  # noqa: E402
    _is_photo_url,
    _parse_photo_gallerydl,
    _yyyymmdd_from_unix,
)
from src.notes.formatter import _RENDERERS, _render_instagram_post, format_note  # noqa: E402

_checks = 0


def check(cond: bool, msg: str) -> None:
    global _checks
    _checks += 1
    if not cond:
        print(f"FAIL: {msg}")
        sys.exit(1)
    print(f"  ok: {msg}")


# A captured-shape gallery-dl -j fixture: one type-2 metadata message (author, contents[], desc,
# stats, createTime) + one type-3 image + one type-3 background-music mp3.
PHOTO_FIXTURE = [
    [2, {
        "author": {"nickname": "2days.delights", "uniqueId": "2days.delights"},
        "desc": "Learn how to make six different wraps. Flour Tortillas 4 cups flour ...",
        "contents": [
            {"desc": "Learn how to make six different wraps at home, with step-by-step recipes.",
             "textExtra": []},
            {"desc": "", "textExtra": []},
            {"desc": "🌯 Flour Tortillas Recipe 🌯", "textExtra": []},
            {"desc": "- 4 cups (480g) bread flour", "textExtra": []},
            {"desc": "- 1½ tsp (9g) salt #pitabread #naan", "textExtra": []},
        ],
        "stats": {"diggCount": 218, "playCount": 90000},
        "createTime": 1650830728,   # 2022-04-24 UTC
    }],
    [3, "https://p16-common-sign.tiktokcdn-us.com/img1~tplv-photomode.jpeg?sig=1",
     {"type": "image", "num": 1, "extension": "jpeg"}],
    [3, "https://sf19.tiktokcdn-us.com/obj/tos-alisg/background-music",
     {"type": "audio", "num": 0, "extension": "mp3"}],
]


def test_is_photo_url():
    check(_is_photo_url("https://www.tiktok.com/@x/photo/7623585639828606238?q=recipe"),
          "/photo/ URL is recognised as a photo post")
    check(not _is_photo_url("https://www.tiktok.com/@x/video/123"),
          "/video/ URL is NOT a photo post")
    check(not _is_photo_url(None) and not _is_photo_url(""),
          "None/empty URL is safe -> not a photo post")


def test_unix_date():
    check(_yyyymmdd_from_unix(1650830728) == "20220424",
          "createTime unix seconds -> YYYYMMDD")
    check(_yyyymmdd_from_unix(None) is None and _yyyymmdd_from_unix("nope") is None,
          "garbage createTime -> None (no crash)")


def test_parse_photo_fixture():
    p = _parse_photo_gallerydl(PHOTO_FIXTURE)
    check(p is not None, "a real photo fixture parses to a dict")
    check(p["author"] == "2days.delights", "author comes from author.nickname (was 'unknown')")
    # Caption must be the line-preserved contents[] reconstruction, not the flattened desc.
    check(p["caption"].startswith("Learn how to make six different wraps at home"),
          "caption comes from contents[] (verbatim first line)")
    check("\n\n🌯 Flour Tortillas Recipe 🌯" in p["caption"],
          "blank-line section break + header are preserved from contents[]")
    check(p["caption"].count("\n") >= 4, "caption keeps its line structure")


def test_audio_is_dropped():
    p = _parse_photo_gallerydl(PHOTO_FIXTURE)
    check(p["image_urls"] == ["https://p16-common-sign.tiktokcdn-us.com/img1~tplv-photomode.jpeg?sig=1"],
          "only the type=image URL is kept")
    check(all(".mp3" not in u and "background-music" not in u for u in p["image_urls"]),
          "the background-music mp3 is NOT in media_urls (never sent to Whisper)")


def test_hashtags_and_date():
    p = _parse_photo_gallerydl(PHOTO_FIXTURE)
    check(p["hashtags"] == ["pitabread", "naan"], "hashtags are parsed from the caption text")
    check(p["upload_date"] == "20220424", "upload_date derived from createTime")
    check(p["like_count"] == 218 and p["view_count"] == 90000, "stats mapped to like/view counts")


def test_desc_fallback_when_no_contents():
    fixture = [
        [2, {"author": {"nickname": "chef"},
             "desc": "A single-line caption that is comfortably longer than thirty chars.",
             "contents": []}],
        [3, "https://cdn.example/x.jpeg", {"type": "image"}],
    ]
    p = _parse_photo_gallerydl(fixture)
    check(p["caption"].startswith("A single-line caption"),
          "falls back to flattened desc when contents[] is empty")
    check(p["author"] == "chef" and p["image_urls"] == ["https://cdn.example/x.jpeg"],
          "author + image still extracted on the desc-fallback path")


def test_empty_and_garbage_return_none():
    check(_parse_photo_gallerydl([]) is None, "empty message list -> None")
    check(_parse_photo_gallerydl("not a list") is None, "non-list input -> None")
    check(_parse_photo_gallerydl([[2, {}]]) is None,
          "metadata with no caption and no images -> None (caller degrades to minimal note)")


def test_formatter_routes_photo_to_image_post():
    check(_RENDERERS.get("tiktok_photo") is _render_instagram_post,
          "tiktok_photo renders via the image-post renderer (embeds + caption, no transcript)")


def test_formatter_coerces_video_type_for_photo_posts():
    # Even if the model tags a photo post as tiktok_video, is_photo_post forces the image render.
    content = ExtractedContent(
        url="https://www.tiktok.com/@x/photo/1",
        platform="tiktok",
        title="Six wraps",
        author="2days.delights",
        body_text="🌯 Flour Tortillas Recipe 🌯\n- 4 cups flour",
        metadata={"is_photo_post": True},
        media_urls=[],
    )
    ai_result = {"note_type": "tiktok_video", "title": "Six wraps",
                 "folder_path": "SAVES/COOKING", "summary": "Six wrap recipes.", "tags": []}
    note = format_note(ai_result, content, media_paths=["tiktok/x/img.jpeg"],
                       transcript=None, config={})
    check("[!quote] Caption" in note, "photo note still renders the caption")
    check("Transcript" not in note,
          "no empty Transcript section leaks in (coerced away from the video renderer)")
    check("EmbedRelativeTo" in note, "the image is embedded")


for _name, _fn in sorted(globals().items()):
    if _name.startswith("test_") and callable(_fn):
        _fn()

print(f"\nAll {_checks} checks passed.")
