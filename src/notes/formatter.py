import os
from datetime import date

from src.extractors.base import ExtractedContent

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


def _metadata_section(content: ExtractedContent, saved_date: str) -> str:
    lines = [f"- **Platform:** {content.platform}"]
    if content.author:
        lines.append(f"- **Author:** {content.author}")
    m = content.metadata or {}
    if m.get("subreddit"):
        lines.append(f"- **Subreddit:** r/{m['subreddit']}")
    if m.get("upload_date"):
        lines.append(f"- **Posted:** {m['upload_date']}")
    elif m.get("created_utc"):
        lines.append(f"- **Posted:** {m['created_utc']}")
    if m.get("view_count"):
        lines.append(f"- **Views:** {m['view_count']:,}")
    if m.get("score"):
        lines.append(f"- **Score:** {m['score']}")
    lines.append(f"- **Saved:** {saved_date}")
    return "## Metadata\n" + "\n".join(lines) + "\n"


def _media_embeds(media_paths: list[str]) -> str:
    if not media_paths:
        return ""
    return "\n".join(f"![[{p}]]" for p in media_paths) + "\n"


def _transcript_block(transcript: str, collapse: bool) -> str:
    if not transcript:
        return ""
    safe = transcript[:8000].replace("\n", "\n> ")
    if collapse:
        return f"> [!note]- Full Transcript\n> {safe}\n"
    return f"## Full Transcript\n{transcript[:8000]}\n"


def _body_quote(content: ExtractedContent) -> str:
    if not content.body_text:
        return ""
    author_line = f"**{content.platform} — {content.author or 'Unknown'}**"
    m = content.metadata or {}
    if m.get("score"):
        author_line += f" — {m['score']:,} upvotes"
    quoted = "\n".join(f"> {l}" for l in content.body_text[:4000].splitlines())
    return f"## Original Content\n> {author_line}\n>\n{quoted}\n"


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

def _render_youtube_video(ai_result, content, media_paths, transcript, collapse):
    saved_date = date.today().strftime("%Y-%m-%d")
    parts = []
    parts.append(f"![{ai_result.get('title', '')}]({content.url})\n")

    if content.chapters:
        vid_id = (content.metadata or {}).get("video_id", "")
        ch_lines = []
        for ch in content.chapters:
            secs = ch.get("seconds", 0)
            link = f"https://youtube.com/watch?v={vid_id}&t={secs}" if vid_id else content.url
            ch_lines.append(f"> - [**{ch['time_str']}**]({link}) {ch['title']}")
        parts.append("> [!abstract]- Chapters\n" + "\n".join(ch_lines) + "\n")

    parts.append(_summary_section(ai_result))
    parts.append(_takeaways_section(ai_result))
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
        _transcript_block(transcript, collapse),
        _summary_section(ai_result),
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
