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

# TikTok's SEO metadata endpoint. yt-dlp's ``description`` is a flattened, reworded paraphrase
# of the caption (section headers upper-cased, ``**bold**``/``*`` markdown stripped, wording
# changed — e.g. "**Tips for Success:**" becomes "Tips:"). This endpoint instead returns
# ``itemCustomTDK.article`` — the caption exactly as it renders in the app's expanded-caption
# ("...more") overlay: Markdown section headers, bullet lists, blank lines between sections.
# That is what the user actually sees on the post, so we prefer it when present.
_TDK_ENDPOINT = "https://www.tiktok.com/api/customtdk/item/"
_TDK_PARAMS = {
    "aid": "1988",
    "app_language": "en",
    "app_name": "tiktok_web",
    "channel": "tiktok_web",
    "device_platform": "web_pc",
    "os": "windows",
    "region": "US",
    "priority_region": "US",
    "from_page": "video",
}
_TDK_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


def _item_id_from_url(url: str) -> str | None:
    """Pull the numeric item id out of a TikTok video/photo URL."""
    m = re.search(r"/(?:video|photo)/(\d+)", url or "")
    return m.group(1) if m else None


def fetch_tdk_caption(
    item_id: str,
    referer: str | None = None,
    cookies_path: str | None = None,
    timeout: int = 10,
) -> str | None:
    """Fetch the creator's full formatted caption from TikTok's ``customtdk/item`` endpoint.

    Returns ``itemCustomTDK.article`` — the caption with its original ``**bold**`` headers,
    ``*`` bullet lists, and blank-line paragraph breaks intact — with the SEO ``keywords`` list
    appended as a trailing ``Keywords:`` line (both are shown together in the app's expanded
    caption). This is materially better than yt-dlp's ``description``, which TikTok's web layer
    serves as a header-stripped, reworded paraphrase.

    Best-effort and fully non-fatal: returns ``None`` on any network error, non-200, missing
    field, or trivially short article, so the caller falls back to the yt-dlp description. A
    plain GET with the site cookies is enough — the endpoint needs no signed anti-bot tokens.
    """
    if not item_id:
        return None
    try:
        headers = {
            "User-Agent": _TDK_UA,
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": referer or f"https://www.tiktok.com/@/video/{item_id}",
        }
        jar = None
        if cookies_path and os.path.exists(cookies_path):
            try:
                jar = http.cookiejar.MozillaCookieJar(cookies_path)
                jar.load(ignore_discard=True, ignore_expires=True)
            except Exception:
                jar = None
        resp = requests.get(
            _TDK_ENDPOINT,
            params=dict(_TDK_PARAMS, itemId=item_id),
            headers=headers,
            cookies=jar,
            timeout=timeout,
        )
        if resp.status_code != 200:
            return None
        tdk = (resp.json() or {}).get("itemCustomTDK") or {}
        article = (tdk.get("article") or "").strip()
        if len(article) < 30:  # empty / placeholder — not worth overriding the description
            return None
        keywords = tdk.get("keywords") or []
        if isinstance(keywords, list):
            kw = ", ".join(k.strip() for k in keywords if isinstance(k, str) and k.strip())
            if kw:
                article = f"{article}\n\nKeywords: {kw}"
        return article
    except Exception as e:
        logger.debug("TDK caption fetch failed for item %s: %s", item_id, e)
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
        self.use_tdk_caption = pcfg.get("use_tdk_caption", True)
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

        # Prefer the creator's real formatted caption from TikTok's TDK endpoint; fall back to
        # the yt-dlp description (paraphrased/flattened) with line breaks restored.
        description = None
        if self.use_tdk_caption:
            item_id = info.get("id") or _item_id_from_url(url)
            description = fetch_tdk_caption(item_id, referer=url, cookies_path=cookies_path)
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
