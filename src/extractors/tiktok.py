import asyncio
import http.cookiejar
import json
import logging
import os
import re
import subprocess
import tempfile
from datetime import datetime, timezone

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


def _is_photo_url(url: str) -> bool:
    """True for TikTok photo/slideshow posts (``/photo/<id>``). yt-dlp raises UnsupportedError on
    these, so they take the gallery-dl path in :meth:`TikTokExtractor._extract_photo`."""
    return "/photo/" in (url or "")


def _yyyymmdd_from_unix(ts) -> str | None:
    """TikTok ``createTime`` (unix seconds) → ``YYYYMMDD``, matching the yt-dlp ``upload_date``
    the video path stores. Returns None for missing/garbage values."""
    try:
        return datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y%m%d")
    except Exception:
        return None


def _parse_photo_gallerydl(data) -> dict | None:
    """Pull caption, author, and still-image URLs out of ``gallery-dl -j`` output for a TikTok
    photo post. Pure (no I/O) so it is unit-testable against a captured fixture.

    gallery-dl emits a JSON array of message tuples: ``[2, {kwdict}]`` carries the post metadata
    (author, ``desc``, ``contents``) and ``[3, "<url>", {kwdict}]`` is one downloadable file —
    ``kwdict['type']`` is ``"image"`` for the slides and ``"audio"`` for the background-music mp3,
    which we deliberately drop (transcribing a song is worthless and would burn Whisper time).
    The caption comes from the SAME ``contents[]`` blob the video path uses, so it is line-for-line
    verbatim; ``desc`` (flattened) is the fallback. Returns None when the array yields neither a
    caption nor any images.
    """
    if not isinstance(data, list):
        return None
    meta: dict | None = None
    image_urls: list[str] = []
    for entry in data:
        if not isinstance(entry, list) or not entry:
            continue
        if entry[0] == 2 and len(entry) > 1 and isinstance(entry[1], dict):
            # Prefer the kwdict that actually carries post fields over an empty directory header.
            if meta is None or entry[1].get("author") or entry[1].get("desc"):
                meta = entry[1]
        elif entry[0] == 3 and len(entry) > 2 and isinstance(entry[2], dict):
            if entry[2].get("type") == "image" and isinstance(entry[1], str):
                image_urls.append(entry[1])
    meta = meta or {}
    author_info = meta.get("author") or {}
    caption = caption_from_contents(meta.get("contents")) or (meta.get("desc") or "").strip()
    if not caption and not image_urls:
        return None
    stats = meta.get("stats") or {}
    return {
        "caption": caption,
        "author": author_info.get("nickname") or author_info.get("uniqueId"),
        "image_urls": image_urls,
        "hashtags": re.findall(r"#(\w+)", caption),
        "like_count": stats.get("diggCount"),
        "view_count": stats.get("playCount"),
        "upload_date": _yyyymmdd_from_unix(meta.get("createTime")),
    }


def fetch_photo_post(url: str, cookies_path: str | None = None, timeout: int = 60) -> dict | None:
    """Extract a TikTok photo/slideshow post via ``gallery-dl`` (yt-dlp can't — it raises
    UnsupportedError on ``/photo/`` URLs). Best-effort and non-fatal: returns None on any
    subprocess/JSON failure so the caller degrades to a minimal note. See
    :func:`_parse_photo_gallerydl` for the returned shape."""
    cmd = ["gallery-dl", "-j"]
    if cookies_path and os.path.exists(cookies_path):
        cmd += ["--cookies", cookies_path]
    cmd.append(url)
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        data = json.loads(result.stdout)
    except (json.JSONDecodeError, ValueError):
        logger.debug("gallery-dl -j produced no parseable JSON for %s", url)
        return None
    except Exception as e:
        logger.debug("gallery-dl photo fetch failed for %s: %s", url, e)
        return None
    return _parse_photo_gallerydl(data)


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
        cookies_arg = cookies_path if has_cookies else None

        # TikTok photo/slideshow posts (/photo/<id>): yt-dlp raises UnsupportedError on these, so
        # never hand them to yt-dlp — extract the still images + caption via gallery-dl instead.
        if _is_photo_url(url):
            return self._extract_photo(url, cookies_arg) or ExtractedContent(
                url=url, platform="tiktok", title=url
            )

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
                # yt-dlp returned nothing — it may still be a photo post reached via an unusual
                # (non-/photo/) URL, so try gallery-dl once before giving up on a minimal note.
                return self._extract_photo(url, cookies_arg) or ExtractedContent(
                    url=url, platform="tiktok", title=url
                )

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

    def _extract_photo(self, url: str, cookies_path: str | None) -> ExtractedContent | None:
        """Build ExtractedContent for a TikTok photo/slideshow post (gallery-dl path).

        Returns None when gallery-dl yields nothing usable, so the caller falls back to a minimal
        note. The still-image CDN URLs go straight into ``media_urls`` for the downloader to fetch
        (each is a normal signed JPEG that the existing direct-download handles); vision then OCRs
        any on-image text. No audio is included, so nothing is sent to Whisper. ``is_photo_post``
        in the metadata lets the formatter render it as an image post, not a video.
        """
        photo = fetch_photo_post(url, cookies_path=cookies_path)
        if not photo or not (photo.get("image_urls") or photo.get("caption")):
            return None
        caption = photo.get("caption") or ""
        return ExtractedContent(
            url=url,
            platform="tiktok",
            title=caption[:80] or url,
            author=photo.get("author"),
            body_text=caption,
            metadata={
                "like_count": photo.get("like_count"),
                "view_count": photo.get("view_count"),
                "hashtags": photo.get("hashtags") or [],
                "upload_date": photo.get("upload_date"),
                "is_photo_post": True,
            },
            media_urls=photo.get("image_urls") or [],
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
