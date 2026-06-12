#!/usr/bin/env python3
"""Manually process a single URL end-to-end, write the note, and print a summary.

Usage:
    python scripts/process_one.py <URL>
    python scripts/process_one.py <URL> --dry-run   # print note only, no file write
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from src.config import load_config
from src.credentials import load_credentials
from src.extractors import get_extractor
from src.extractors.enrich import enrich_embedded_media
from src.media.downloader import download_media, abs_to_obsidian_embed
from src.media.transcriber import transcribe
from src.media.vision import prepare_images_for_claude
from src.ai.claude_client import analyze_content, fact_check
from src.notes.formatter import format_note
from src.notes.file_manager import write_note
from src.utils.url_parser import detect_platform, normalize_url


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

    transcript = None
    if content.captions:
        transcript = content.captions
        print(f"  Using existing captions ({len(transcript)} chars)")
    elif media_paths_abs:
        print("  Attempting transcription...")
        transcript = await transcribe(media_paths_abs[0], config)
        if transcript:
            print(f"  Transcript: {len(transcript)} chars")
        else:
            print("  Transcription skipped or unavailable")

    image_blocks = []
    if media_paths_abs and platform != "youtube":
        image_blocks = prepare_images_for_claude(media_paths_abs, platform, config)
        if image_blocks:
            print(f"  Vision: {len(image_blocks)} image block(s) prepared for Claude")

    print("Sending to Claude for analysis...")
    ai_result = await analyze_content(
        content, transcript, config,
        image_blocks=image_blocks or None,
    )
    print(f"  Note type: {ai_result.get('note_type')}")
    print(f"  Folder: {ai_result.get('folder_path')}")
    print(f"  Tags ({len(ai_result.get('tags', []))}): {', '.join(ai_result.get('tags', []))}")

    fc_result = None
    if ai_result.get("topics"):
        print(f"  Topics: {ai_result['topics']} — checking for fact-check triggers")
        fc_result = await fact_check(content, ai_result, config)
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
