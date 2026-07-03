import asyncio
import json
import os
import re
import subprocess
import tempfile

from src.extractors.base import BaseExtractor, ExtractedContent


def restore_caption_linebreaks(text: str) -> str:
    """Restore the line breaks TikTok strips from a video's caption.

    TikTok's metadata ``description`` (what yt-dlp returns) flattens the author's hard line
    breaks: every newline they typed comes back as a run of 2+ spaces (typically three), while
    ordinary word gaps stay single spaces. The caption therefore arrives as one wall of text —
    each ingredient and step glued onto the last. When the description carries no real newlines
    but does contain multi-space runs, treat each run as the line break TikTok ate and restore
    it, so the caption renders with its original per-line structure.

    Conservative and idempotent: a no-op when real newlines are already present (nothing was
    flattened) or when there are no multi-space runs to interpret. Note that a break TikTok
    collapsed all the way to a single space (it does this after some colon-terminated header
    lines) is indistinguishable from a word gap and is intentionally left alone.
    """
    if not text or "\n" in text:
        return text or ""
    if not re.search(r" {2,}", text):
        return text
    segments = (seg.strip() for seg in re.split(r" {2,}", text))
    return "\n".join(seg for seg in segments if seg)


class TikTokExtractor(BaseExtractor):
    def __init__(self, config: dict):
        self.config = config
        pcfg = config.get("platforms", {}).get("tiktok", {})
        self.no_watermark = pcfg.get("no_watermark", True)
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
