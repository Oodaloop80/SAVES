import asyncio
import os
import subprocess

import praw

from src.extractors.base import BaseExtractor, ExtractedContent
from src.utils.url_parser import resolve_reddit_short_url


class RedditExtractor(BaseExtractor):
    def __init__(self, config: dict):
        self.config = config
        pcfg = config.get("platforms", {}).get("reddit", {})
        self.top_comments_count = pcfg.get("top_comments_count", 5)
        self.include_op_top_level = pcfg.get("include_op_top_level_comments", True)
        self.cookies_dir = config.get("paths", {}).get("cookies_dir", "cookies")

    def _make_reddit(self) -> praw.Reddit:
        return praw.Reddit(
            client_id=os.environ["REDDIT_CLIENT_ID"],
            client_secret=os.environ["REDDIT_CLIENT_SECRET"],
            user_agent=os.environ["REDDIT_USER_AGENT"],
        )

    def can_handle(self, url: str) -> bool:
        return "reddit.com" in url or "redd.it" in url

    async def extract(self, url: str) -> ExtractedContent:
        url = resolve_reddit_short_url(url)
        return await asyncio.to_thread(self._extract_sync, url)

    def _extract_sync(self, url: str) -> ExtractedContent:
        reddit = self._make_reddit()
        submission = reddit.submission(url=url)
        submission.comments.replace_more(limit=0)

        # Build top comments list
        all_comments = list(submission.comments)
        top_by_score = sorted(all_comments, key=lambda c: c.score, reverse=True)[:self.top_comments_count]
        op_top_level = []
        if self.include_op_top_level and submission.author:
            op_top_level = [
                c for c in all_comments
                if str(c.author) == str(submission.author)
            ]

        seen_ids = set()
        combined = []
        for c in top_by_score + op_top_level:
            if c.id not in seen_ids:
                seen_ids.add(c.id)
                combined.append({"author": str(c.author), "score": c.score, "text": c.body})
        combined.sort(key=lambda x: x["score"], reverse=True)

        media_urls = []
        if submission.is_video and submission.media:
            media_urls.append(submission.media["reddit_video"]["fallback_url"])
        elif getattr(submission, "is_gallery", False):
            media_urls = self._gallery_urls(url)
        elif submission.url and submission.url.endswith((".jpg", ".jpeg", ".png", ".gif", ".webp")):
            media_urls.append(submission.url)

        return ExtractedContent(
            url=url,
            platform="reddit",
            title=submission.title,
            author=f"u/{submission.author}" if submission.author else None,
            body_text=submission.selftext or "",
            metadata={
                "subreddit": str(submission.subreddit),
                "score": submission.score,
                "flair": submission.link_flair_text,
                "created_utc": submission.created_utc,
                "permalink": f"https://reddit.com{submission.permalink}",
            },
            media_urls=media_urls,
            top_comments=combined,
        )

    def _gallery_urls(self, url: str) -> list[str]:
        cookies_path = os.path.join(self.cookies_dir, "reddit.txt")
        cmd = ["gallery-dl", "--get-urls", url]
        if os.path.exists(cookies_path):
            cmd += ["--cookies", cookies_path]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            return [line.strip() for line in result.stdout.splitlines() if line.strip().startswith("http")]
        except Exception:
            return []
