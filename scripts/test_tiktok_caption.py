"""Regression checks for TikTok caption fidelity.

Two mechanisms keep a TikTok caption looking like the real post:

1. `fetch_tdk_caption` pulls the creator's *formatted* caption (Markdown headers, `*` bullets,
   blank lines) from TikTok's `customtdk/item` endpoint — the text shown in the app's expanded
   "...more" caption. This is preferred over yt-dlp's `description`, which TikTok's web layer
   serves as a header-stripped, reworded paraphrase. Network call is mocked here.
2. `restore_caption_linebreaks` is the fallback: when the TDK endpoint is unavailable and we
   fall back to yt-dlp's `description`, it turns the runs of 2+ spaces (TikTok's flattened
   newlines) back into line breaks so the caption isn't one wall of text.

Pure logic + mocked HTTP — no live network / yt-dlp.

Run: python scripts/test_tiktok_caption.py
Exits non-zero on the first failed assertion.
"""
import os
import sys
from unittest.mock import patch

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.extractors.base import ExtractedContent  # noqa: E402
from src.extractors.tiktok import (  # noqa: E402
    _item_id_from_url,
    fetch_tdk_caption,
    restore_caption_linebreaks,
)
from src.notes.formatter import format_note  # noqa: E402


class _FakeResponse:
    """Minimal stand-in for a requests.Response."""

    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload

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


# --- TDK (formatted-caption) endpoint ---------------------------------------------------------

# A trimmed copy of a real customtdk/item response for the seafood-stew video.
_TDK_PAYLOAD = {
    "itemCustomTDK": {
        "article": (
            "Indulge in a luxurious creamy seafood stew.\n\n"
            "**Ingredients for the Seafood:**\n"
            "* 300 g shrimp, peeled and deveined\n"
            "* 2 tablespoons olive oil\n\n"
            "**Tips for Success:**\n"
            "* **Seafood Texture:** Cook seafood just until tender to maintain a juicy texture.\n"
            "* **Gluten-Free:** This recipe is naturally gluten-free."
        ),
        "keywords": ["creamy seafood stew recipe", " shrimp lobster stew", " easy seafood stew"],
        "desc": "Learn how to make a creamy seafood stew.",
        "title": "Creamy Seafood Stew Recipe",
    },
    "statusCode": 0,
}


def test_item_id_from_url():
    check(_item_id_from_url("https://www.tiktok.com/@x/video/7620630606224968990?q=1")
          == "7620630606224968990", "video id parsed from a full URL with query string")
    check(_item_id_from_url("https://www.tiktok.com/@x/photo/12345") == "12345",
          "photo id parsed")
    check(_item_id_from_url("https://www.tiktok.com/@x") is None, "no id -> None")
    check(_item_id_from_url("") is None, "empty url -> None")


def test_fetch_tdk_success_prefers_article_and_appends_keywords():
    with patch("src.extractors.tiktok.requests.get",
               return_value=_FakeResponse(200, _TDK_PAYLOAD)):
        out = fetch_tdk_caption("7620630606224968990", referer="https://tiktok.com/x")
    check(out is not None, "success returns a caption string")
    check("**Tips for Success:**" in out, "bold section header preserved verbatim")
    check("* **Seafood Texture:**" in out, "nested bold bullet preserved verbatim")
    check("\n\n**Ingredients for the Seafood:**" in out, "blank line before section preserved")
    check(out.rstrip().startswith("Indulge"), "article body used as the caption")
    check("Keywords: creamy seafood stew recipe, shrimp lobster stew, easy seafood stew" in out,
          "keywords list appended as a trailing comma-joined line (stripped)")


def test_fetch_tdk_non_200_returns_none():
    with patch("src.extractors.tiktok.requests.get",
               return_value=_FakeResponse(403, {})):
        check(fetch_tdk_caption("123") is None, "non-200 status -> None (fall back to yt-dlp)")


def test_fetch_tdk_missing_or_short_article_returns_none():
    with patch("src.extractors.tiktok.requests.get",
               return_value=_FakeResponse(200, {"itemCustomTDK": {"keywords": ["x"]}})):
        check(fetch_tdk_caption("123") is None, "missing article -> None")
    with patch("src.extractors.tiktok.requests.get",
               return_value=_FakeResponse(200, {"itemCustomTDK": {"article": "too short"}})):
        check(fetch_tdk_caption("123") is None, "trivially short article -> None")


def test_fetch_tdk_network_error_and_bad_id_are_nonfatal():
    check(fetch_tdk_caption("") is None, "empty item_id -> None without any request")
    with patch("src.extractors.tiktok.requests.get", side_effect=RuntimeError("boom")):
        check(fetch_tdk_caption("123") is None, "request exception is swallowed -> None")


def test_fetch_tdk_no_keywords_still_returns_article():
    payload = {"itemCustomTDK": {"article": _TDK_PAYLOAD["itemCustomTDK"]["article"]}}
    with patch("src.extractors.tiktok.requests.get",
               return_value=_FakeResponse(200, payload)):
        out = fetch_tdk_caption("123")
    check(out is not None and "Keywords:" not in out,
          "article without keywords -> caption returned, no trailing Keywords line")


def main():
    print("TikTok caption fidelity checks (TDK endpoint + line-break fallback)")
    for fn in (
        test_runs_become_line_breaks,
        test_within_line_single_spaces_preserved,
        test_idempotent_and_noops,
        test_formatter_renders_caption_multiline,
        test_item_id_from_url,
        test_fetch_tdk_success_prefers_article_and_appends_keywords,
        test_fetch_tdk_non_200_returns_none,
        test_fetch_tdk_missing_or_short_article_returns_none,
        test_fetch_tdk_network_error_and_bad_id_are_nonfatal,
        test_fetch_tdk_no_keywords_still_returns_article,
    ):
        print(f"\n[{fn.__name__}]")
        fn()
    print(f"\n{_checks} checks passed.")


if __name__ == "__main__":
    main()
