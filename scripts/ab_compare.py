#!/usr/bin/env python3
"""A/B-compare the analysis stage across two models on one real URL.

Extracts + downloads + transcribes + OCRs the URL ONCE (shared, fair inputs), then runs
the analysis (folder routing / note_type / tags / summary) with each model and writes a
SEPARATE, clearly-labeled note to the DEV vault so you can open both in Obsidian and judge
quality + routing side by side. Fact-check is skipped here — this compares the analysis
stage only, and skipping it keeps the run cheap and fast.

Both notes land in SAVES/_AB_TEST/ with a `[Opus]` / `[Sonnet]` filename prefix and a
callout at the top showing that model's CHOSEN folder, so routing differences are visible
without scattering the notes across the vault.

Usage:
    python scripts/ab_compare.py <URL>
"""
import asyncio
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

logging.basicConfig(level=logging.WARNING, format="%(message)s")
logging.getLogger("src").setLevel(logging.INFO)

from src.ai.claude_client import analyze_content  # noqa: E402
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

# The two models under test. Left = current default (Opus), right = the cheaper candidate.
MODELS = [("Opus", "claude-opus-4-8"), ("Sonnet", "claude-sonnet-4-6")]
AB_FOLDER = "SAVES/_AB_TEST"


def _label_callout(label: str, model_id: str, ai_result: dict) -> str:
    tags = ", ".join(ai_result.get("tags", []))
    return (
        f"> [!info] A/B TEST — {label} (`{model_id}`)\n"
        f"> **This model's chosen folder:** `{ai_result.get('folder_path')}`\n"
        f"> **Note type:** {ai_result.get('note_type')}\n"
        f"> **Tags ({len(ai_result.get('tags', []))}):** {tags}\n"
        f"> Compare against the other model's note for the same post (same `_AB_TEST` folder).\n\n"
    )


async def run(url: str):
    load_credentials()
    config = load_config()

    url = normalize_url(url)
    platform = detect_platform(url)
    print(f"Platform detected: {platform}")

    # ---- Shared inputs (run ONCE) ----
    extractor = get_extractor(url, config)
    print("Extracting content...")
    content = await extractor.extract(url)
    content = await enrich_embedded_media(content, config)
    print(f"  Title: {content.title}")
    print(f"  Media URLs: {len(content.media_urls)}")

    paths = config.get("paths", {})
    media_root = paths.get("media_root", "test-media")
    vault_root = paths.get("vault_root", "test-vault")

    print("Downloading media...")
    media_paths_abs = await download_media(
        platform=platform, author=content.author or "unknown", title=content.title or url,
        media_urls=content.media_urls, source_url=url, media_root=media_root,
        config=config, cookies_dir=paths.get("cookies_dir", "cookies"),
    )
    print(f"  Downloaded {len(media_paths_abs)} media file(s)")
    embed_paths = [abs_to_obsidian_embed(p, media_root, vault_root) for p in media_paths_abs]

    if content.metadata.get("article_markdown"):
        await localize_article_images(content, platform, media_root, vault_root)

    transcript = None
    if content.captions:
        transcript = content.captions
    elif media_paths_abs and is_audio_video(media_paths_abs[0]):
        print("  Transcribing...")
        transcript = await transcribe(media_paths_abs[0], config)
    if transcript:
        print(f"  Transcript: {len(transcript)} chars")

    image_blocks = []
    if media_paths_abs and platform not in ("youtube", "generic"):
        image_blocks = prepare_images_for_claude(media_paths_abs, platform, config)
        if image_blocks:
            print(f"  Vision: {len(image_blocks)} image block(s)")

    saves_root = paths.get("saves_root") or os.path.join(vault_root, "SAVES")
    existing_folders = scan_saves_folders(saves_root)

    # ---- Per-model analysis + labeled note ----
    written = []
    for label, model_id in MODELS:
        print(f"\n=== Analyzing with {label} ({model_id}) ===")
        model_config = {**config, "ai": {**config.get("ai", {}), "model": model_id}}
        ai_result = await analyze_content(
            content, transcript, model_config,
            image_blocks=image_blocks or None, existing_folders=existing_folders,
        )
        print(f"  Folder: {ai_result.get('folder_path')}")
        print(f"  Note type: {ai_result.get('note_type')}")
        print(f"  Tags ({len(ai_result.get('tags', []))}): {', '.join(ai_result.get('tags', []))}")

        note_md = _label_callout(label, model_id, ai_result) + format_note(
            ai_result, content, embed_paths, transcript, model_config, None
        )
        base_title = ai_result.get("title") or content.title or url
        note_path = write_note(vault_root, AB_FOLDER, f"[{label}] {base_title}", note_md)
        written.append((label, note_path))
        print(f"  Wrote: {note_path}")

    print("\n" + "=" * 60)
    print("A/B notes written — open these in Obsidian and compare:")
    for label, path in written:
        print(f"  {label}: {path}")
    print("=" * 60)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python scripts/ab_compare.py <URL>")
        sys.exit(1)
    asyncio.run(run(sys.argv[1]))
