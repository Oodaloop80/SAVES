import os
import re
from datetime import date, datetime, timezone

from src.extractors.base import ExtractedContent


def _format_posted(value) -> str:
    """Render a post date. Accepts a Unix epoch (int/float/numeric str) or a
    YYYYMMDD string (yt-dlp upload_date). Falls back to the raw value as text."""
    if value is None:
        return ""
    # yt-dlp upload_date: "20240115"
    s = str(value)
    if s.isdigit() and len(s) == 8:
        try:
            return datetime.strptime(s, "%Y%m%d").strftime("%Y-%m-%d")
        except ValueError:
            pass
    # Unix epoch seconds (Reddit created_utc, possibly float)
    try:
        ts = float(value)
        return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
    except (TypeError, ValueError, OSError):
        return s

# ─────────────────────────────────────────────────────────
# Public entry point
# ─────────────────────────────────────────────────────────

def format_note(
    ai_result: dict,
    content: ExtractedContent,
    media_paths: list[str],
    transcript: str | None,
    config: dict,
    fact_check_result: dict | None = None,
    location_check_result: dict | None = None,
    include_warnings: bool = False,
) -> str:
    ncfg = config.get("notes", {})
    saved_date = date.today().strftime(ncfg.get("date_format", "%Y-%m-%d"))
    collapse_transcript = ncfg.get("collapse_transcript", True)

    note_type = ai_result.get("note_type", "web_generic")
    parts = [_frontmatter(ai_result, content, saved_date)]

    # Warning callouts (only written to note when include_warnings=True)
    if include_warnings:
        if fact_check_result and fact_check_result.get("disputed_claims"):
            parts.append(_fact_check_callout(fact_check_result))
        if location_check_result and location_check_result.get("location_disputed"):
            parts.append(_location_callout(location_check_result))

    # Dispatch to per-type renderer
    renderer = _RENDERERS.get(note_type, _render_web_generic)
    parts.append(renderer(ai_result, content, media_paths, transcript, collapse_transcript))

    return "\n".join(parts)


# ─────────────────────────────────────────────────────────
# Shared components
# ─────────────────────────────────────────────────────────

def _frontmatter(ai_result: dict, content: ExtractedContent, saved_date: str) -> str:
    tags = ai_result.get("tags") or []
    tag_lines = "\n".join(f"  - {t}" for t in tags)
    title = ai_result.get("title", "").replace('"', "'")
    return f"""---
title: "{title}"
source_url: "{content.url}"
platform: {content.platform}
saved_date: {saved_date}
author: {content.author or 'unknown'}
tags:
{tag_lines}
type: save
---"""


def _summary_section(ai_result: dict) -> str:
    s = ai_result.get("summary", "")
    return f"## Summary\n{s}\n" if s else ""


def _takeaways_section(ai_result: dict) -> str:
    items = ai_result.get("key_takeaways") or []
    if not items:
        return ""
    bullets = "\n".join(f"- {t}" for t in items)
    return f"## Key Takeaways\n{bullets}\n"


def _author_link(content: ExtractedContent) -> str:
    """Render author as a clickable link where the platform supports it."""
    a = content.author or ""
    if not a:
        return ""
    m = content.metadata or {}
    if content.platform == "reddit":
        handle = a.split("/")[-1]  # "u/name" -> "name"
        return f"[{a}](https://reddit.com/u/{handle})"
    if content.platform == "youtube" and m.get("channel_id"):
        return f"[{a}](https://youtube.com/channel/{m['channel_id']})"
    return a


def _metadata_section(content: ExtractedContent, saved_date: str) -> str:
    lines = [f"- **Platform:** {content.platform}"]
    author = _author_link(content)
    if author:
        lines.append(f"- **Author:** {author}")
    m = content.metadata or {}
    if m.get("subreddit"):
        sub = m["subreddit"]
        lines.append(f"- **Subreddit:** [r/{sub}](https://reddit.com/r/{sub})")
    if m.get("upload_date"):
        lines.append(f"- **Posted:** {_format_posted(m['upload_date'])}")
    elif m.get("created_utc"):
        lines.append(f"- **Posted:** {_format_posted(m['created_utc'])}")
    if m.get("view_count"):
        lines.append(f"- **Views:** {m['view_count']:,}")
    if m.get("score"):
        lines.append(f"- **Score:** {m['score']}")
    # Embedded YouTube source (when a post links/embeds a YouTube video)
    if m.get("youtube_url"):
        lines.append(f"- **Video Source:** [YouTube]({m['youtube_url']})")
        channel = m.get("youtube_channel")
        if channel:
            if m.get("youtube_channel_id"):
                channel = f"[{channel}](https://youtube.com/channel/{m['youtube_channel_id']})"
            lines.append(f"- **YouTube Channel:** {channel}")
        if m.get("youtube_upload_date"):
            lines.append(f"- **YouTube Posted:** {_format_posted(m['youtube_upload_date'])}")
        yt_stats = []
        if m.get("youtube_views") is not None:
            yt_stats.append(f"{m['youtube_views']:,} views")
        if m.get("youtube_likes") is not None:
            yt_stats.append(f"{m['youtube_likes']:,} likes")
        if m.get("youtube_duration"):
            yt_stats.append(_format_duration(m["youtube_duration"]))
        if yt_stats:
            lines.append(f"- **YouTube:** {' · '.join(yt_stats)}")
    lines.append(f"- **Saved:** {saved_date}")
    return "## Metadata\n" + "\n".join(lines) + "\n"


def _format_duration(seconds) -> str:
    try:
        s = int(seconds)
    except (TypeError, ValueError):
        return str(seconds)
    h, m, sec = s // 3600, (s % 3600) // 60, s % 60
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"


def _media_embeds(media_paths: list[str]) -> str:
    """Emit External File Embed plugin blocks. Each device maps `media://` to its
    own MEDIA root, so the same note embeds correctly on any device/platform."""
    if not media_paths:
        return ""
    blocks = [f"```EmbedRelativeTo\nmedia://{p}\n```" for p in media_paths]
    return "\n\n".join(blocks) + "\n"


def _paragraphize(text: str, sentences_per: int = 4) -> list[str]:
    """Group a flat run-on transcript into readable paragraphs by sentence."""
    text = " ".join(text.split())  # collapse whitespace/newlines
    sentences = re.split(r'(?<=[.!?])\s+', text.strip())
    sentences = [s for s in sentences if s]
    return [" ".join(sentences[i:i + sentences_per])
            for i in range(0, len(sentences), sentences_per)]


def _transcript_block(transcript: str, collapse: bool) -> str:
    if not transcript:
        return ""
    paras = _paragraphize(transcript[:100000])
    if collapse:
        body = "\n>\n> ".join(paras)
        return f"> [!note]- Full Transcript\n> {body}\n"
    return "## Full Transcript\n\n" + "\n\n".join(paras) + "\n"


def _body_quote(content: ExtractedContent) -> str:
    if not content.body_text:
        return ""
    author_line = f"**{content.platform} — {content.author or 'Unknown'}**"
    m = content.metadata or {}
    if m.get("score"):
        author_line += f" — {m['score']:,} upvotes"
    quoted = "\n".join(f"> {l}" for l in content.body_text[:4000].splitlines())
    return f"## Original Content\n> {author_line}\n>\n{quoted}\n"


def _video_description_block(content: ExtractedContent) -> str:
    """Render an embedded YouTube video's description (recipe / instructions / links)."""
    desc = (content.metadata or {}).get("youtube_description")
    if not desc:
        return ""
    channel = (content.metadata or {}).get("youtube_channel", "YouTube")
    quoted = "\n".join(f"> {l}" for l in desc[:6000].splitlines())
    return f"## Video Description\n> [!quote]- From {channel} (YouTube)\n{quoted}\n"


def _comments_section(content: ExtractedContent) -> str:
    if not content.top_comments:
        return ""
    blocks = []
    for c in content.top_comments:
        body = "\n".join(f"> {l}" for l in c["text"][:800].splitlines())
        blocks.append(f"> **u/{c['author']}** ({c['score']} ↑)\n{body}")
    return "## Top Comments\n\n" + "\n\n".join(blocks) + "\n"


def _paywall_warning(content: ExtractedContent) -> str:
    if (content.metadata or {}).get("possible_paywall"):
        return "> [!warning] Content may be paywalled — text extraction may be incomplete\n"
    return ""


def _no_media_warning(media_paths: list[str]) -> str:
    if not media_paths:
        return "> [!warning] Media unavailable\n"
    return ""


def _fact_check_callout(fc: dict) -> str:
    lines = ["> [!warning] Fact-Check Flags"]
    for claim in fc.get("disputed_claims", []):
        lines.append(f"> - **Claimed:** {claim.get('claim', '')}")
        lines.append(f">   **Reality:** {claim.get('reality', '')}")
        if claim.get("source"):
            lines.append(f">   **Source:** {claim['source']}")
    return "\n".join(lines) + "\n"


def _location_callout(lc: dict) -> str:
    stated = lc.get("stated_location", "unknown")
    actual = lc.get("claimed_actual_location", "unknown")
    evidence = lc.get("evidence", "")
    confidence = lc.get("confidence", "")
    lines = [
        "> [!warning] Location Dispute Flagged",
        f"> - **Stated:** {stated}",
        f"> - **Claimed actual:** {actual} ({confidence} confidence)",
    ]
    if evidence:
        lines.append(f"> - **Evidence:** {evidence}")
    return "\n".join(lines) + "\n"


# ─────────────────────────────────────────────────────────
# Per-type renderers
# ─────────────────────────────────────────────────────────

def _chapters_block(content: ExtractedContent) -> str:
    if not content.chapters:
        return ""
    m = content.metadata or {}
    vid_id = m.get("video_id") or m.get("youtube_video_id") or ""
    base = m.get("youtube_url") or content.url
    ch_lines = []
    for ch in content.chapters:
        secs = ch.get("seconds", 0)
        link = f"https://youtube.com/watch?v={vid_id}&t={secs}" if vid_id else base
        ch_lines.append(f"> - [**{ch['time_str']}**]({link}) {ch['title']}")
    return "> [!abstract]- Chapters\n" + "\n".join(ch_lines) + "\n"


def _render_youtube_video(ai_result, content, media_paths, transcript, collapse):
    saved_date = date.today().strftime("%Y-%m-%d")
    parts = []
    parts.append(f"![{ai_result.get('title', '')}]({content.url})\n")
    parts.append(_chapters_block(content))
    parts.append(_summary_section(ai_result))
    parts.append(_takeaways_section(ai_result))
    parts.append(_video_description_block(content))
    parts.append(_transcript_block(transcript, collapse))
    parts.append(_metadata_section(content, saved_date))
    return "\n".join(p for p in parts if p)


def _render_reddit_text(ai_result, content, media_paths, transcript, collapse):
    saved_date = date.today().strftime("%Y-%m-%d")
    parts = [
        _summary_section(ai_result),
        _takeaways_section(ai_result),
        _body_quote(content),
        _comments_section(content),
        _metadata_section(content, saved_date),
    ]
    return "\n".join(p for p in parts if p)


def _render_reddit_gallery(ai_result, content, media_paths, transcript, collapse):
    saved_date = date.today().strftime("%Y-%m-%d")
    parts = [
        _media_embeds(media_paths),
        _no_media_warning(media_paths),
        _summary_section(ai_result),
        _video_description_block(content),
        _body_quote(content),
        _comments_section(content),
        _metadata_section(content, saved_date),
    ]
    return "\n".join(p for p in parts if p)


def _render_reddit_video(ai_result, content, media_paths, transcript, collapse):
    saved_date = date.today().strftime("%Y-%m-%d")
    parts = [
        _media_embeds(media_paths),
        _no_media_warning(media_paths),
        _chapters_block(content),
        _summary_section(ai_result),
        _takeaways_section(ai_result),
        _video_description_block(content),
        _transcript_block(transcript, collapse),
        _body_quote(content),
        _comments_section(content),
        _metadata_section(content, saved_date),
    ]
    return "\n".join(p for p in parts if p)


def _render_instagram_reel(ai_result, content, media_paths, transcript, collapse):
    saved_date = date.today().strftime("%Y-%m-%d")
    caption = content.body_text or content.captions or ""
    parts = [
        _media_embeds(media_paths),
        _no_media_warning(media_paths),
        _transcript_block(transcript, collapse),
    ]
    if caption:
        quoted = "\n".join(f"> {l}" for l in caption[:2000].splitlines())
        parts.append(f"## Caption\n{quoted}\n")
    parts += [
        _summary_section(ai_result),
        _metadata_section(content, saved_date),
    ]
    return "\n".join(p for p in parts if p)


def _render_instagram_post(ai_result, content, media_paths, transcript, collapse):
    saved_date = date.today().strftime("%Y-%m-%d")
    caption = content.body_text or ""
    parts = [_media_embeds(media_paths), _no_media_warning(media_paths)]
    if caption:
        quoted = "\n".join(f"> {l}" for l in caption[:2000].splitlines())
        parts.append(f"## Caption\n{quoted}\n")
    parts += [_summary_section(ai_result), _metadata_section(content, saved_date)]
    return "\n".join(p for p in parts if p)


def _render_tiktok_video(ai_result, content, media_paths, transcript, collapse):
    saved_date = date.today().strftime("%Y-%m-%d")
    caption = content.body_text or ""
    parts = [
        _media_embeds(media_paths),
        _no_media_warning(media_paths),
        _transcript_block(transcript, collapse),
    ]
    if caption:
        quoted = "\n".join(f"> {l}" for l in caption[:2000].splitlines())
        parts.append(f"## Caption\n{quoted}\n")
    parts += [_summary_section(ai_result), _metadata_section(content, saved_date)]
    return "\n".join(p for p in parts if p)


def _render_facebook_video(ai_result, content, media_paths, transcript, collapse):
    saved_date = date.today().strftime("%Y-%m-%d")
    caption = content.body_text or ""
    parts = [
        _media_embeds(media_paths),
        _no_media_warning(media_paths),
        _transcript_block(transcript, collapse),
    ]
    if caption:
        quoted = "\n".join(f"> {l}" for l in caption[:2000].splitlines())
        parts.append(f"## Caption\n{quoted}\n")
    parts += [_summary_section(ai_result), _metadata_section(content, saved_date)]
    return "\n".join(p for p in parts if p)


def _render_facebook_post(ai_result, content, media_paths, transcript, collapse):
    saved_date = date.today().strftime("%Y-%m-%d")
    parts = [
        _media_embeds(media_paths),
        _summary_section(ai_result),
        _body_quote(content),
        _metadata_section(content, saved_date),
    ]
    return "\n".join(p for p in parts if p)


def _render_web_recipe(ai_result, content, media_paths, transcript, collapse):
    saved_date = date.today().strftime("%Y-%m-%d")
    hero = media_paths[:1]
    parts = [
        _media_embeds(hero),
        _summary_section(ai_result),
        "## Ingredients\n*(See original content below)*\n",
        "## Instructions\n*(See original content below)*\n",
        _paywall_warning(content),
        _body_quote(content),
        _metadata_section(content, saved_date),
    ]
    return "\n".join(p for p in parts if p)


def _render_web_travel(ai_result, content, media_paths, transcript, collapse):
    saved_date = date.today().strftime("%Y-%m-%d")
    parts = [
        _media_embeds(media_paths[:4]),
        _summary_section(ai_result),
        _takeaways_section(ai_result),
        "## Key Details\n*(costs, tips, and logistics from original content)*\n",
        _paywall_warning(content),
        _body_quote(content),
        _metadata_section(content, saved_date),
    ]
    return "\n".join(p for p in parts if p)


def _render_web_article(ai_result, content, media_paths, transcript, collapse):
    saved_date = date.today().strftime("%Y-%m-%d")
    hero = media_paths[:1]
    parts = [
        _media_embeds(hero),
        _summary_section(ai_result),
        _takeaways_section(ai_result),
        _paywall_warning(content),
        _body_quote(content),
        _metadata_section(content, saved_date),
    ]
    return "\n".join(p for p in parts if p)


def _render_web_generic(ai_result, content, media_paths, transcript, collapse):
    saved_date = date.today().strftime("%Y-%m-%d")
    hero = media_paths[:1]
    parts = [
        _media_embeds(hero),
        _summary_section(ai_result),
        _takeaways_section(ai_result),
        _paywall_warning(content),
        _body_quote(content),
        _metadata_section(content, saved_date),
    ]
    return "\n".join(p for p in parts if p)


# ─────────────────────────────────────────────────────────
# Dispatch table
# ─────────────────────────────────────────────────────────

_RENDERERS = {
    "youtube_video":   _render_youtube_video,
    "reddit_text":     _render_reddit_text,
    "reddit_gallery":  _render_reddit_gallery,
    "reddit_video":    _render_reddit_video,
    "instagram_reel":  _render_instagram_reel,
    "instagram_post":  _render_instagram_post,
    "tiktok_video":    _render_tiktok_video,
    "facebook_video":  _render_facebook_video,
    "facebook_post":   _render_facebook_post,
    "web_recipe":      _render_web_recipe,
    "web_travel":      _render_web_travel,
    "web_article":     _render_web_article,
    "web_generic":     _render_web_generic,
    # Legacy/fallback aliases from old prompts
    "youtube":         _render_youtube_video,
    "recipe":          _render_web_recipe,
    "travel":          _render_web_travel,
    "article":         _render_web_article,
    "generic":         _render_web_generic,
}
