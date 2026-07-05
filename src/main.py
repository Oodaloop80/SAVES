import asyncio
import logging
import os
import sys

# Allow `python src\main.py` from the repo root on bare-metal dev: put the repo root on
# sys.path so `src.*` imports resolve. (Docker runs from /app where this is a no-op.)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.config import load_config
from src.credentials import load_credentials
from src.discord_bot.bot import SAVESBot
from src.discord_bot.notifications import send_duplicate_notice
from src.processor import run_processor
from src.queue_manager import ProcessingState, QueueManager
from src.utils.file_io import remove_url_from_inbox
from src.utils.preferences import PreferencesStore
from src.utils.validation import validate_startup
from src.watcher import FileWatcher

os.makedirs("logs", exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("logs/processor.log", mode="a"),
        logging.FileHandler("logs/errors.log", mode="a"),
    ],
)
logging.getLogger().handlers[2].setLevel(logging.ERROR)

logger = logging.getLogger(__name__)


async def main():
    config = load_config()
    load_credentials()
    validate_startup(config)  # fail fast on missing paths/channels before anything starts

    paths = config.get("paths", {})
    inbox_path = paths.get("inbox_file", "")

    prefs_cfg = config.get("preferences", {})
    prefs = PreferencesStore(
        path=prefs_cfg.get("file", "preferences.json"),
        enabled=prefs_cfg.get("enabled", True),
    )

    state = ProcessingState(paths.get("state_file", "processing_state.json"))
    queue: asyncio.Queue = asyncio.Queue()
    skip_duplicates = config.get("processing", {}).get("skip_duplicates", True)
    queue_manager = QueueManager(queue, state, skip_duplicates=skip_duplicates)

    bot = SAVESBot(config, prefs, state)
    # /forget needs to clear the queue manager's session dedup sets too, or a URL
    # forgotten and re-pasted within the same process would stay blocked until restart.
    bot.queue_manager = queue_manager

    # Reconcile crash orphans: URLs that were marked_pending before the pipeline crashed
    # (between mark_pending and bot.store.add) have no Discord card and will never be
    # re-queued because is_processed() returns True for "pending". Reset them to "failed"
    # so scan_inbox() picks them up again on this startup.
    _reconcile_crash_orphans(state, bot)

    loop = asyncio.get_running_loop()

    log_channel = config.get("discord", {}).get("channel_log", "SAVES-logs")

    async def scan_inbox():
        """Queue new URLs and, for any already-saved duplicates, ping Discord and drop the
        line from the inbox so it doesn't re-trigger on the next file change."""
        # Wait for the Discord connection before scanning. A scan that runs pre-connect
        # finds duplicates, fails the channel lookup silently, and STILL removes the inbox
        # line — the notice is lost forever. Nothing is sacrificed by waiting: approval
        # cards need the bot online anyway, and the wait is a no-op once connected.
        # (DECISION: notifications are immediate, never dropped/batched — see ROADMAP
        # "Decisions locked".)
        await bot.wait_until_ready()
        duplicates = await queue_manager.enqueue_from_file(inbox_path)
        for raw_url, existing_path in duplicates:
            await send_duplicate_notice(bot, log_channel, raw_url, existing_path)
            try:
                remove_url_from_inbox(inbox_path, raw_url)
            except Exception as e:
                logger.warning(f"Failed to clear duplicate URL from inbox: {e}")

    def on_file_change():
        asyncio.ensure_future(scan_inbox())

    debounce = config.get("watcher", {}).get("debounce_seconds", 3.0)
    watcher = FileWatcher(inbox_path, loop, on_file_change, debounce_seconds=debounce)
    watcher.start()

    # As a background task, NOT awaited: scan_inbox blocks on bot.wait_until_ready(),
    # and the bot only becomes ready inside bot.start() below — awaiting here would deadlock.
    startup_scan_task = asyncio.create_task(scan_inbox())

    processor_task = asyncio.create_task(
        run_processor(queue, config, bot, state, prefs)
    )

    discord_token = os.environ["DISCORD_BOT_TOKEN"]
    try:
        await bot.start(discord_token)
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        startup_scan_task.cancel()
        processor_task.cancel()
        watcher.stop()
        await bot.close()


def _reconcile_crash_orphans(state, bot) -> None:
    """Reset 'pending' state entries that have no Discord card to 'failed' so they are
    re-queued on the next scan_inbox(). These arise when the app crashes between
    mark_pending (start of pipeline) and bot.store.add (Discord card sent) — the URL
    stays stuck as 'pending' forever because is_processed() returns True for that status."""
    store_urls = {item.url for item in bot.store.get_all()}
    orphans = [
        url for url, entry in state._state.items()
        if entry.get("status") == "pending" and url not in store_urls
    ]
    if orphans:
        logger.warning(
            "Startup: found %d crash-orphan 'pending' URL(s) with no Discord card — "
            "resetting to 'failed' so they are re-queued: %s",
            len(orphans), orphans,
        )
        for url in orphans:
            state.mark_failed(url, "crash orphan: pipeline never reached Discord card — re-queued")


if __name__ == "__main__":
    asyncio.run(main())
