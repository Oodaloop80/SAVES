import re
import urllib.parse
from datetime import date, datetime, timezone

from src.extractors.base import ExtractedContent


def _format_posted(value) -> str:
    """Render a post date. Accepts Unix epoch (int/float) or YYYYMMDD string."""
    if value is None:
        return ""
    s = str(value)
    if s.isdigit() and len(s) == 8:
        try:
            return datetime.strptime(s, "%Y%m%d").strftime("%Y-%m-%d")
        except ValueError:
            pass
    try:
        ts = float(value)
        return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
    except (TypeError, ValueError, OSError):
        return s


def _format_duration(seconds) -> str:
    try:
        s = int(seconds)
    except (TypeError, ValueError):
        return str(seconds)
    h, m, sec = s // 3600, (s % 3600) // 60, s % 60
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"


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

    if include_warnings:
        if fact_check_result and fact_check_result.get("disputed_claims"):
            parts.append(_fact_check_callout(fact_check_result))
        if location_check_result and location_check_result.get("location_disputed"):
            parts.append(_location_callout(location_check_result))

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
    author = content.author or "unknown"
    return f"""---
title: "{title}"
source_url: "{content.url}"
platform: {content.platform}
saved_date: {saved_date}
author: "{author}"
tags:
{tag_lines}
type: save
---"""


def _source_line(content: ExtractedContent) -> str:
    """Italic attribution line placed at the top of the note body."""
    m = content.metadata or {}
    parts = []
    platform = content.platform

    if platform == "reddit":
        sub = m.get("subreddit", "")
        if sub:
            parts.append(f"[r/{sub}](https://reddit.com/r/{sub})")
        if content.author:
            handle = content.author.split("/")[-1]
            parts.append(f"[{content.author}](https://reddit.com/u/{handle})")
        posted = _format_posted(m["created_utc"]) if m.get("created_utc") else None
    elif platform == "youtube":
        channel = content.author
        cid = m.get("channel_id")
        if channel and cid:
            parts.append(f"[{channel}](https://youtube.com/channel/{cid})")
        elif channel:
            parts.append(channel)
        posted = _format_posted(m["upload_date"]) if m.get("upload_date") else None
    else:
        try:
            domain = urllib.parse.urlparse(content.url).netloc or platform
        except Exception:
            domain = platform
        parts.append(f"[{domain}]({content.url})")
        raw_date = m.get("upload_date") or m.get("created_utc")
        posted = _format_posted(raw_date) if raw_date else None

    if posted:
        parts.append(posted)

    # When a non-YouTube post links an embedded YouTube video, credit the channel
    yt_ch = m.get("youtube_channel")
    if yt_ch and platform != "youtube":
        yt_cid = m.get("youtube_channel_id", "")
        yt_link = f"[{yt_ch}](https://youtube.com/channel/{yt_cid})" if yt_cid else yt_ch
        parts.append(f"YouTube: {yt_link}")

    return f"*{' · '.join(parts)}*\n" if parts else ""


def _summary_section(ai_result: dict) -> str:
    s = ai_result.get("summary", "")
    return f"> [!abstract] Summary\n> {s}\n" if s else ""


def _takeaways_section(ai_result: dict) -> str:
    items = ai_result.get("key_takeaways") or []
    if not items:
        return ""
    bullets = "\n".join(f"> - {t}" for t in items)
    return f"> [!tip] Key Takeaways\n{bullets}\n"


def _author_link(content: ExtractedContent) -> str:
    a = content.author or ""
    if not a:
        return ""
    m = content.metadata or {}
    if content.platform == "reddit":
        handle = a.split("/")[-1]
        return f"[{a}](https://reddit.com/u/{handle})"
    if content.platform == "youtube" and m.get("channel_id"):
        return f"[{a}](https://youtube.com/channel/{m['channel_id']})"
    return a


def _metadata_section(content: ExtractedContent, saved_date: str) -> str:
    lines = [f"> - **Platform:** {content.platform}"]
    author = _author_link(content)
    if author:
        lines.append(f"> - **Author:** {author}")
    m = content.metadata or {}
    if m.get("subreddit"):
        sub = m["subreddit"]
        lines.append(f"> - **Subreddit:** [r/{sub}](https://reddit.com/r/{sub})")
    if m.get("upload_date"):
        lines.append(f"> - **Posted:** {_format_posted(m['upload_date'])}")
    elif m.get("created_utc"):
        lines.append(f"> - **Posted:** {_format_posted(m['created_utc'])}")
    if m.get("view_count"):
        lines.append(f"> - **Views:** {m['view_count']:,}")
    if m.get("score"):
        lines.append(f"> - **Score:** {m['score']:,}")
    if m.get("youtube_url"):
        lines.append(f"> - **Video:** [YouTube]({m['youtube_url']})")
        yt_ch = m.get("youtube_channel")
        if yt_ch:
            yt_cid = m.get("youtube_channel_id", "")
            ch_link = f"[{yt_ch}](https://youtube.com/channel/{yt_cid})" if yt_cid else yt_ch
            lines.append(f"> - **YouTube Channel:** {ch_link}")
        if m.get("youtube_upload_date"):
            lines.append(f"> - **YouTube Posted:** {_format_posted(m['youtube_upload_date'])}")
        yt_stats = []
        if m.get("youtube_views") is not None:
            yt_stats.append(f"{m['youtube_views']:,} views")
        if m.get("youtube_likes") is not None:
            yt_stats.append(f"{m['youtube_likes']:,} likes")
        if m.get("youtube_duration"):
            yt_stats.append(_format_duration(m["youtube_duration"]))
        if yt_stats:
            lines.append(f"> - **YouTube Stats:** {' · '.join(yt_stats)}")
    lines.append(f"> - **Saved:** {saved_date}")
    return "> [!info]- Sources & Metadata\n" + "\n".join(lines) + "\n"


def _media_embeds(media_paths: list[str]) -> str:
    if not media_paths:
        return ""
    blocks = [f"```EmbedRelativeTo\nmedia://{p}\n```" for p in media_paths]
    return "\n\n".join(blocks) + "\n"


def _paragraphize(text: str, sentences_per: int = 4) -> list[str]:
    text = " ".join(text.split())
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
    m = content.metadata or {}
    header = f"> **{content.platform} — {content.author or 'Unknown'}**"
    if m.get("score"):
        header += f" — {m['score']:,} upvotes"
    quoted = "\n".join(f"> {l}" for l in content.body_text[:4000].splitlines())
    return f"> [!quote]- Original Post\n{header}\n>\n{quoted}\n"


def _render_comment_at_depth(c: dict, depth: int) -> str:
    """Render one comment at a given blockquote depth (1 = >, 2 = >>, …)."""
    prefix = "> " * depth
    author = c.get("author", "[deleted]")
    op_label = (
        ' <span style="background-color:#0079d3;color:#ffffff;font-weight:bold;'
        'padding:0 5px;border-radius:3px;">OP</span>'
        if c.get("is_op") else ""
    )
    permalink = c.get("permalink", "")
    author_str = f"[u/{author}]({permalink})" if permalink else f"u/{author}"
    header = f"{prefix}**{author_str}**{op_label} ({c.get('score', 0)} ↑)"
    text = c.get("text", "")[:800]
    body = "\n".join(f"{prefix}{line}" for line in text.splitlines())
    return f"{header}\n{body}" if body else header


def _render_comment_block(c: dict) -> str:
    """Render a comment entry. Thread-context entries nest ancestors at increasing depth."""
    thread = c.get("thread_context")
    if thread:
        # Render each ancestor at increasing depth, then the OP reply deepest
        parts = [_render_comment_at_depth(a, i + 1) for i, a in enumerate(thread)]
        parts.append(_render_comment_at_depth(c, len(thread) + 1))
        return "\n".join(parts)
    return _render_comment_at_depth(c, 1)


def _comments_section(content: ExtractedContent) -> str:
    if not content.top_comments:
        return ""
    blocks = [_render_comment_block(c) for c in content.top_comments]
    inner = "\n>\n".join(blocks)
    return f"> [!example]- Top Comments\n{inner}\n"


def _video_description_block(content: ExtractedContent) -> str:
    desc = (content.metadata or {}).get("youtube_description")
    if not desc:
        return ""
    channel = (content.metadata or {}).get("youtube_channel", "YouTube")
    quoted = "\n".join(f"> {l}" for l in desc[:6000].splitlines())
    return f"> [!quote]- Video Description (from {channel} on YouTube)\n{quoted}\n"


def _paywall_warning(content: ExtractedContent) -> str:
    if (content.metadata or {}).get("possible_paywall"):
        return "> [!warning] Content may be paywalled — text extraction may be incomplete\n"
    return ""


def _no_media_warning(media_paths: list[str]) -> str:
    if not media_paths:
        return "> [!warning] Media file unavailable\n"
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
    parts = [
        _source_line(content),
        f"![{ai_result.get('title', '')}]({content.url})\n",
        _chapters_block(content),
        _summary_section(ai_result),
        _takeaways_section(ai_result),
        _transcript_block(transcript, collapse),
        "---",
        _metadata_section(content, saved_date),
    ]
    return "\n".join(p for p in parts if p)


def _render_reddit_text(ai_result, content, media_paths, transcript, collapse):
    saved_date = date.today().strftime("%Y-%m-%d")
    parts = [
        _source_line(content),
        _summary_section(ai_result),
        _takeaways_section(ai_result),
        _body_quote(content),
        _comments_section(content),
        "---",
        _metadata_section(content, saved_date),
    ]
    return "\n".join(p for p in parts if p)


def _render_reddit_gallery(ai_result, content, media_paths, transcript, collapse):
    saved_date = date.today().strftime("%Y-%m-%d")
    parts = [
        _source_line(content),
        _media_embeds(media_paths),
        _no_media_warning(media_paths),
        _summary_section(ai_result),
        _video_description_block(content),
        _body_quote(content),
        _comments_section(content),
        "---",
        _metadata_section(content, saved_date),
    ]
    return "\n".join(p for p in parts if p)


def _render_reddit_video(ai_result, content, media_paths, transcript, collapse):
    saved_date = date.today().strftime("%Y-%m-%d")
    parts = [
        _source_line(content),
        _media_embeds(media_paths),
        _no_media_warning(media_paths),
        _chapters_block(content),
        _summary_section(ai_result),
        _takeaways_section(ai_result),
        _video_description_block(content),
        _transcript_block(transcript, collapse),
        _body_quote(content),
        _comments_section(content),
        "---",
        _metadata_section(content, saved_date),
    ]
    return "\n".join(p for p in parts if p)


def _render_instagram_reel(ai_result, content, media_paths, transcript, collapse):
    saved_date = date.today().strftime("%Y-%m-%d")
    caption = content.body_text or content.captions or ""
    parts = [
        _source_line(content),
        _media_embeds(media_paths),
        _no_media_warning(media_paths),
        _transcript_block(transcript, collapse),
    ]
    if caption:
        quoted = "\n".join(f"> {l}" for l in caption[:2000].splitlines())
        parts.append(f"> [!quote]- Caption\n{quoted}\n")
    parts += [
        _summary_section(ai_result),
        "---",
        _metadata_section(content, saved_date),
    ]
    return "\n".join(p for p in parts if p)


def _render_instagram_post(ai_result, content, media_paths, transcript, collapse):
    saved_date = date.today().strftime("%Y-%m-%d")
    caption = content.body_text or ""
    parts = [
        _source_line(content),
        _media_embeds(media_paths),
        _no_media_warning(media_paths),
    ]
    if caption:
        quoted = "\n".join(f"> {l}" for l in caption[:2000].splitlines())
        parts.append(f"> [!quote]- Caption\n{quoted}\n")
    parts += [
        _summary_section(ai_result),
        "---",
        _metadata_section(content, saved_date),
    ]
    return "\n".join(p for p in parts if p)


def _render_tiktok_video(ai_result, content, media_paths, transcript, collapse):
    saved_date = date.today().strftime("%Y-%m-%d")
    caption = content.body_text or ""
    parts = [
        _source_line(content),
        _media_embeds(media_paths),
        _no_media_warning(media_paths),
        _transcript_block(transcript, collapse),
    ]
    if caption:
        quoted = "\n".join(f"> {l}" for l in caption[:2000].splitlines())
        parts.append(f"> [!quote]- Caption\n{quoted}\n")
    parts += [
        _summary_section(ai_result),
        "---",
        _metadata_section(content, saved_date),
    ]
    return "\n".join(p for p in parts if p)


def _render_facebook_video(ai_result, content, media_paths, transcript, collapse):
    saved_date = date.today().strftime("%Y-%m-%d")
    caption = content.body_text or ""
    parts = [
        _source_line(content),
        _media_embeds(media_paths),
        _no_media_warning(media_paths),
        _transcript_block(transcript, collapse),
    ]
    if caption:
        quoted = "\n".join(f"> {l}" for l in caption[:2000].splitlines())
        parts.append(f"> [!quote]- Caption\n{quoted}\n")
    parts += [
        _summary_section(ai_result),
        "---",
        _metadata_section(content, saved_date),
    ]
    return "\n".join(p for p in parts if p)


def _render_facebook_post(ai_result, content, media_paths, transcript, collapse):
    saved_date = date.today().strftime("%Y-%m-%d")
    parts = [
        _source_line(content),
        _media_embeds(media_paths),
        _summary_section(ai_result),
        _body_quote(content),
        "---",
        _metadata_section(content, saved_date),
    ]
    return "\n".join(p for p in parts if p)


def _render_web_recipe(ai_result, content, media_paths, transcript, collapse):
    saved_date = date.today().strftime("%Y-%m-%d")
    hero = media_paths[:1]
    parts = [
        _source_line(content),
        _media_embeds(hero),
        _summary_section(ai_result),
        "## Ingredients\n*(See original content below)*\n",
        "## Instructions\n*(See original content below)*\n",
        _paywall_warning(content),
        _body_quote(content),
        "---",
        _metadata_section(content, saved_date),
    ]
    return "\n".join(p for p in parts if p)


def _render_web_travel(ai_result, content, media_paths, transcript, collapse):
    saved_date = date.today().strftime("%Y-%m-%d")
    parts = [
        _source_line(content),
        _media_embeds(media_paths[:4]),
        _summary_section(ai_result),
        _takeaways_section(ai_result),
        "## Key Details\n*(costs, tips, and logistics from original content)*\n",
        _paywall_warning(content),
        _body_quote(content),
        "---",
        _metadata_section(content, saved_date),
    ]
    return "\n".join(p for p in parts if p)


def _render_web_article(ai_result, content, media_paths, transcript, collapse):
    saved_date = date.today().strftime("%Y-%m-%d")
    hero = media_paths[:1]
    parts = [
        _source_line(content),
        _media_embeds(hero),
        _summary_section(ai_result),
        _takeaways_section(ai_result),
        _paywall_warning(content),
        _body_quote(content),
        "---",
        _metadata_section(content, saved_date),
    ]
    return "\n".join(p for p in parts if p)


def _render_web_generic(ai_result, content, media_paths, transcript, collapse):
    saved_date = date.today().strftime("%Y-%m-%d")
    hero = media_paths[:1]
    parts = [
        _source_line(content),
        _media_embeds(hero),
        _summary_section(ai_result),
        _takeaways_section(ai_result),
        _paywall_warning(content),
        _body_quote(content),
        "---",
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
    # Legacy/fallback aliases
    "youtube":         _render_youtube_video,
    "recipe":          _render_web_recipe,
    "travel":          _render_web_travel,
    "article":         _render_web_article,
    "generic":         _render_web_generic,
}
