import asyncio
import html
import logging
import re
import urllib.parse

import requests

from src.extractors.base import BaseExtractor, ExtractedContent
from src.utils.url_parser import resolve_reddit_short_url

logger = logging.getLogger(__name__)

# A browser-like User-Agent avoids Reddit/Cloudflare bot blocking on the public
# .json endpoint. A generic "python-requests" or bot UA frequently gets a 403.
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
}


class RedditExtractor(BaseExtractor):
    def __init__(self, config: dict):
        self.config = config
        pcfg = config.get("platforms", {}).get("reddit", {})
        self.top_comments_count = pcfg.get("top_comments_count", 5)
        self.include_op_top_level = pcfg.get("include_op_top_level_comments", True)

    def can_handle(self, url: str) -> bool:
        return "reddit.com" in url or "redd.it" in url

    async def extract(self, url: str) -> ExtractedContent:
        url = resolve_reddit_short_url(url)
        return await asyncio.to_thread(self._extract_sync, url)

    def _extract_sync(self, url: str) -> ExtractedContent:
        # Build the .json URL from the path only — query strings (?utm_...=)
        # and fragments must be dropped, or ".json" lands inside the query and
        # Reddit returns an HTML page instead of JSON.
        parsed = urllib.parse.urlparse(url)
        path = parsed.path.rstrip("/")
        json_url = f"{parsed.scheme}://{parsed.netloc}{path}.json"
        resp = requests.get(json_url, headers=_HEADERS, timeout=15)

        if resp.status_code == 403:
            try:
                reason = resp.json().get("reason", "")
            except Exception:
                reason = ""
            subreddit = _subreddit_from_url(url)
            if reason == "private":
                raise PermissionError(f"private subreddit — {subreddit} requires membership to view")
            if reason == "quarantined":
                raise PermissionError(f"quarantined subreddit — {subreddit} requires opt-in to view")
            raise PermissionError(
                f"403 from Reddit for {subreddit}. The subreddit may be private/restricted, "
                f"or Reddit is rate-limiting/blocking this request."
            )

        resp.raise_for_status()

        # A non-JSON body means Reddit served an HTML block/error page rather
        # than the API response — surface it clearly instead of a raw decode error.
        ctype = resp.headers.get("content-type", "")
        if "json" not in ctype.lower():
            raise RuntimeError(
                f"Reddit returned non-JSON ({ctype or 'unknown'}) for {json_url} — "
                f"likely a Cloudflare block or rate limit. First bytes: {resp.text[:60]!r}"
            )
        data = resp.json()

        if isinstance(data, dict) and data.get("reason") in ("private", "quarantined"):
            subreddit = _subreddit_from_url(url)
            raise PermissionError(f"private subreddit — {subreddit} requires membership to view")

        post = data[0]["data"]["children"][0]["data"]
        comments_listing = data[1]["data"]["children"] if len(data) > 1 else []

        return ExtractedContent(
            url=url,
            platform="reddit",
            title=post.get("title", ""),
            author=f"u/{post['author']}" if post.get("author") not in (None, "[deleted]") else None,
            body_text=post.get("selftext") or "",
            metadata={
                "subreddit": post.get("subreddit", ""),
                "score": post.get("score", 0),
                "flair": post.get("link_flair_text"),
                "created_utc": post.get("created_utc"),
                "permalink": f"https://reddit.com{post['permalink']}" if post.get("permalink") else url,
            },
            media_urls=self._media_urls(post),
            top_comments=self._top_comments(post, comments_listing),
        )

    def _media_urls(self, post: dict) -> list[str]:
        if post.get("is_video"):
            video_url = (post.get("media") or {}).get("reddit_video", {}).get("fallback_url")
            if video_url:
                return [video_url]
        if post.get("is_gallery") and post.get("media_metadata"):
            return self._gallery_urls(post)
        post_url = post.get("url", "")
        if re.search(r'\.(jpg|jpeg|png|gif|webp)(\?|$)', post_url, re.IGNORECASE):
            return [post_url]
        return []

    def _gallery_urls(self, post: dict) -> list[str]:
        metadata = post.get("media_metadata", {})
        items = post.get("gallery_data", {}).get("items", [])
        ordered_ids = [item["media_id"] for item in items if "media_id" in item] or list(metadata)
        urls = []
        for media_id in ordered_ids:
            meta = metadata.get(media_id, {})
            if meta.get("status") == "valid":
                img_url = html.unescape(meta.get("s", {}).get("u", ""))
                if img_url:
                    urls.append(img_url)
        return urls

    def _top_comments(self, post: dict, listing: list) -> list[dict] | None:
        op = post.get("author", "")
        flat = [
            {
                "id": c["data"].get("id", ""),
                "author": c["data"].get("author", "[deleted]"),
                "score": c["data"].get("score", 0),
                "text": c["data"].get("body", ""),
            }
            for c in listing if c.get("kind") == "t1"
        ]
        top = sorted(flat, key=lambda c: c["score"], reverse=True)[:self.top_comments_count]
        op_comments = [c for c in flat if self.include_op_top_level and c["author"] == op] if op else []
        seen: set[str] = set()
        combined = []
        for c in top + op_comments:
            if c["id"] not in seen:
                seen.add(c["id"])
                combined.append({"author": c["author"], "score": c["score"], "text": c["text"]})
        combined.sort(key=lambda x: x["score"], reverse=True)
        return combined or None


def _subreddit_from_url(url: str) -> str:
    m = re.search(r'reddit\.com/r/([^/]+)', url)
    return f"r/{m.group(1)}" if m else "unknown subreddit"
