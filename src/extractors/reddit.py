import asyncio
import html
import http.cookiejar
import logging
import os
import re
import time
import urllib.parse

import requests

from src.extractors.base import BaseExtractor, ExtractedContent
from src.utils.url_parser import resolve_reddit_short_url

logger = logging.getLogger(__name__)

# Persistent session so Reddit/Cloudflare sees a consistent client.
_SESSION = requests.Session()
_SESSION.headers.update({
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/html, */*;q=0.9",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-origin",
})

_COOKIES_LOADED = False


def _load_cookies(cookies_dir: str) -> bool:
    """Load cookies/reddit.txt (Netscape format) into the shared session, once.

    Cookies are exported with "Get cookies.txt LOCALLY". They are domain-scoped to
    .reddit.com, so they apply to both www.reddit.com and old.reddit.com. Returns
    True if a cookie file was found and loaded.
    """
    global _COOKIES_LOADED
    if _COOKIES_LOADED:
        return True
    cookie_path = os.path.join(cookies_dir, "reddit.txt")
    if not os.path.exists(cookie_path):
        return False
    try:
        jar = http.cookiejar.MozillaCookieJar(cookie_path)
        jar.load(ignore_discard=True, ignore_expires=True)
        _SESSION.cookies.update(jar)
        _COOKIES_LOADED = True
        logger.info("Loaded Reddit cookies from %s (%d cookies)", cookie_path, len(jar))
        return True
    except Exception as e:
        logger.warning("Failed to load Reddit cookies from %s: %s", cookie_path, e)
        return False


def _to_old_reddit_json(url: str) -> str:
    """Convert any reddit.com URL to an old.reddit.com .json URL (path only, no query)."""
    parsed = urllib.parse.urlparse(url)
    # Force old.reddit.com — significantly less Cloudflare-protected
    host = re.sub(r'^(www\.|new\.)?', 'old.', parsed.netloc, flags=re.IGNORECASE)
    path = parsed.path.rstrip("/")
    return f"{parsed.scheme}://{host}{path}.json"


class RedditExtractor(BaseExtractor):
    def __init__(self, config: dict):
        self.config = config
        pcfg = config.get("platforms", {}).get("reddit", {})
        self.top_comments_count = pcfg.get("top_comments_count", 5)
        self.include_op_top_level = pcfg.get("include_op_top_level_comments", True)
        self.cookies_dir = config.get("paths", {}).get("cookies_dir", "cookies")

    def can_handle(self, url: str) -> bool:
        return "reddit.com" in url or "redd.it" in url

    async def extract(self, url: str) -> ExtractedContent:
        url = resolve_reddit_short_url(url)
        return await asyncio.to_thread(self._extract_sync, url)

    def _extract_sync(self, url: str) -> ExtractedContent:
        _load_cookies(self.cookies_dir)
        json_url = _to_old_reddit_json(url)
        logger.debug("Reddit JSON URL: %s", json_url)

        resp = _SESSION.get(json_url, timeout=20)

        # 429 rate-limit — wait and retry once
        if resp.status_code == 429:
            retry_after = int(resp.headers.get("Retry-After", "10"))
            logger.warning("Reddit rate-limited; waiting %ds", retry_after)
            time.sleep(retry_after)
            resp = _SESSION.get(json_url, timeout=20)

        if resp.status_code == 403:
            # Try to read JSON reason; HTML means Cloudflare blocked (not a private sub)
            reason = ""
            if "application/json" in resp.headers.get("Content-Type", ""):
                try:
                    reason = resp.json().get("reason", "")
                except Exception:
                    pass
            subreddit = _subreddit_from_url(url)
            if reason == "private":
                raise PermissionError(
                    f"private subreddit — {subreddit} requires membership to view"
                )
            if reason == "quarantined":
                raise PermissionError(
                    f"quarantined subreddit — {subreddit} requires opt-in to view"
                )
            raise PermissionError(
                f"Reddit returned 403 for {subreddit}. "
                "The subreddit may be private/restricted, or Reddit is blocking "
                "this request. Try exporting your Reddit cookies and placing them "
                "in cookies/reddit.txt."
            )

        resp.raise_for_status()

        if "application/json" not in resp.headers.get("Content-Type", ""):
            raise RuntimeError(
                f"Reddit returned non-JSON response (likely a Cloudflare challenge). "
                f"Content-Type: {resp.headers.get('Content-Type')}. "
                "Export your Reddit browser cookies to cookies/reddit.txt to fix this."
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
        # Reddit-hosted video (v.redd.it): hand the permalink to yt-dlp, which muxes
        # the separate DASH video+audio streams. The raw fallback_url is video-ONLY.
        if post.get("is_video"):
            permalink = post.get("permalink")
            if permalink:
                return [f"https://www.reddit.com{permalink}"]
            video_url = (post.get("media") or {}).get("reddit_video", {}).get("fallback_url")
            return [video_url] if video_url else []
        if post.get("is_gallery") and post.get("media_metadata"):
            return self._gallery_urls(post)
        post_url = post.get("url", "")
        # Direct image link
        if re.search(r'\.(jpg|jpeg|png|gif|webp)(\?|$)', post_url, re.IGNORECASE):
            return [post_url]
        # External video host linked from the post (YouTube, Streamable, etc.) —
        # yt-dlp will resolve it. post_hint marks these as "rich:video"/"hosted:video".
        hint = post.get("post_hint", "")
        if hint in ("rich:video", "hosted:video") and post_url:
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

    def _collect_comments(
        self, children: list, ancestors: list, result: list
    ) -> None:
        """Recursively traverse the comment tree, recording each comment with its ancestors."""
        for c in children:
            if c.get("kind") != "t1":
                continue
            d = c["data"]
            permalink = (
                f"https://reddit.com{d['permalink']}?context=3"
                if d.get("permalink") else ""
            )
            entry = {
                "id": d.get("id", ""),
                "author": d.get("author", "[deleted]"),
                "score": d.get("score", 0),
                "text": d.get("body", ""),
                "permalink": permalink,
                "ancestors": list(ancestors),
            }
            result.append(entry)
            replies = d.get("replies")
            if replies and isinstance(replies, dict):
                sub = replies.get("data", {}).get("children", [])
                self._collect_comments(sub, ancestors + [entry], result)

    def _top_comments(self, post: dict, listing: list) -> list[dict] | None:
        op = post.get("author", "")

        # Recursively collect every comment in the thread tree
        all_comments: list[dict] = []
        self._collect_comments(listing, [], all_comments)

        top_level = [c for c in all_comments if not c["ancestors"]]
        nested    = [c for c in all_comments if c["ancestors"]]

        # Top N top-level comments by score
        top_n = sorted(top_level, key=lambda c: c["score"], reverse=True)[:self.top_comments_count]

        # OP's own top-level comments (may already be in top_n)
        op_top = [c for c in top_level if self.include_op_top_level and c["author"] == op] if op else []

        # OP nested replies — include up to 5, with their full ancestor chain for context
        op_nested = [c for c in nested if c["author"] == op][:5] if op else []

        def _fmt(c: dict, with_thread: bool) -> dict:
            out: dict = {
                "id": c["id"],
                "author": c["author"],
                "score": c["score"],
                "text": c["text"],
                "permalink": c["permalink"],
                "is_op": bool(op and c["author"] == op),
                "thread_context": None,
            }
            if with_thread and c["ancestors"]:
                out["thread_context"] = [
                    {
                        "id": a["id"],
                        "author": a["author"],
                        "score": a["score"],
                        "text": a["text"],
                        "permalink": a["permalink"],
                        "is_op": bool(op and a["author"] == op),
                    }
                    for a in c["ancestors"]
                ]
            return out

        seen: set[str] = set()
        combined: list[dict] = []

        for c in sorted(top_n + op_top, key=lambda x: x["score"], reverse=True):
            if c["id"] not in seen:
                seen.add(c["id"])
                combined.append(_fmt(c, with_thread=False))

        for c in op_nested:
            if c["id"] not in seen:
                seen.add(c["id"])
                combined.append(_fmt(c, with_thread=True))

        return combined or None


def _subreddit_from_url(url: str) -> str:
    m = re.search(r'reddit\.com/r/([^/]+)', url)
    return f"r/{m.group(1)}" if m else "unknown subreddit"
