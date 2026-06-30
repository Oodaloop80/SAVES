#!/usr/bin/env python3
"""Manually process a single URL end-to-end, write the note, and print a summary.

Usage:
    python scripts/process_one.py <URL>
    python scripts/process_one.py <URL> --dry-run   # print note only, no file write
"""
import asyncio
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

# Surface our own INFO-level progress (e.g. the fact-check web-search rounds) so a slow
# silent pass doesn't look like a freeze. Third-party libs stay at WARNING to avoid noise.
logging.basicConfig(level=logging.WARNING, format="%(message)s")
logging.getLogger("src").setLevel(logging.INFO)

from src.ai.claude_client import analyze_content, fact_check  # noqa: E402
from src.config import load_config  # noqa: E402
from src.credentials import load_credentials  # noqa: E402
from src.extractors import get_extractor  # noqa: E402
from src.extractors.enrich import enrich_embedded_media  # noqa: E402
from src.media.downloader import (  # noqa: E402
    abs_to_obsidian_embed,
    download_media,
    localize_article_images,
)
from src.media.transcriber import is_audio_video, transcribe  # noqa: E402
from src.media.vision import prepare_images_for_claude  # noqa: E402
from src.notes.file_manager import write_note  # noqa: E402
from src.notes.formatter import format_note  # noqa: E402
from src.utils.url_parser import detect_platform, normalize_url  # noqa: E402
from src.utils.vault_scanner import scan_saves_folders  # noqa: E402


async def run(url: str, dry_run: bool = False):
    load_credentials()
    config = load_config()

    url = normalize_url(url)
    platform = detect_platform(url)
    print(f"Platform detected: {platform}")

    extractor = get_extractor(url, config)
    print("Extracting content...")
    content = await extractor.extract(url)
    content = await enrich_embedded_media(content, config)
    if content.metadata.get("youtube_url"):
        print(f"  Enriched with embedded YouTube: {content.metadata['youtube_url']}")
        if content.metadata.get("youtube_channel"):
            print(f"    Channel: {content.metadata['youtube_channel']}")
        if content.metadata.get("youtube_description"):
            print(f"    Description: {len(content.metadata['youtube_description'])} chars (recipe/instructions)")
        if content.captions:
            print(f"  YouTube captions available ({len(content.captions)} chars) — Whisper will be skipped")
        else:
            print("  No YouTube captions found — will fall back to Whisper on the downloaded video")
    print(f"  Title: {content.title}")
    print(f"  Author: {content.author}")
    print(f"  Body length: {len(content.body_text)} chars")
    if content.top_comments:
        print(f"  Top comments: {len(content.top_comments)}")
    print(f"  Media URLs extracted: {content.media_urls}")

    paths = config.get("paths", {})
    media_root = paths.get("media_root", "test-media")
    vault_root = paths.get("vault_root", "test-vault")

    print("Downloading media...")
    media_paths_abs = await download_media(
        platform=platform,
        author=content.author or "unknown",
        title=content.title or url,
        media_urls=content.media_urls,
        source_url=url,
        media_root=media_root,
        config=config,
        cookies_dir=paths.get("cookies_dir", "cookies"),
    )
    print(f"  Downloaded {len(media_paths_abs)} media file(s)")
    embed_paths = [abs_to_obsidian_embed(p, media_root, vault_root) for p in media_paths_abs]

    if content.metadata.get("article_markdown"):
        await localize_article_images(content, platform, media_root, vault_root)
        print("  Localized inline article images into the vault")

    transcript = None
    if content.captions:
        transcript = content.captions
        print(f"  Using existing captions ({len(transcript)} chars)")
    elif media_paths_abs and is_audio_video(media_paths_abs[0]):
        print("  Attempting transcription...")
        transcript = await transcribe(media_paths_abs[0], config)
        if transcript:
            print(f"  Transcript: {len(transcript)} chars")
        else:
            print("  Transcription skipped or unavailable")
    elif media_paths_abs:
        print("  No audio/video media — skipping transcription")

    image_blocks = []
    # Skip OCR/vision for generic web articles — body text is already extracted as Markdown.
    if media_paths_abs and platform not in ("youtube", "generic"):
        image_blocks = prepare_images_for_claude(media_paths_abs, platform, config)
        if image_blocks:
            print(f"  Vision: {len(image_blocks)} image block(s) prepared for Claude")

    saves_root = paths.get("saves_root") or os.path.join(vault_root, "SAVES")
    existing_folders = scan_saves_folders(saves_root)
    if existing_folders:
        print(f"  Consulting {len(existing_folders)} existing vault folder(s) for placement")

    print("Sending to Claude for analysis...")
    ai_result = await analyze_content(
        content, transcript, config,
        image_blocks=image_blocks or None,
        existing_folders=existing_folders,
    )
    print(f"  Note type: {ai_result.get('note_type')}")
    print(f"  Folder: {ai_result.get('folder_path')}")
    print(f"  Tags ({len(ai_result.get('tags', []))}): {', '.join(ai_result.get('tags', []))}")

    fc_result = None
    if ai_result.get("topics"):
        print(f"  Topics: {ai_result['topics']} — checking for fact-check triggers")
        fc_result = await fact_check(content, ai_result, config, image_blocks=image_blocks or None)
        if fc_result:
            if fc_result.get("opinion_only"):
                print("  Fact-check: opinion/analysis content — no claims to verify")
            elif fc_result.get("disputed_claims"):
                print(f"  Fact-check: {len(fc_result['disputed_claims'])} disputed claim(s)")
            else:
                print("  Fact-check: all claims verified")

    note_md = format_note(ai_result, content, embed_paths, transcript, config, fc_result)

    print("\n" + "=" * 60)
    print(note_md)
    print("=" * 60)

    if dry_run:
        print("\n[dry-run] Note not written to disk.")
        return

    folder_path = ai_result.get("folder_path", "SAVES/_UNSORTED")
    filename = ai_result.get("title") or ai_result.get("filename") or content.title or url
    note_path = write_note(vault_root, folder_path, filename, note_md)
    print(f"\nNote written to: {note_path}")



if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python scripts/process_one.py <URL> [--dry-run]")
        sys.exit(1)
    dry = "--dry-run" in sys.argv
    asyncio.run(run(sys.argv[1], dry_run=dry))
