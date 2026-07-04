import asyncio
import json
import os
import subprocess
import tempfile

from src.extractors.base import BaseExtractor, ExtractedContent
from src.utils.url_parser import extract_urls


class FacebookExtractor(BaseExtractor):
    def __init__(self, config: dict):
        self.config = config
        self.cookies_dir = config.get("paths", {}).get("cookies_dir", "cookies")

    def can_handle(self, url: str) -> bool:
        return any(d in url for d in ("facebook.com", "fb.com", "fb.watch"))

    async def extract(self, url: str) -> ExtractedContent:
        result = await asyncio.to_thread(self._extract_sync, url)
        # If we found an embedded article URL, route to GenericExtractor
        article_url = result.metadata.get("embedded_article_url")
        if article_url:
            from src.extractors.generic import GenericExtractor
            generic = GenericExtractor(self.config)
            article = await generic.extract(article_url)
            article.metadata["facebook_post_url"] = url
            article.metadata["facebook_description"] = result.body_text
            return article
        return result

    def _extract_sync(self, url: str) -> ExtractedContent:
        cookies_path = os.path.join(self.cookies_dir, "facebook.txt")

        with tempfile.TemporaryDirectory() as tmpdir:
            cmd = ["yt-dlp", "--write-info-json", "--skip-download", "--no-warnings",
                   "-o", os.path.join(tmpdir, "%(id)s.%(ext)s")]
            if os.path.exists(cookies_path):
                cmd += ["--cookies", cookies_path]
            cmd.append(url)
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)

            info_files = [f for f in os.listdir(tmpdir) if f.endswith(".info.json")]
            if not info_files:
                # Raise instead of fabricating ExtractedContent(title=url) — the stub used
                # to burn OCR/analysis tokens and produce a junk approval card. Raising
                # lands it as mark_failed + #SAVES-alerts with the actual yt-dlp reason
                # (typically expired cookies, which the processor's auth routing picks up
                # from the "login" wording in yt-dlp's own stderr when present).
                stderr_tail = (result.stderr or "").strip()[-400:]
                raise RuntimeError(
                    f"Facebook extraction produced no metadata (yt-dlp rc={result.returncode}) — "
                    f"cookies/facebook.txt may be expired or the post is inaccessible: "
                    f"{stderr_tail or 'no stderr'}"
                )

            with open(os.path.join(tmpdir, info_files[0]), encoding="utf-8") as f:
                info = json.load(f)

        description = info.get("description", "")

        # Did yt-dlp actually find a playable video? A real FB video post carries a duration
        # (and a format list); a text/photo/link-share post that merely links an article does
        # not. This gates the reroute below so a video isn't silently discarded.
        has_video = bool(info.get("duration") or info.get("formats"))

        # Detect a shared external article link in the post body.
        embedded_urls = extract_urls(description)
        article_url = next(
            (u for u in embedded_urls if "facebook.com" not in u and "fb.com" not in u),
            None,
        )

        # Reroute the WHOLE post to the article extractor ONLY when this is genuinely a
        # link-share post with no video of its own. A video post that just links a source
        # article in its caption must still be archived AS the video (with the link preserved
        # in metadata) — rerouting it would drop the video, i.e. the thing being saved.
        if article_url and not has_video:
            # Store the article URL so the async extract() can route it
            return ExtractedContent(
                url=url,
                platform="facebook",
                title=description[:80] or url,
                author=info.get("uploader"),
                body_text=description,
                metadata={
                    "embedded_article_url": article_url,
                    "facebook_post_url": url,
                },
                media_urls=[],
            )

        return ExtractedContent(
            url=url,
            platform="facebook",
            title=info.get("title") or description[:80] or url,
            author=info.get("uploader") or info.get("channel"),
            body_text=description,
            metadata={
                "like_count": info.get("like_count"),
                "view_count": info.get("view_count"),
                "upload_date": info.get("upload_date"),
                # A source article linked in a video post's caption — kept in metadata so it
                # surfaces in the note without hijacking the video archive. None when absent
                # (the prompt/metadata dumps skip None values).
                "related_article_url": article_url,
            },
            media_urls=[url],  # video downloaded by downloader
        )
