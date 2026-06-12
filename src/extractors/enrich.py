"""Cross-platform media enrichment.

When a post on one platform (Reddit, Facebook, etc.) embeds or links a video
hosted on another platform (currently YouTube), we enrich the extracted content
with the richer source's data: captions/subtitles (so we skip Whisper),
chapters, channel name, view count, duration, and a canonical source link.

The host post's own data (title, author, subreddit, comments, score) is kept;
the embedded video's data is layered on top under youtube_* metadata keys.
"""
import logging
import re

from src.extractors.base import ExtractedContent

logger = logging.getLogger(__name__)

_YOUTUBE_RE = re.compile(r'(?:youtube\.com/watch\?v=|youtu\.be/|youtube\.com/shorts/)', re.IGNORECASE)


def _find_youtube_url(urls: list[str]) -> str | None:
    for u in urls or []:
        if _YOUTUBE_RE.search(u):
            return u
    return None


async def enrich_embedded_media(content: ExtractedContent, config: dict) -> ExtractedContent:
    """If the content embeds a YouTube video, layer YouTube data onto it.
    Non-fatal: any failure returns the original content unchanged."""
    if content.platform == "youtube":
        return content

    yt_url = _find_youtube_url(content.media_urls)
    if not yt_url:
        return content

    try:
        from src.extractors.youtube import YouTubeExtractor
        yt = await YouTubeExtractor(config).extract(yt_url)
    except Exception as e:
        logger.warning(f"Embedded YouTube enrichment failed for {yt_url}: {e}")
        return content

    # Prefer YouTube captions over Whisper (complete + accurate, no transcription cost)
    if not content.captions and yt.captions:
        content.captions = yt.captions
    if not content.chapters and yt.chapters:
        content.chapters = yt.chapters

    ym = yt.metadata or {}
    # Always record the canonical YouTube link, even if metadata extraction was thin.
    content.metadata["youtube_url"] = yt.url
    if yt.author:
        content.metadata["youtube_channel"] = yt.author
    if ym.get("channel_id"):
        content.metadata["youtube_channel_id"] = ym["channel_id"]
    if yt.title:
        content.metadata["youtube_title"] = yt.title
    if ym.get("video_id"):
        content.metadata["youtube_video_id"] = ym["video_id"]
    if ym.get("view_count") is not None:
        content.metadata["youtube_views"] = ym["view_count"]
    if ym.get("like_count") is not None:
        content.metadata["youtube_likes"] = ym["like_count"]
    if ym.get("duration") is not None:
        content.metadata["youtube_duration"] = ym["duration"]
    if ym.get("upload_date"):
        content.metadata["youtube_upload_date"] = ym["upload_date"]

    # The YouTube description usually holds the full recipe / instructions / links.
    # Stored in metadata so the formatter renders it in a dedicated section and the
    # prompt builder feeds it to Claude for tagging — without overwriting the host
    # post's own body or mis-attributing it.
    if yt.body_text:
        content.metadata["youtube_description"] = yt.body_text

    logger.info(
        "Enriched %s post with YouTube video %s (captions=%s, chapters=%s, description=%d chars)",
        content.platform, yt_url, bool(yt.captions), bool(yt.chapters),
        len(yt.body_text or ""),
    )
    return content
