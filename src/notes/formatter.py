import re
import urllib.parse
from datetime import date, datetime, timezone

from src.extractors.base import ExtractedContent
from src.utils.units import convert_measurements


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


def _url_safe_handle(value) -> str:
    """Return a clean @handle for a profile URL, or '' if the value isn't a usable
    handle. Display names (with spaces) and placeholders are rejected so we never
    emit a broken URL like https://instagram.com/Rest In Pizza."""
    if not value:
        return ""
    h = str(value).strip().lstrip("@")
    if not h or h.lower() == "unknown" or any(c.isspace() for c in h) or h.isdigit():
        return ""
    return h


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

    # A TikTok photo/slideshow post has still images and a caption but no video or transcript.
    # The extractor marks it with is_photo_post; if the model still tagged it as a video type,
    # render it as an image post (embeds + caption + summary, no empty transcript block).
    if note_type in ("tiktok_video", "tiktok") and (content.metadata or {}).get("is_photo_post"):
        note_type = "tiktok_photo"

    parts = [_frontmatter(ai_result, content, saved_date)]

    # Blank spacer line so that when the note opens in Live Preview, the cursor
    # lands on this empty line instead of inside the first embed/code block — which
    # would otherwise show raw "```EmbedRelativeTo ..." markup until you click away.
    parts.append("")

    if include_warnings:
        if fact_check_result and (
            fact_check_result.get("disputed_claims")
            or any(f.get("severity") == "warning" for f in (fact_check_result.get("flags") or []) if isinstance(f, dict))
        ):
            parts.append(_fact_check_callout(fact_check_result))
        if location_check_result and (
            location_check_result.get("location_disputed") or location_check_result.get("advisories")
        ):
            parts.append(_location_callout(location_check_result))

    renderer = _RENDERERS.get(note_type, _render_web_generic)
    body = renderer(ai_result, content, media_paths, transcript, collapse_transcript)

    # Safety net: never silently drop downloaded media. If the chosen template
    # rendered no embed (e.g. a single-image post classified as reddit_text),
    # embed the media at the top of the body so it always appears in the note.
    if media_paths and "EmbedRelativeTo" not in body:
        embeds = _media_embeds(media_paths)
        if embeds:
            body = embeds + "\n" + body

    # English translation of non-English content. Caption-bearing renderers place it inline just
    # above the caption; every other note type gets it at the note top. The `not in body` guard
    # avoids a duplicate when the renderer already emitted it.
    translation_block = _translation_section(ai_result)
    if translation_block and translation_block not in body:
        parts.append(translation_block)

    # Inject supplemental sections (image text + fact-check + location) before the --- separator.
    image_text = (ai_result.get("image_text") or "").strip()
    fc_inline = fact_check_result or (
        ai_result.get("_fact_check")
        if isinstance(ai_result.get("_fact_check"), dict) else None
    )
    lc_inline = location_check_result or (
        ai_result.get("_location_check")
        if isinstance(ai_result.get("_location_check"), dict) else None
    )
    inserts = []
    # web_recipe renderer handles recipe, image_text, and transcript itself. For all other note
    # types, inject the styled '🍽️ Recipe' section first (always its own section), then the
    # post's own '📄 Text from Images' OCR, and finally the off-site bio / full-page reproduction
    # LAST — it's external blog content, so it belongs below the reel's own extracted material.
    recipe_type = note_type in ("web_recipe", "recipe")
    has_recipe_fields = bool(
        ai_result.get("recipe_ingredients") or ai_result.get("recipe_instructions")
        or ai_result.get("recipe_ingredient_groups")
    )
    if not recipe_type and has_recipe_fields:
        inserts.append(_recipe_section(ai_result))
    if image_text and not recipe_type:
        inserts.append(_image_text_section(image_text))
    bio_section = "" if recipe_type else _bio_recipe_section(ai_result, content)
    if bio_section:
        inserts.append(bio_section)
    if fc_inline:
        sec = _fact_check_note_section(fc_inline)
        if sec:
            inserts.append(sec)
    if lc_inline and (lc_inline.get("location_disputed") or lc_inline.get("advisories")):
        inserts.append(_location_callout(lc_inline))
    if inserts:
        sep = "\n---\n"
        if sep in body:
            # rsplit (last separator), not split (first): a web-article body carries the full
            # article Markdown, which routinely contains its own '---' horizontal rules. The
            # separator we want is the one before the trailing 'Sources & Metadata' block —
            # always the LAST '\n---\n' in a rendered body — so the recipe/fact-check/location
            # sections land just above Metadata instead of mid-article after the first HR.
            pre, post = body.rsplit(sep, 1)
            body = pre + "\n" + "\n".join(inserts) + sep + post
        else:
            body = body + "\n" + "\n".join(inserts)

    parts.append(body)
    return "\n".join(parts)


# ─────────────────────────────────────────────────────────
# Shared components
# ─────────────────────────────────────────────────────────

def _sanitize_yaml_str(value: str) -> str:
    """Collapse newlines/tabs to a space so YAML double-quoted strings stay on one line."""
    return re.sub(r'[\r\n\t]+', ' ', value).strip()


def _frontmatter(ai_result: dict, content: ExtractedContent, saved_date: str) -> str:
    tags = ai_result.get("tags") or []
    title = _sanitize_yaml_str(ai_result.get("title", "")).replace('"', "'")
    author = content.author or "unknown"
    m = content.metadata or {}

    lines = ["---", f'title: "{title}"']

    post_title = _sanitize_yaml_str(content.title or "").replace('"', "'")
    # Suppress post_title when it equals the source URL (happens when yt-dlp/extractor
    # couldn't get a real title and fell back to using the URL as the title).
    if post_title and post_title != content.url and post_title != content.url.replace('"', "'"):
        lines.append(f'post_title: "{post_title}"')

    lines += [
        f'source_url: "{content.url}"',
        f"platform: {content.platform}",
        f"saved_date: {saved_date}",
        f'author: "{author}"',
    ]

    if content.platform == "reddit":
        handle = author.split("/")[-1] if "/" in author else author
        if handle and handle not in ("unknown", ""):
            lines.append(f'author_url: "https://reddit.com/u/{handle}"')
        sub = m.get("subreddit", "")
        if sub:
            lines.append(f"subreddit: r/{sub}")
            lines.append(f'subreddit_url: "https://reddit.com/r/{sub}"')
        posted = _format_posted(m["created_utc"]) if m.get("created_utc") else ""
        if posted:
            lines.append(f"posted: {posted}")
        yt_ch = m.get("youtube_channel")
        yt_cid = m.get("youtube_channel_id", "")
        if yt_ch:
            lines.append(f'youtube_channel: "{yt_ch}"')
            if yt_cid:
                lines.append(f'youtube_channel_url: "https://youtube.com/channel/{yt_cid}"')
    elif content.platform == "youtube":
        cid = m.get("channel_id")
        if cid:
            lines.append(f'channel_url: "https://youtube.com/channel/{cid}"')
        posted = _format_posted(m["upload_date"]) if m.get("upload_date") else ""
        if posted:
            lines.append(f"posted: {posted}")
    elif content.platform == "instagram":
        handle = _url_safe_handle(m.get("author_handle") or author)
        if handle:
            lines.append(f'author_url: "https://instagram.com/{handle}"')
        raw_date = m.get("upload_date") or m.get("created_utc")
        if raw_date:
            posted = _format_posted(raw_date)
            if posted:
                lines.append(f"posted: {posted}")
    elif content.platform == "tiktok":
        handle = _url_safe_handle(m.get("author_handle") or author)
        if handle:
            lines.append(f'author_url: "https://tiktok.com/@{handle}"')
        raw_date = m.get("upload_date") or m.get("created_utc")
        if raw_date:
            posted = _format_posted(raw_date)
            if posted:
                lines.append(f"posted: {posted}")
    else:
        try:
            domain = urllib.parse.urlparse(content.url).netloc
        except Exception:
            domain = ""
        if domain:
            lines.append(f"domain: {domain}")
        raw_date = m.get("upload_date") or m.get("created_utc")
        if raw_date:
            posted = _format_posted(raw_date)
            if posted:
                lines.append(f"posted: {posted}")

    if tags:
        lines.append("tags:")
        # Quote each tag so YAML never coerces a numeric-looking tag (e.g. "225",
        # "2026") into an int — Obsidian flags non-string list items as
        # "Type mismatch, expected Tags".
        for t in tags:
            lines.append(f'  - "{str(t).replace(chr(34), chr(39))}"')
    else:
        lines.append("tags: []")

    lines += ["type: SAVES.app", "---"]
    return "\n".join(lines)


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


def _translation_section(ai_result: dict) -> str:
    """Expanded-by-default English-translation callout, shown only when the source content is in
    another language (Claude fills `translation` and leaves it null for English). Caption-bearing
    renderers place it just above the caption; every other note type gets it at the note top."""
    translation = (ai_result.get("translation") or "").strip()
    if not translation:
        return ""
    lang = (ai_result.get("source_language") or "").strip()
    suffix = f" (from {lang})" if lang and lang.lower() != "english" else ""
    quoted = "\n".join(f"> {line}" for line in translation.splitlines())
    return f"> [!info]+ 🌐 English Translation{suffix}\n{quoted}\n"


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
    if content.platform == "instagram":
        handle = _url_safe_handle(m.get("author_handle") or a)
        return f"[{a}](https://instagram.com/{handle})" if handle else a
    if content.platform == "tiktok":
        handle = _url_safe_handle(m.get("author_handle") or a)
        return f"[{a}](https://tiktok.com/@{handle})" if handle else a
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
    recipe_src = m.get("followed_recipe_url") or m.get("followed_recipe_hint")
    if recipe_src:
        host = urllib.parse.urlparse(recipe_src).netloc.lstrip("www.") or "source"
        label = "Recipe source" if m.get("followed_recipe_url") else "Recipe likely at"
        lines.append(f"> - **{label}:** [{host}]({recipe_src})")
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
    quoted = "\n".join(f"> {line}" for line in content.body_text[:20000].splitlines())
    return f"> [!quote] Original Post\n{header}\n>\n{quoted}\n"


def _render_comment_at_depth(c: dict, depth: int) -> str:
    """Render one comment at a given blockquote depth (1 = >, 2 = >>, …)."""
    prefix = "> " * depth
    author = c.get("author", "[deleted]")
    op_label = ' <span class="saves-op-badge">OP</span>' if c.get("is_op") else ""
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
    quoted = "\n".join(f"> {line}" for line in desc[:6000].splitlines())
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
            lines.append(f">   **Source:** {_source_link(claim['source'])}")
    for fl in fc.get("flags", []):
        if isinstance(fl, dict) and fl.get("severity") == "warning":
            lines.append(f"> - **{_humanize_label(fl.get('type', 'flag'))}:** {fl.get('detail', '')}")
            if fl.get("source"):
                lines.append(f">   **Source:** {_source_link(fl['source'])}")
    return "\n".join(lines) + "\n"


def _source_link(value) -> str:
    """Render a source as a clickable markdown link when it's a URL, else plain text."""
    s = str(value).strip()
    if s.startswith("http://") or s.startswith("https://"):
        try:
            domain = urllib.parse.urlparse(s).netloc or s
        except Exception:
            domain = s
        return f"[{domain}]({s})"
    return s


def _claim_with_source(claim) -> tuple[str, str]:
    """Normalize a claim entry (string or {claim, source}) → (text, rendered_source)."""
    if isinstance(claim, dict):
        return claim.get("claim", ""), (_source_link(claim["source"]) if claim.get("source") else "")
    return str(claim), ""


def _humanize_label(value) -> str:
    """media_authenticity → Media Authenticity."""
    return str(value).replace("_", " ").replace("-", " ").strip().title() or "Flag"


def _fact_check_note_section(fc: dict) -> str:
    """Inline fact-check section written into every note that triggered a check.

    Always renders *something* when a fact-check ran (fc is a non-empty dict), so the
    note gives positive confirmation the check happened — even when no claims were
    disputed. Source URLs render as clickable links. `flags` carry the cross-cutting
    findings (media authenticity, recycled content, source credibility, conflict of
    interest, tax validity, scam signals, etc.)."""
    if not fc:
        return ""
    disputed = fc.get("disputed_claims") or []
    verified = fc.get("verified_claims") or []
    flags = fc.get("flags") or []
    sources = fc.get("sources") or []

    warning_flags = [f for f in flags if (f.get("severity") if isinstance(f, dict) else "") == "warning"]
    has_warning = bool(disputed) or bool(warning_flags)
    callout = "[!warning]-" if has_warning else "[!info]-"
    lines = [f"> {callout} Fact Check"]

    if fc.get("opinion_only") and not disputed and not verified:
        lines.append("> This content is opinion/analysis — no specific factual claims were verified.")

    if disputed:
        lines.append("> **Disputed Claims:**")
        for claim in disputed:
            text, src = _claim_with_source(claim)
            reality = claim.get("reality", "") if isinstance(claim, dict) else ""
            lines.append(f"> - **Claimed:** {text}")
            if reality:
                lines.append(f">   **Reality:** {reality}")
            if src:
                lines.append(f">   **Source:** {src}")

    if verified:
        lines.append("> **Verified Claims:**")
        for v in verified:
            text, src = _claim_with_source(v)
            lines.append(f"> - {text}" + (f" — {src}" if src else ""))

    if flags:
        lines.append("> **Flags:**")
        for fl in flags:
            if not isinstance(fl, dict):
                lines.append(f"> - {fl}")
                continue
            label = _humanize_label(fl.get("type") or fl.get("label") or "flag")
            mark = "⚠️ " if fl.get("severity") == "warning" else ""
            detail = fl.get("detail", "")
            lines.append(f"> - {mark}**{label}:** {detail}")
            if fl.get("source"):
                lines.append(f">   **Source:** {_source_link(fl['source'])}")

    if not disputed and not verified and not flags:
        lines.append("> No specific factual claims were flagged as disputed.")

    if sources:
        lines.append("> **Sources:**")
        for s in sources:
            lines.append(f"> - {_source_link(s)}")
    return "\n".join(lines) + "\n"


def _image_text_section(image_text: str) -> str:
    """Text extracted from carousel slides where the image content IS the text."""
    if not image_text or not image_text.strip():
        return ""
    quoted = "\n".join(f"> {line}" for line in image_text.strip().splitlines())
    return f"> [!quote] Text from Images\n{quoted}\n"


def _location_callout(lc: dict) -> str:
    disputed = lc.get("location_disputed")
    title = "Location Dispute Flagged" if disputed else "Travel Advisory"
    lines = [f"> [!warning] {title}"]
    if disputed:
        stated = lc.get("stated_location", "unknown")
        actual = lc.get("claimed_actual_location", "unknown")
        confidence = lc.get("confidence", "")
        lines.append(f"> - **Stated:** {stated}")
        lines.append(f"> - **Claimed actual:** {actual} ({confidence} confidence)")
        if lc.get("evidence"):
            lines.append(f"> - **Evidence:** {lc['evidence']}")
    for adv in lc.get("advisories", []) or []:
        if isinstance(adv, dict):
            lines.append(f"> - **{_humanize_label(adv.get('type', 'advisory'))}:** {adv.get('detail', '')}")
        else:
            lines.append(f"> - {adv}")
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
    # Use yt-dlp's canonical watch?v= URL — Obsidian only embeds YouTube in that form,
    # not /shorts/ URLs. canonical_url is stored by the extractor for every video.
    embed_url = (content.metadata or {}).get("canonical_url") or content.url
    parts = [
        f"![{ai_result.get('title', '')}]({embed_url})\n",
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
        _media_embeds(media_paths),
        _no_media_warning(media_paths),
        _translation_section(ai_result),
    ]
    if caption:
        quoted = "\n".join(f"> {line}" for line in caption[:8000].splitlines())
        parts.append(f"> [!quote] Caption\n{quoted}\n")
    parts += [
        _transcript_block(transcript, collapse),
        _summary_section(ai_result),
        "---",
        _metadata_section(content, saved_date),
    ]
    return "\n".join(p for p in parts if p)


def _render_instagram_post(ai_result, content, media_paths, transcript, collapse):
    saved_date = date.today().strftime("%Y-%m-%d")
    caption = content.body_text or ""
    parts = [
        _media_embeds(media_paths),
        _no_media_warning(media_paths),
        _translation_section(ai_result),
    ]
    if caption:
        quoted = "\n".join(f"> {line}" for line in caption[:8000].splitlines())
        parts.append(f"> [!quote] Caption\n{quoted}\n")
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
        _media_embeds(media_paths),
        _no_media_warning(media_paths),
        _translation_section(ai_result),
    ]
    if caption:
        quoted = "\n".join(f"> {line}" for line in caption[:8000].splitlines())
        parts.append(f"> [!quote] Caption\n{quoted}\n")
    parts += [
        _transcript_block(transcript, collapse),
        _summary_section(ai_result),
        "---",
        _metadata_section(content, saved_date),
    ]
    return "\n".join(p for p in parts if p)


def _render_facebook_video(ai_result, content, media_paths, transcript, collapse):
    saved_date = date.today().strftime("%Y-%m-%d")
    caption = content.body_text or ""
    parts = [
        _media_embeds(media_paths),
        _no_media_warning(media_paths),
        _translation_section(ai_result),
    ]
    if caption:
        quoted = "\n".join(f"> {line}" for line in caption[:8000].splitlines())
        parts.append(f"> [!quote] Caption\n{quoted}\n")
    parts += [
        _transcript_block(transcript, collapse),
        _summary_section(ai_result),
        "---",
        _metadata_section(content, saved_date),
    ]
    return "\n".join(p for p in parts if p)


def _render_facebook_post(ai_result, content, media_paths, transcript, collapse):
    saved_date = date.today().strftime("%Y-%m-%d")
    parts = [
        _media_embeds(media_paths),
        _summary_section(ai_result),
        _body_quote(content),
        "---",
        _metadata_section(content, saved_date),
    ]
    return "\n".join(p for p in parts if p)


# FDA reference Daily Values (adult, 2,000-kcal diet). Used to compute %DV deterministically
# from the estimated amounts so the label doesn't rely on the model for the arithmetic.
_DAILY_VALUES = {
    "total_fat": (78.0, "g"),
    "saturated_fat": (20.0, "g"),
    "cholesterol": (300.0, "mg"),
    "sodium": (2300.0, "mg"),
    "total_carbs": (275.0, "g"),
    "dietary_fiber": (28.0, "g"),
    "added_sugars": (50.0, "g"),
    "potassium": (4700.0, "mg"),
}
_MASS_IN_GRAMS = {"g": 1.0, "mg": 1e-3, "mcg": 1e-6, "µg": 1e-6, "ug": 1e-6, "kg": 1000.0}


def _parse_amount(value) -> tuple[float | None, str]:
    """Pull a leading number and unit out of a string like '18 g', '320 kcal', '2,300mg'."""
    if value is None:
        return None, ""
    m = re.match(r"\s*([\d.,]+)\s*([a-zA-Zµ%]*)", str(value))
    if not m:
        return None, ""
    try:
        return float(m.group(1).replace(",", "")), m.group(2).lower()
    except ValueError:
        return None, ""


def _dv_percent(key: str, value) -> str:
    """%DV for a nutrient from its estimated amount, or '' when it can't be computed."""
    ref = _DAILY_VALUES.get(key)
    if not ref:
        return ""
    amount, unit = _parse_amount(value)
    if amount is None:
        return ""
    ref_amount, ref_unit = ref
    if unit in _MASS_IN_GRAMS:
        grams = amount * _MASS_IN_GRAMS[unit]
    elif not unit:  # bare number — assume it's already in the reference unit
        grams = amount * _MASS_IN_GRAMS[ref_unit]
    else:
        return ""
    ref_grams = ref_amount * _MASS_IN_GRAMS[ref_unit]
    if ref_grams <= 0:
        return ""
    return f"{round(grams / ref_grams * 100)}%"


def _nutrition_label(nutrition: dict | None) -> str:
    """Render an FDA-style Nutrition Facts panel (HTML — Obsidian renders it on desktop and
    mobile) from the per-serving nutrition. Returns '' when no nutrition data is present.
    Amounts come from the model, or from the source's published values where it had them (see
    apply_structured_recipe — those keys are listed in `_source_keys`, which flips the caption
    to a "published + supplemented" disclaimer). %DV is computed here from the FDA references."""
    if not isinstance(nutrition, dict) or not nutrition:
        return ""

    def val(key: str) -> str:
        v = nutrition.get(key)
        return str(v).strip() if v not in (None, "", "null") else ""

    serving_size = val("serving_size")
    servings = val("servings")
    cal_num, _ = _parse_amount(val("calories"))
    calories = str(int(round(cal_num))) if cal_num is not None else (val("calories") or "—")

    def cell(text, *, align="left", bold=False, size=14, indent=0, border="1px solid #000"):
        left_pad = 8 + indent * 16
        weight = "font-weight:bold;" if bold else ""
        return (
            f'<td style="border-top:{border};padding:1px 8px 1px {left_pad}px;'
            f'text-align:{align};font-size:{size}px;{weight}">{text}</td>'
        )

    def nutrient_row(label, key, *, indent=0, bold=False, prefix="", show_dv=True):
        amount = val(key)
        if not amount:
            return ""
        name = f"<b>{label}</b>" if bold else label
        left = f"{prefix}{name} {amount}" if not prefix else f"{prefix} {amount} {label}"
        pct = _dv_percent(key, amount) if show_dv else ""
        return (
            "<tr>"
            + cell(left, indent=indent)
            + cell(pct, align="right", bold=True)
            + "</tr>"
        )

    rows = []
    rows.append(nutrient_row("Total Fat", "total_fat", bold=True))
    rows.append(nutrient_row("Saturated Fat", "saturated_fat", indent=1))
    rows.append(nutrient_row("Trans Fat", "trans_fat", indent=1, show_dv=False))
    rows.append(nutrient_row("Polyunsaturated Fat", "polyunsaturated_fat", indent=1, show_dv=False))
    rows.append(nutrient_row("Monounsaturated Fat", "monounsaturated_fat", indent=1, show_dv=False))
    rows.append(nutrient_row("Cholesterol", "cholesterol", bold=True))
    rows.append(nutrient_row("Sodium", "sodium", bold=True))
    rows.append(nutrient_row("Total Carbohydrate", "total_carbs", bold=True))
    rows.append(nutrient_row("Dietary Fiber", "dietary_fiber", indent=1))
    rows.append(nutrient_row("Total Sugars", "total_sugars", indent=1, show_dv=False))
    rows.append(nutrient_row("Added Sugars", "added_sugars", indent=2, prefix="Includes"))
    rows.append(nutrient_row("Protein", "protein", bold=True, show_dv=False))
    rows.append(nutrient_row("Omega-3", "omega_3", indent=1, show_dv=False))
    rows.append(nutrient_row("Omega-6", "omega_6", indent=1, show_dv=False))
    macro_html = "".join(r for r in rows if r)

    # Bottom section: potassium + any vitamins/minerals, separated by the thick FDA rule.
    micro_rows = []
    pot = val("potassium")
    if pot:
        micro_rows.append(
            "<tr>" + cell(f"Potassium {pot}", border="8px solid #000")
            + cell(_dv_percent("potassium", pot), align="right", bold=True, border="8px solid #000")
            + "</tr>"
        )
    micros = nutrition.get("micros")
    if isinstance(micros, list):
        for i, mic in enumerate(micros):
            if not isinstance(mic, dict):
                continue
            name = str(mic.get("name", "")).strip()
            if not name:
                continue
            amount = str(mic.get("amount", "")).strip()
            dv = str(mic.get("dv", "")).strip()
            # Thick rule above the first bottom-section row when potassium didn't already draw it.
            border = "8px solid #000" if (not micro_rows and i == 0) else "1px solid #000"
            micro_rows.append(
                "<tr>" + cell(f"{name} {amount}".strip(), border=border)
                + cell(dv, align="right", bold=True, border=border) + "</tr>"
            )
    micro_html = "".join(micro_rows)

    servings_line = (
        f'<tr><td colspan="2" style="padding:0 8px;font-size:13px;">{servings} servings per recipe</td></tr>'
        if servings else ""
    )
    serving_size_line = (
        '<tr>'
        f'<td style="padding:0 8px 4px;font-weight:bold;border-bottom:8px solid #000;">Serving size</td>'
        f'<td style="padding:0 8px 4px;text-align:right;font-weight:bold;border-bottom:8px solid #000;">{serving_size}</td>'
        '</tr>'
        if serving_size else
        '<tr><td colspan="2" style="padding:0 8px 4px;border-bottom:8px solid #000;"></td></tr>'
    )

    table = (
        '<table style="border-collapse:collapse;width:100%;max-width:360px;'
        'font-family:Arial,Helvetica,sans-serif;font-size:14px;border:1px solid #000;">'
        '<tr><td colspan="2" style="font-size:28px;font-weight:900;padding:4px 8px 0;">Nutrition Facts</td></tr>'
        + servings_line
        + serving_size_line
        + '<tr><td colspan="2" style="padding:2px 8px 0;font-size:12px;font-weight:bold;">Amount per serving</td></tr>'
        + '<tr>'
        '<td style="padding:0 8px;font-size:26px;font-weight:900;border-bottom:4px solid #000;">Calories</td>'
        f'<td style="padding:0 8px;font-size:26px;font-weight:900;text-align:right;border-bottom:4px solid #000;">{calories}</td>'
        '</tr>'
        + '<tr><td colspan="2" style="padding:1px 8px;text-align:right;font-weight:bold;font-size:12px;border-bottom:1px solid #000;">% Daily Value*</td></tr>'
        + macro_html
        + micro_html
        + '<tr><td colspan="2" style="padding:4px 8px;font-size:11px;border-top:4px solid #000;">'
        '* Percent Daily Values are based on a 2,000 calorie diet.</td></tr>'
        '</table>'
    )

    times = []
    for key, lbl in (("prep_time", "Prep"), ("cook_time", "Cook"), ("total_time", "Total")):
        v = val(key)
        if v:
            times.append(f"{lbl}: {v}")
    times_line = f"\n{' · '.join(times)}\n" if times else ""

    caveat = val("notes")
    caveat_line = f"\n*{caveat}*\n" if caveat else ""

    if nutrition.get("_source_keys"):
        disclaimer = (
            "*📋 Per-serving values published by the source, supplemented with 🤖 AI estimates "
            "for nutrients it didn't list (e.g. omegas, vitamins). Approximate.*"
        )
    else:
        disclaimer = (
            "*🤖 AI estimate from the recipe — approximate, not a verified nutrition analysis.*"
        )
    return (
        "### Nutrition Facts\n\n"
        f"{disclaimer}\n"
        f"{times_line}\n{table}\n{caveat_line}"
    )


def _blockquote(md: str) -> str:
    """Prefix every line so a multi-line Markdown block renders inside an Obsidian callout.
    Blank lines become a bare '>' so the callout keeps flowing."""
    return "\n".join((">" if not line.strip() else f"> {line}") for line in md.splitlines())


def _ingredients_md(ai_result: dict) -> str:
    """Ingredients as Markdown — grouped under their section titles ('For the garlic confit',
    'For the pasta', 'To finish', …) when the structured data carried them, otherwise a single
    flat bulleted list. Preserving these titles is a hard requirement: a recipe's ingredients
    belong to specific components and must not be flattened into one anonymous list."""
    groups = ai_result.get("recipe_ingredient_groups")
    if groups:
        blocks = []
        for g in groups:
            items = g.get("items") or []
            if not items:
                continue
            lines = "\n".join(f"- {convert_measurements(str(i))}" for i in items)
            name = (g.get("name") or "").strip().rstrip(":")
            blocks.append(f"**{name}**\n\n{lines}" if name else lines)
        if blocks:
            return "\n\n".join(blocks)
    flat = ai_result.get("recipe_ingredients") or []
    return "\n".join(f"- {convert_measurements(str(i))}" for i in flat)


def _instructions_md(ai_result: dict) -> str:
    """Method as Markdown — grouped under their section titles when present (numbering restarts
    per named group). Step bold lead-ins (e.g. '**Confit the garlic.** Place the cloves…') are
    preserved from the structured data; that per-step bolding is what makes the method readable
    instead of a wall of numbered prose."""
    groups = ai_result.get("recipe_instruction_groups")
    if groups:
        blocks = []
        for g in groups:
            steps = g.get("steps") or []
            if not steps:
                continue
            lines = "\n".join(
                f"{idx + 1}. {convert_measurements(str(s))}" for idx, s in enumerate(steps)
            )
            name = (g.get("name") or "").strip().rstrip(":")
            blocks.append(f"**{name}**\n\n{lines}" if name else lines)
        if blocks:
            return "\n\n".join(blocks)
    flat = ai_result.get("recipe_instructions") or []
    return "\n".join(f"{idx + 1}. {convert_measurements(str(s))}" for idx, s in enumerate(flat))


def _recipe_inner_md(ai_result: dict) -> str:
    """Recipe content (meta line, grouped ingredients, grouped method, notes) as plain Markdown,
    without a heading or callout wrapper."""
    servings = (ai_result.get("recipe_servings") or "").strip()
    time_str = (ai_result.get("recipe_time") or "").strip()
    notes = (ai_result.get("recipe_notes") or "").strip()

    parts = []
    meta = " · ".join(filter(None, [servings, time_str]))
    if meta:
        parts.append(f"*{meta}*")
    ing = _ingredients_md(ai_result)
    if ing:
        parts.append("**🧺 Ingredients**\n\n" + ing)
    method = _instructions_md(ai_result)
    if method:
        parts.append("**🍳 Method**\n\n" + method)
    if notes:
        parts.append("**📝 Notes**\n\n" + convert_measurements(notes))
    return "\n\n".join(parts)


def _recipe_section(ai_result: dict) -> str:
    """The styled Recipe section: a foldable '🍽️ Recipe' callout (same callout styling as the
    Summary and Caption sections so the section change is obvious at a glance) holding the
    grouped ingredients and method. The AI nutrition label — raw HTML that doesn't render
    cleanly inside a callout — is appended just beneath it."""
    inner = _recipe_inner_md(ai_result)
    if inner:
        callout = "> [!example]+ 🍽️ Recipe\n" + _blockquote(inner) + "\n"
    else:
        callout = "> [!example]+ 🍽️ Recipe\n> *(Ingredients and instructions — see sources below)*\n"
    label = _nutrition_label(ai_result.get("nutrition"))
    if label:
        return callout + "\n" + label + "\n"
    return callout


def _link_label(url: str) -> str:
    """Render a URL as a [host](url) markdown link."""
    try:
        host = urllib.parse.urlparse(url).netloc.lstrip("www.")
    except Exception:
        host = ""
    return f"[{host or url}]({url})"


def has_offsite_recipe_context(content: ExtractedContent) -> bool:
    """True when this post pointed off-site for its recipe (so a bio-link section is warranted)."""
    m = content.metadata or {}
    return bool(m.get("offsite_recipe_detected") or m.get("followed_recipe_url")
                or m.get("followed_recipe_hint"))


def _strip_leading_title(md: str) -> str:
    """Drop the reproduced page's own top-level ``# Title`` — it's redundant with the note's
    frontmatter title and the ``## Full Web Clipping`` header the reproduction sits under. Skips
    any leading hero image / embed block first, since the image localizer prepends the feature
    image (an ``EmbedRelativeTo`` fence) ahead of the title."""
    lines = md.splitlines()
    i, n = 0, len(lines)
    while i < n:
        s = lines[i].strip()
        if not s:
            i += 1
            continue
        if s.startswith("```"):  # a fenced block, e.g. the localized hero EmbedRelativeTo image
            i += 1
            while i < n and not lines[i].strip().startswith("```"):
                i += 1
            i += 1  # consume the closing fence
            continue
        if s.startswith("!["):  # a leading Markdown image
            i += 1
            continue
        if re.match(r"^#\s+\S", lines[i]):  # first real content line is an H1 -> drop it
            return "\n".join(lines[:i] + lines[i + 1:]).lstrip("\n")
        break
    return md


def _demote_headings(md: str, by: int = 1) -> str:
    """Increase the level of every ATX heading by ``by`` (capped at h6) so a reproduced page's
    own headings nest *under* the section header above them instead of competing with it.
    Respects fenced code blocks."""
    out, in_fence = [], False
    for ln in md.splitlines():
        if ln.lstrip().startswith("```"):
            in_fence = not in_fence
            out.append(ln)
            continue
        if not in_fence:
            mt = re.match(r"^(#{1,6})(\s)", ln)
            if mt:
                level = min(len(mt.group(1)) + by, 6)
                ln = "#" * level + ln[len(mt.group(1)):]
        out.append(ln)
    return "\n".join(out)


def _followed_page_reproduction(content: ExtractedContent) -> str:
    """Reproduce the followed off-site page in the note as its own clearly-delineated section —
    a Web-Clipper-style clipping of the page's structured Markdown (faithful headings,
    bold/links/lists) with images already localized into the vault. Set off by a rule and a
    prominent section header, with the page's own headings demoted so they nest under it.
    Empty when there's no page."""
    m = content.metadata or {}
    md = (m.get("followed_recipe_article_markdown") or "").strip()
    if not md:
        return ""
    md = _demote_headings(_strip_leading_title(md), by=1)
    url = m.get("followed_recipe_url") or ""
    host = urllib.parse.urlparse(url).netloc if url else ""
    if host.startswith("www."):
        host = host[4:]
    header = "## 📄 Full Web Clipping" + (f" — {host}" if host else "")
    attribution = (
        f"> [!info] 📎 Reproduced in full from {_link_label(url)} so the recipe survives if the "
        "original post or page is taken down.\n\n"
    ) if url else ""
    return f"---\n\n{header}\n\n{attribution}{md}\n"


def _bio_recipe_section(ai_result: dict, content: ExtractedContent) -> str:
    """Provenance for recipes that live behind a 'link in bio' / 'recipe on my profile'
    pointer: a styled callout crediting the off-site source, followed by a faithful
    reproduction of that page (text + localized images + formatting). The extracted recipe
    itself is rendered as its own styled '🍽️ Recipe' section ABOVE this one, so this section is
    purely the source credit and the full-page copy — never the recipe again."""
    if not has_offsite_recipe_context(content):
        return ""
    m = content.metadata or {}
    url = m.get("followed_recipe_url")
    hint = m.get("followed_recipe_hint")
    page = _followed_page_reproduction(content)
    has_recipe = bool(
        ai_result.get("recipe_ingredients") or ai_result.get("recipe_instructions")
        or ai_result.get("recipe_ingredient_groups")
    )
    title = "🔗 Recipe from Bio / Off-Site Link"

    if url:
        if has_recipe:
            tail = " The full source page is reproduced below." if page else ""
            banner = (
                f"> [!success]+ {title}\n"
                f"> The recipe above was extracted automatically from {_link_label(url)}.{tail}\n"
            )
        elif page:
            banner = (
                f"> [!warning]+ {title}\n"
                f"> I followed the off-site link ({_link_label(url)}) but could not parse a clean "
                "structured recipe from it — the full source page is reproduced below so nothing "
                "is lost.\n"
            )
        else:
            banner = (
                f"> [!warning] {title}\n"
                f"> I followed the off-site link ({_link_label(url)}) but could not find a "
                "structured recipe on that page — open it directly to check.\n"
            )
        return banner + ("\n" + page if page else "")
    if hint:
        return (
            f"> [!warning] {title}\n"
            "> This post points to an off-site recipe, but I could not open it automatically. "
            f"Try the link the poster shared: {_link_label(hint)}\n"
        )
    return (
        f"> [!warning] {title}\n"
        "> This post points to an off-site recipe (\"link in bio\"), but I could not locate or "
        "extract the recipe automatically.\n"
    )


def _render_web_recipe(ai_result, content, media_paths, transcript, collapse):
    # Section order per spec:
    # Media → Summary → Recipe → Caption → Text from Images → Transcript → Sources & Metadata
    saved_date = date.today().strftime("%Y-%m-%d")
    hero = media_paths[:1]

    caption = content.body_text or ""
    caption_section = ""
    if caption:
        quoted = "\n".join(f"> {line}" for line in caption[:8000].splitlines())
        caption_section = f"> [!quote] Caption\n{quoted}\n"

    # Render image_text inline at the recipe-specified position (not via generic inject)
    image_text = (ai_result.get("image_text") or "").strip()
    image_text_section = _image_text_section(image_text) if image_text else ""

    parts = [
        _media_embeds(hero),
        _summary_section(ai_result),
        _recipe_section(ai_result),
        caption_section,
        image_text_section,
        _transcript_block(transcript, collapse),
        _paywall_warning(content),
        "---",
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
        "---",
        _metadata_section(content, saved_date),
    ]
    return "\n".join(p for p in parts if p)


def _article_body(content: ExtractedContent) -> str:
    """Clean Markdown body for web articles (headings, paragraphs, links, inline image
    embeds), as produced by the trafilatura extractor + image localizer. Falls back to the
    blockquoted plain text when no structured Markdown is available."""
    md = (content.metadata or {}).get("article_markdown")
    if md and md.strip():
        return md.strip() + "\n"
    return _body_quote(content)


def _render_web_article(ai_result, content, media_paths, transcript, collapse):
    # Hero embed is intentionally omitted: article images are embedded inline in the body
    # by the localizer. The format_note safety net still embeds downloaded media at the top
    # if (and only if) the body contains no inline embeds.
    saved_date = date.today().strftime("%Y-%m-%d")
    parts = [
        _summary_section(ai_result),
        _takeaways_section(ai_result),
        _paywall_warning(content),
        _article_body(content),
        "---",
        _metadata_section(content, saved_date),
    ]
    return "\n".join(p for p in parts if p)


def _render_web_generic(ai_result, content, media_paths, transcript, collapse):
    saved_date = date.today().strftime("%Y-%m-%d")
    parts = [
        _summary_section(ai_result),
        _takeaways_section(ai_result),
        _paywall_warning(content),
        _article_body(content),
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
    "tiktok_photo":    _render_instagram_post,  # image post, no video/transcript
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
