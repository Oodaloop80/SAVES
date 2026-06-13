import asyncio
import logging
import os
import random
import re
import subprocess
import time

from src.extractors.base import BaseExtractor, ExtractedContent
from src.utils.url_parser import normalize_url

logger = logging.getLogger(__name__)


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

        # yt-dlp for metadata (caption, author, comments)
        metadata = self._ytdlp_metadata(url)

        # If yt-dlp returned nothing (no cookies, rate-limited, etc.), fall back to
        # gallery-dl which works for public posts without authentication.
        if not metadata:
            logger.info("Instagram: yt-dlp returned no metadata — falling back to gallery-dl")
            metadata = self._gallery_dl_metadata(url)

        # If we still have no comments from yt-dlp, try instaloader (needs session)
        if not metadata.get("first_owner_comment"):
            fc = self._instaloader_first_owner_comment(url, metadata.get("owner_handle"))
            if fc:
                metadata["first_owner_comment"] = fc

        # Media via gallery-dl
        media_urls = self._gallery_dl_urls(url)
        if not media_urls:
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

    def _ytdlp_metadata(self, url: str) -> dict:
        """Extract post metadata via yt-dlp. Requires cookies for most Instagram posts.
        Returns {} when yt-dlp can't access the post (no cookies, rate-limited, etc.)."""
        import tempfile, json
        with tempfile.TemporaryDirectory() as tmpdir:
            cookies_path = os.path.join(self.cookies_dir, "instagram.txt")
            cookies_exist = os.path.exists(cookies_path)
            cmd = ["yt-dlp", "--write-info-json", "--write-comments",
                   "--skip-download", "--no-warnings",
                   "-o", os.path.join(tmpdir, "%(id)s.%(ext)s")]
            if cookies_exist:
                cmd += ["--cookies", cookies_path]
            cmd.append(url)
            logger.info("Instagram yt-dlp: cookies=%s", cookies_exist)
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            if proc.returncode != 0:
                logger.warning("Instagram yt-dlp exited %d: %s", proc.returncode,
                               (proc.stderr or proc.stdout or "")[:300])
            info_files = [f for f in os.listdir(tmpdir) if f.endswith(".info.json")]
            logger.info("Instagram yt-dlp: info.json files found = %s", info_files)
            if not info_files:
                return {}
            with open(os.path.join(tmpdir, info_files[0]), encoding="utf-8") as f:
                info = json.load(f)
            # `uploader` is the display name; `uploader_id`/`channel_id` is the handle.
            handle = (info.get("uploader_id") or info.get("channel_id") or info.get("channel"))
            clean_handle = handle.lstrip("@") if handle else None
            owner_ids = self._owner_id_set(info, clean_handle)
            logger.info("Instagram yt-dlp: uploader=%r handle=%r comments=%d",
                        info.get("uploader"), clean_handle, len(info.get("comments") or []))
            return {
                "caption": info.get("description", ""),
                "owner_username": info.get("uploader") or info.get("channel"),
                "owner_handle": clean_handle,
                "likes": info.get("like_count"),
                "shortcode": info.get("id"),
                "date": info.get("upload_date"),
                "first_owner_comment": self._first_owner_comment(info, owner_ids),
            }

    def _gallery_dl_metadata(self, url: str) -> dict:
        """Extract post metadata via gallery-dl -j (works for public posts without cookies).

        gallery-dl -j emits a single pretty-printed JSON array. Each element is a
        message tuple: [3, "media_url", {kwdict}] for media, or [2, {kwdict}] for a
        directory. The post metadata (username, description, etc.) lives in the kwdict.
        """
        import json
        cookies_path = os.path.join(self.cookies_dir, "instagram.txt")
        cmd = ["gallery-dl", "-j"]
        if os.path.exists(cookies_path):
            cmd += ["--cookies", cookies_path]
        cmd.append(url)
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            try:
                data = json.loads(result.stdout)
            except ValueError:
                logger.debug("Instagram: gallery-dl -j produced no parseable JSON")
                return {}
            # Find the first message tuple that carries a kwdict with post fields.
            meta = self._first_gallery_dl_kwdict(data)
            if not meta:
                return {}
            # gallery-dl Instagram fields: username=@handle, fullname=display name,
            # description=caption, date="YYYY-MM-DD HH:MM:SS", post_shortcode, likes
            handle = str(meta.get("username") or "").lstrip("@")
            caption = meta.get("description") or meta.get("title") or ""
            raw_date = str(meta.get("date") or "")
            date_str = raw_date[:10].replace("-", "")  # → YYYYMMDD or ""
            logger.info(
                "Instagram: gallery-dl metadata — author=%s, caption=%d chars",
                handle or "(none)", len(caption),
            )
            return {
                "caption": caption,
                "owner_username": meta.get("fullname") or meta.get("username") or "",
                "owner_handle": handle or None,
                "likes": meta.get("likes"),
                "shortcode": (meta.get("post_shortcode") or meta.get("shortcode")
                              or self._shortcode_from_url(url)),
                "date": date_str,
                "first_owner_comment": None,
            }
        except Exception as e:
            logger.debug("Instagram: gallery-dl metadata fallback failed: %s", e)
        return {}

    @staticmethod
    def _first_gallery_dl_kwdict(data) -> dict | None:
        """Pull the first kwdict (metadata dict) out of gallery-dl -j output.

        Prefer a dict that actually has post fields (username/description) so we don't
        grab an empty directory header."""
        if not isinstance(data, list):
            return None
        fallback = None
        for entry in data:
            if not isinstance(entry, list):
                continue
            for part in entry:
                if isinstance(part, dict):
                    if part.get("username") or part.get("description"):
                        return part
                    if fallback is None:
                        fallback = part
        return fallback

    @staticmethod
    def _owner_id_set(info: dict, clean_handle: str | None) -> set:
        """All lowercased identifiers that could mark a comment as the poster's own."""
        ids = set()
        for key in ("uploader_id", "channel_id", "channel", "uploader"):
            v = info.get(key)
            if v:
                ids.add(str(v).lstrip("@").lower())
        if clean_handle:
            ids.add(clean_handle.lower())
        return ids

    def _first_owner_comment(self, info: dict, owner_ids: set) -> str | None:
        """Return the text of the poster's own first comment from yt-dlp's comment list.

        Instagram accounts often post extra context (article text, ingredient lists,
        event details) as the very first comment on their own post. We capture it and
        append it to body_text so Claude and the note templates see the full content.
        """
        comments = info.get("comments") or []
        if not comments:
            logger.debug("Instagram: yt-dlp returned no comments for this post")
            return None
        if not owner_ids:
            return None
        # yt-dlp comment fields: author (username), author_id (numeric), text, timestamp.
        # Match on EITHER field so we don't miss when one is numeric and the other a handle.
        sorted_comments = sorted(comments, key=lambda c: c.get("timestamp") or 0)
        for c in sorted_comments:
            candidates = {
                str(c.get("author") or "").lstrip("@").lower(),
                str(c.get("author_id") or "").lstrip("@").lower(),
            }
            candidates.discard("")
            if candidates & owner_ids:
                text = (c.get("text") or "").strip()
                if text:
                    logger.info("Instagram: captured owner's first comment (%d chars)", len(text))
                    return text
        logger.debug("Instagram: %d comments fetched, none from the post owner", len(comments))
        return None

    @staticmethod
    def _shortcode_from_url(url: str) -> str | None:
        m = re.search(r"/(?:p|reel|tv)/([^/?#]+)", url)
        return m.group(1) if m else None

    def _instaloader_first_owner_comment(self, url: str, owner_handle: str | None = None) -> str | None:
        """Fallback: use instaloader to fetch the owner's first comment.

        Requires a logged-in instaloader session (mounted at
        /root/.config/instaloader/session-<username>). Fully guarded — any failure
        (no session, rate limit, import error, network) returns None without raising.
        """
        shortcode = self._shortcode_from_url(url)
        if not shortcode:
            return None
        try:
            import instaloader
        except ImportError:
            return None
        try:
            L = instaloader.Instaloader(
                download_pictures=False, download_videos=False,
                download_comments=False, save_metadata=False, quiet=True,
            )
            if not self._load_instaloader_session(instaloader, L):
                logger.debug("Instagram: no instaloader session available for comments")
                return None
            post = instaloader.Post.from_shortcode(L.context, shortcode)
            owner = (post.owner_username or "").lower()
            if not owner:
                return None
            best = None  # (timestamp, text) for the earliest owner comment
            for c in post.get_comments():
                if (c.owner.username or "").lower() != owner:
                    continue
                text = (c.text or "").strip()
                if not text:
                    continue
                ts = c.created_at_utc.timestamp() if c.created_at_utc else 0
                if best is None or ts < best[0]:
                    best = (ts, text)
            if best:
                logger.info("Instagram: captured owner's first comment via instaloader (%d chars)", len(best[1]))
                return best[1]
        except Exception as e:
            logger.debug("Instagram: instaloader comment fetch failed: %s", e)
        return None

    def _load_instaloader_session(self, instaloader, L) -> bool:
        """Load the first available instaloader session file. Returns True on success."""
        cfg_dir = os.path.expanduser("~/.config/instaloader")
        try:
            session_files = [
                f for f in os.listdir(cfg_dir) if f.startswith("session-")
            ]
        except OSError:
            return False
        for fname in session_files:
            username = fname[len("session-"):]
            try:
                L.load_session_from_file(username, os.path.join(cfg_dir, fname))
                return True
            except Exception:
                continue
        return False

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
