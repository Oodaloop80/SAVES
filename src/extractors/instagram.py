import asyncio
import os
import random
import subprocess
import time

from src.extractors.base import BaseExtractor, ExtractedContent
from src.utils.url_parser import normalize_url


class InstagramExtractor(BaseExtractor):
    def __init__(self, config: dict):
        self.config = config
        pcfg = config.get("platforms", {}).get("instagram", {})
        self.delay = pcfg.get("delay_seconds", 4.0)
        self.cookies_dir = config.get("paths", {}).get("cookies_dir", "cookies")

    def can_handle(self, url: str) -> bool:
        return "instagram.com" in url

    async def extract(self, url: str) -> ExtractedContent:
        url = normalize_url(url)
        return await asyncio.to_thread(self._extract_sync, url)

    def _extract_sync(self, url: str) -> ExtractedContent:
        delay = self.delay + random.uniform(-1.0, 1.0)
        time.sleep(max(delay, 1.0))

        # Try instaloader for metadata
        metadata = self._instaloader_metadata(url)

        # Media via gallery-dl
        media_urls = self._gallery_dl_urls(url)
        if not media_urls:
            # Reels fallback: yt-dlp
            media_urls = [url]

        return ExtractedContent(
            url=url,
            platform="instagram",
            title=metadata.get("caption", "")[:80] or url,
            author=metadata.get("owner_username"),
            body_text=metadata.get("caption", ""),
            metadata={
                "like_count": metadata.get("likes"),
                "shortcode": metadata.get("shortcode"),
                "post_date": metadata.get("date"),
            },
            media_urls=media_urls,
        )

    def _instaloader_metadata(self, url: str) -> dict:
        # instaloader doesn't have a clean JSON-output mode for single posts,
        # so we use yt-dlp --write-info-json for basic metadata
        import tempfile, json
        with tempfile.TemporaryDirectory() as tmpdir:
            cookies_path = os.path.join(self.cookies_dir, "instagram.txt")
            cmd = ["yt-dlp", "--write-info-json", "--skip-download", "--no-warnings",
                   "-o", os.path.join(tmpdir, "%(id)s.%(ext)s")]
            if os.path.exists(cookies_path):
                cmd += ["--cookies", cookies_path]
            cmd.append(url)
            subprocess.run(cmd, capture_output=True, timeout=60)
            info_files = [f for f in os.listdir(tmpdir) if f.endswith(".info.json")]
            if info_files:
                with open(os.path.join(tmpdir, info_files[0]), encoding="utf-8") as f:
                    info = json.load(f)
                return {
                    "caption": info.get("description", ""),
                    "owner_username": info.get("uploader") or info.get("channel"),
                    "likes": info.get("like_count"),
                    "shortcode": info.get("id"),
                    "date": info.get("upload_date"),
                }
        return {}

    def _gallery_dl_urls(self, url: str) -> list[str]:
        cookies_path = os.path.join(self.cookies_dir, "instagram.txt")
        cmd = ["gallery-dl", "--get-urls"]
        if os.path.exists(cookies_path):
            cmd += ["--cookies", cookies_path]
        cmd.append(url)
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            return [l.strip() for l in result.stdout.splitlines()
                    if l.strip().startswith("http")]
        except Exception:
            return []
