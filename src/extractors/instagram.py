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

        caption = metadata.get("caption", "")
        first_comment = metadata.get("first_owner_comment") or ""
        # Merge owner's first comment into body_text when it exists and isn't just
        # a repeat of the caption (some tools duplicate the caption as a comment).
        if first_comment and first_comment.strip() != caption.strip():
            body_text = (caption + "\n\n---\n\n" + first_comment).strip() if caption else first_comment
        else:
            body_text = caption

        return ExtractedContent(
            url=url,
            platform="instagram",
            title=caption[:80] or url,
            author=metadata.get("owner_username"),
            body_text=body_text,
            metadata={
                "like_count": metadata.get("likes"),
                "shortcode": metadata.get("shortcode"),
                "post_date": metadata.get("date"),
                "upload_date": metadata.get("date"),
                "author_handle": metadata.get("owner_handle"),
            },
            media_urls=media_urls,
        )

    def _instaloader_metadata(self, url: str) -> dict:
        # instaloader doesn't have a clean JSON-output mode for single posts,
        # so we use yt-dlp --write-info-json for basic metadata.
        # --write-comments fetches the comment list so we can capture the poster's
        # own first comment (many accounts post article text or extra details there).
        import tempfile, json
        with tempfile.TemporaryDirectory() as tmpdir:
            cookies_path = os.path.join(self.cookies_dir, "instagram.txt")
            cmd = ["yt-dlp", "--write-info-json", "--write-comments",
                   "--skip-download", "--no-warnings",
                   "-o", os.path.join(tmpdir, "%(id)s.%(ext)s")]
            if os.path.exists(cookies_path):
                cmd += ["--cookies", cookies_path]
            cmd.append(url)
            subprocess.run(cmd, capture_output=True, timeout=60)
            info_files = [f for f in os.listdir(tmpdir) if f.endswith(".info.json")]
            if info_files:
                with open(os.path.join(tmpdir, info_files[0]), encoding="utf-8") as f:
                    info = json.load(f)
                # yt-dlp: `uploader` is the display name ("Rest In Pizza");
                # `uploader_id`/`channel_id` is the @handle used in the profile
                # URL ("brewtal_pizza"). Keep both — display name for `author`,
                # handle for building the author_url.
                handle = (
                    info.get("uploader_id")
                    or info.get("channel_id")
                    or info.get("channel")
                )
                clean_handle = handle.lstrip("@") if handle else None
                first_owner_comment = self._first_owner_comment(info, clean_handle)
                return {
                    "caption": info.get("description", ""),
                    "owner_username": info.get("uploader") or info.get("channel"),
                    "owner_handle": clean_handle,
                    "likes": info.get("like_count"),
                    "shortcode": info.get("id"),
                    "date": info.get("upload_date"),
                    "first_owner_comment": first_owner_comment,
                }
        return {}

    def _first_owner_comment(self, info: dict, owner_handle: str | None) -> str | None:
        """Return the text of the poster's own first comment, if present.

        Instagram accounts often post extra context (article text, ingredient lists,
        event details) as the very first comment on their own post. We capture it and
        append it to body_text so Claude and the note templates see the full content.
        """
        comments = info.get("comments") or []
        if not comments or not owner_handle:
            return None
        # yt-dlp comment fields: author, author_id, text, timestamp, id
        # author_id typically starts with "@" for Instagram
        owner_lower = owner_handle.lower()
        # Sort by timestamp ascending to find the chronologically first comment
        sorted_comments = sorted(
            comments,
            key=lambda c: c.get("timestamp") or 0,
        )
        for c in sorted_comments:
            commenter = (c.get("author_id") or c.get("author") or "").lstrip("@").lower()
            if commenter == owner_lower:
                text = (c.get("text") or "").strip()
                if text:
                    return text
        return None

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
