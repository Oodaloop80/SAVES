import asyncio
import http.cookiejar
import json
import logging
import os
import re
import subprocess
import tempfile

import requests

from src.extractors.base import BaseExtractor, ExtractedContent

logger = logging.getLogger(__name__)

# TikTok's video page embeds a rehydration JSON blob (``__UNIVERSAL_DATA_FOR_REHYDRATION__``)
# whose ``itemStruct.contents`` holds the caption split into the exact lines the app renders —
# one array element per line, with empty ``desc`` entries standing in for the blank lines the
# author typed between sections. That is the caption verbatim (what you see in the app's expanded
# "...more" overlay). The flat ``itemStruct.desc`` / yt-dlp ``description`` is the SAME text with
# every hard line break stripped, so it cannot reproduce the section headers, bullet lists, and
# blank-line paragraph breaks; ``contents`` can. A plain cookies GET is enough — the blob is
# server-rendered into the page HTML (no signed anti-bot tokens, no headless browser).
#
# (An earlier revision used TikTok's ``customtdk/item`` endpoint instead. Its ``article`` field
# looks nicely formatted but is a machine-REWORDED SEO rewrite — it invents a marketing intro,
# rephrases the headers, and is empty for many videos — not the creator's real caption. ``contents``
# is the literal caption and is present whenever the video-detail page loads, so it supersedes it.)
_REHYDRATION_RE = re.compile(
    r'id="__UNIVERSAL_DATA_FOR_REHYDRATION__"[^>]*>(.*?)</script>', re.S
)
_CAPTION_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


def _item_id_from_url(url: str) -> str | None:
    """Pull the numeric item id out of a TikTok video/photo URL."""
    m = re.search(r"/(?:video|photo)/(\d+)", url or "")
    return m.group(1) if m else None


def caption_from_contents(contents) -> str | None:
    """Join ``itemStruct.contents[]`` into the caption exactly as TikTok renders it.

    Each element is ``{"desc": "<one line>", "textExtra": [...]}``; an empty ``desc`` is a blank
    line the author typed between sections. Per-line trailing whitespace is trimmed (TikTok pads
    some lines with a stray space) but the blank lines *between* content lines are preserved, so
    the reconstruction is line-for-line and paragraph-for-paragraph identical to the app. Leading
    and trailing blank lines are dropped.

    Returns ``None`` for anything that isn't a real multi-line caption (missing/empty array, a
    single line, all-blank, or a trivially short result), so the caller falls back to the
    flattened description.
    """
    if not isinstance(contents, list) or len(contents) < 2:
        return None
    lines = [((c.get("desc") or "") if isinstance(c, dict) else "").rstrip() for c in contents]
    while lines and not lines[0]:      # drop leading blank lines
        lines.pop(0)
    while lines and not lines[-1]:     # drop trailing blank lines
        lines.pop()
    if sum(1 for ln in lines if ln) < 2:
        return None
    caption = "\n".join(lines)
    return caption if len(caption) >= 30 else None


def fetch_contents_caption(
    url: str,
    cookies_path: str | None = None,
    timeout: int = 10,
) -> str | None:
    """Fetch the creator's verbatim, line-preserved caption from the video page's rehydration JSON.

    Reads ``__DEFAULT_SCOPE__ -> webapp.video-detail -> itemInfo -> itemStruct -> contents`` from
    the page HTML and reconstructs the caption via :func:`caption_from_contents`. Best-effort and
    fully non-fatal: returns ``None`` on any network error, non-200, missing blob, or JSON-shape
    surprise, so the caller falls back to :func:`restore_caption_linebreaks` on the yt-dlp
    description. A plain GET with the site cookies is enough — no signed anti-bot tokens needed.
    """
    if not _item_id_from_url(url):
        return None
    try:
        headers = {
            "User-Agent": _CAPTION_UA,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        }
        jar = None
        if cookies_path and os.path.exists(cookies_path):
            try:
                jar = http.cookiejar.MozillaCookieJar(cookies_path)
                jar.load(ignore_discard=True, ignore_expires=True)
            except Exception:
                jar = None
        resp = requests.get(url, headers=headers, cookies=jar, timeout=timeout)
        if resp.status_code != 200:
            return None
        m = _REHYDRATION_RE.search(resp.text)
        if not m:
            return None
        data = json.loads(m.group(1))
        item = (
            (data.get("__DEFAULT_SCOPE__", {})
             .get("webapp.video-detail", {})
             .get("itemInfo", {}) or {})
            .get("itemStruct", {})
        )
        return caption_from_contents(item.get("contents"))
    except Exception as e:
        logger.debug("contents caption fetch failed for %s: %s", url, e)
        return None


# A dash bullet in a flattened caption: a space, a hyphen, then a non-space that is not another
# hyphen -- e.g. " -3 tbsp", " -½ tsp", " -Freshly". Internal hyphens ("smoke-point",
# "non-stick", "Center-cut") have no preceding space and never match, and " - " (spaces on both
# sides, a prose dash) doesn't match either because the char after the hyphen must be non-space.
_DASH_BULLET = re.compile(r"(?<=\S) -(?=[^\s-])")


def restore_caption_linebreaks(text: str) -> str:
    """Restore the line breaks TikTok strips from a video's caption.

    TikTok's metadata ``description`` (what yt-dlp returns, used only as the fallback when the
    richer TDK caption is unavailable) flattens the author's hard line breaks. Three artifacts of
    that flattening are recoverable, and this restores each of them when — and only when — the
    description carries no real newlines of its own:

    * **Runs of 2+ ordinary spaces** — the common case; each run was a newline the author typed.
    * **No-break spaces (U+00A0)** — TikTok sometimes preserves a hard break as an ``\\xa0``
      instead (often padded with an ordinary space). Normal typing never puts a no-break space
      between words, so any nbsp-bearing whitespace run is treated as a line break.
    * **Dash bullet lists** — a ``- item`` list flattened onto one line, where every `` -<char>``
      begins a bullet. Only restored when several such markers are present (i.e. it really is a
      list), so an incidental `` -word`` dash in prose is left alone.

    Conservative and idempotent: a no-op when real newlines are already present (nothing was
    flattened). A break TikTok collapsed all the way to a single ordinary space with no dash
    bullet after it (it does this after some colon-terminated headers) is indistinguishable from
    a word gap and is intentionally left glued.
    """
    if not text:
        return ""
    if "\n" in text:
        return text
    restored = re.sub(r"[ \t]*\xa0[ \t\xa0]*", "\n", text)  # no-break-space breaks
    restored = re.sub(r" {2,}", "\n", restored)             # multi-space breaks
    if len(_DASH_BULLET.findall(restored)) >= 3:            # a real dash-bullet list
        restored = _DASH_BULLET.sub("\n-", restored)
    lines = [ln.strip() for ln in restored.split("\n")]
    return "\n".join(ln for ln in lines if ln) or text


class TikTokExtractor(BaseExtractor):
    def __init__(self, config: dict):
        self.config = config
        pcfg = config.get("platforms", {}).get("tiktok", {})
        self.no_watermark = pcfg.get("no_watermark", True)
        # Fetch the creator's verbatim caption from the page's rehydration ``contents[]``.
        # ``use_tdk_caption`` is the legacy name for this flag (the old customtdk source); it is
        # still honoured so an un-updated config keeps working.
        self.use_rich_caption = pcfg.get("use_rich_caption", pcfg.get("use_tdk_caption", True))
        self.cookies_dir = config.get("paths", {}).get("cookies_dir", "cookies")

    def can_handle(self, url: str) -> bool:
        return "tiktok.com" in url

    async def extract(self, url: str) -> ExtractedContent:
        return await asyncio.to_thread(self._extract_sync, url)

    def _extract_sync(self, url: str) -> ExtractedContent:
        cookies_path = os.path.join(self.cookies_dir, "tiktok.txt")
        has_cookies = os.path.exists(cookies_path)

        with tempfile.TemporaryDirectory() as tmpdir:
            cmd = [
                "yt-dlp",
                "--write-info-json",
                "--skip-download",
                "--no-warnings",
                "-o", os.path.join(tmpdir, "%(id)s.%(ext)s"),
            ]
            if has_cookies and self.no_watermark:
                cmd += ["--cookies", cookies_path]
            cmd.append(url)

            subprocess.run(cmd, capture_output=True, timeout=60)

            info_files = [f for f in os.listdir(tmpdir) if f.endswith(".info.json")]
            if not info_files:
                return ExtractedContent(url=url, platform="tiktok", title=url)

            with open(os.path.join(tmpdir, info_files[0]), encoding="utf-8") as f:
                info = json.load(f)

        captions = self._read_auto_captions(info)
        hashtags = [t.get("name", "") for t in info.get("tags", []) if t.get("name")]

        # Prefer the creator's verbatim caption (line-for-line, with blank-line section breaks)
        # from the page's rehydration ``contents[]``; fall back to the yt-dlp description
        # (flattened) with line breaks heuristically restored.
        description = None
        if self.use_rich_caption:
            description = fetch_contents_caption(url, cookies_path=cookies_path)
        if not description:
            description = restore_caption_linebreaks(info.get("description", ""))

        return ExtractedContent(
            url=url,
            platform="tiktok",
            title=info.get("title") or description[:80],
            author=info.get("uploader") or info.get("creator"),
            body_text=description,
            metadata={
                "like_count": info.get("like_count"),
                "view_count": info.get("view_count"),
                "hashtags": hashtags,
                "duration": info.get("duration"),
                "upload_date": info.get("upload_date"),
            },
            media_urls=[url],  # yt-dlp downloads this via downloader
            captions=captions,
        )

    def _read_auto_captions(self, info: dict) -> str | None:
        auto_caps = info.get("automatic_captions", {})
        for lang in ("en", "en-orig"):
            if lang in auto_caps:
                entries = auto_caps[lang]
                for entry in entries:
                    if entry.get("ext") in ("json3", "srv3", "vtt"):
                        # captions are URLs in this format; return description as fallback
                        break
        return None
