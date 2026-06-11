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
            subprocess.run(cmd, capture_output=True, timeout=60)

            info_files = [f for f in os.listdir(tmpdir) if f.endswith(".info.json")]
            if not info_files:
                return ExtractedContent(url=url, platform="facebook", title=url)

            with open(os.path.join(tmpdir, info_files[0]), encoding="utf-8") as f:
                info = json.load(f)

        description = info.get("description", "")

        # Detect shared article link in post body
        embedded_urls = extract_urls(description)
        article_url = next(
            (u for u in embedded_urls if "facebook.com" not in u and "fb.com" not in u),
            None,
        )
        if article_url:
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
            },
            media_urls=[url],  # video downloaded by downloader
        )
