import asyncio
import logging
import os

from src.ai.claude_client import analyze_content, fact_check
from src.ai.verifier import check_travel_location
from src.discord_bot.approval import new_pending
from src.discord_bot.notifications import send_alert
from src.extractors import get_extractor
from src.extractors.enrich import enrich_embedded_media
from src.media.downloader import download_media, abs_to_obsidian_embed
from src.media.transcriber import transcribe, is_audio_video
from src.media.vision import prepare_images_for_claude
from src.queue_manager import ProcessingState
from src.utils.preferences import PreferencesStore, get_source_key
from src.utils.url_parser import detect_platform, normalize_url
from src.utils.vault_scanner import scan_saves_folders

logger = logging.getLogger(__name__)


async def run_processor(
    queue: asyncio.Queue,
    config: dict,
    bot,
    state: ProcessingState,
    prefs: PreferencesStore,
):
    paths = config.get("paths", {})
    media_root = paths.get("media_root", "/media")
    vault_root = paths.get("vault_root", "/vault")
    cookies_dir = paths.get("cookies_dir", "cookies")
    alert_channel = config.get("discord", {}).get("channel_alerts", "SAVES-alerts")

    while True:
        url = await queue.get()
        try:
            await _process_one(
                url, config, bot, state, prefs,
                media_root, vault_root, cookies_dir, alert_channel,
            )
        except Exception as e:
            logger.exception(f"Unhandled error processing {url}: {e}")
            state.mark_failed(url, str(e))
        finally:
            queue.task_done()
            platform = detect_platform(url)
            delay = _get_delay(config, platform)
            if delay:
                await asyncio.sleep(delay)


async def _process_one(
    url: str, config: dict, bot, state: ProcessingState, prefs: PreferencesStore,
    media_root: str, vault_root: str, cookies_dir: str, alert_channel: str,
):
    url = normalize_url(url)
    platform = detect_platform(url)
    logger.info(f"Processing [{platform}] {url}")
    state.mark_pending(url)

    # 1. Extract
    extractor = get_extractor(url, config)
    try:
        content = await extractor.extract(url)
    except Exception as e:
        err_msg = str(e)
        if any(code in err_msg for code in ("404", "not found", "removed")):
            state.mark_failed(url, err_msg, permanent=True)
            await _append_failed_url(url, err_msg, vault_root)
            await send_alert(bot, alert_channel, f"404/deleted: {url}\n{err_msg}")
        elif any(code in err_msg for code in ("401", "403", "private", "login")):
            state.mark_retry_after_auth(url, platform)
            await send_alert(bot, alert_channel, f"Auth required for {platform}: {url}")
        else:
            state.mark_failed(url, err_msg)
            await send_alert(bot, alert_channel, f"Extraction failed: {url}\n{err_msg}")
        return

    # 1b. Enrich with embedded cross-platform media (e.g. a YouTube video in a Reddit post)
    content = await enrich_embedded_media(content, config)

    # 2. Build preferences hint for this source
    source_key = get_source_key(platform, content.metadata or {}, content.author)
    preferences_hint = prefs.hint(source_key)

    # 3. Download media
    media_paths_abs = []
    try:
        media_paths_abs = await download_media(
            platform=platform,
            author=content.author or "unknown",
            title=content.title or url,
            media_urls=content.media_urls,
            source_url=url,
            media_root=media_root,
            config=config,
            cookies_dir=cookies_dir,
        )
    except Exception as e:
        logger.warning(f"Media download failed for {url}: {e}")
        content.metadata["media_download_failed"] = True

    embed_paths = [abs_to_obsidian_embed(p, media_root, vault_root) for p in media_paths_abs]

    # 4. Transcribe
    transcript = None
    if content.captions:
        transcript = content.captions
    elif media_paths_abs:
        audio_candidates = [p for p in media_paths_abs if is_audio_video(p)]
        if audio_candidates and config.get("transcription", {}).get("enabled", True):
            transcript = await transcribe(audio_candidates[0], config)

    # 5. Prepare vision data (images + video keyframes) for non-YouTube platforms
    image_blocks: list[dict] = []
    if media_paths_abs and platform != "youtube":
        try:
            image_blocks = await asyncio.to_thread(
                prepare_images_for_claude, media_paths_abs, platform, config
            )
            if image_blocks:
                logger.info(f"Vision: prepared {len(image_blocks)} image block(s) for {url}")
        except Exception as e:
            logger.warning(f"Vision preparation failed (non-fatal): {e}")

    # 6. AI analysis (text + vision, with preferences hint + existing vault folders)
    saves_root = config.get("paths", {}).get("saves_root") or os.path.join(vault_root, "SAVES")
    existing_folders = await asyncio.to_thread(scan_saves_folders, saves_root)
    try:
        ai_result = await analyze_content(
            content, transcript, config, preferences_hint,
            image_blocks=image_blocks or None,
            existing_folders=existing_folders,
        )
    except Exception as e:
        logger.error(f"AI analysis failed for {url}: {e}")
        await send_alert(bot, alert_channel, f"Claude API failed for {url}: {e}")
        state.mark_failed(url, f"AI failed: {e}")
        return

    # 7. Fact-check (health/political/finance) and travel location check in parallel
    fc_result = None
    lc_result = None
    try:
        fc_task = asyncio.create_task(fact_check(content, ai_result, config))
        lc_task = asyncio.create_task(check_travel_location(content, ai_result, config))
        fc_result, lc_result = await asyncio.gather(fc_task, lc_task, return_exceptions=True)
        if isinstance(fc_result, Exception):
            logger.warning(f"Fact-check failed (non-fatal): {fc_result}")
            fc_result = None
        if isinstance(lc_result, Exception):
            logger.warning(f"Location check failed (non-fatal): {lc_result}")
            lc_result = None
    except Exception as e:
        logger.warning(f"Secondary analysis failed (non-fatal): {e}")

    # Store results on ai_result for Discord preview and later approval
    if fc_result:
        ai_result["_fact_check"] = fc_result
    if lc_result:
        ai_result["_location_check"] = lc_result

    # Store source_key so bot can update preferences on approval
    ai_result["_source_key"] = source_key

    # 8. Send to Discord for approval
    content_summary = {
        "title": content.title,
        "author": content.author,
        "platform": content.platform,
        "body_text": content.body_text,
        "captions": content.captions,
        "metadata": content.metadata,
        "chapters": content.chapters,
        "top_comments": content.top_comments,
    }

    pending = new_pending(
        url=url,
        platform=platform,
        ai_result=ai_result,
        content_summary=content_summary,
        media_paths=embed_paths,
        transcript=transcript,
    )

    bot.store.add(pending)
    await bot.send_for_approval(pending)
    logger.info(f"Sent for approval: {url}")


def _get_delay(config: dict, platform: str) -> float:
    return config.get("platforms", {}).get(platform, {}).get("delay_seconds", 0.0)


async def _append_failed_url(url: str, reason: str, vault_root: str):
    from datetime import date
    failed_dir = os.path.join(vault_root, "SAVES", "_FAILED")
    os.makedirs(failed_dir, exist_ok=True)
    failed_file = os.path.join(failed_dir, "failed-urls.md")
    today = date.today().isoformat()
    line = f"- [ ] {url} — {reason[:80]} — {today}\n"
    with open(failed_file, "a", encoding="utf-8") as f:
        f.write(line)
