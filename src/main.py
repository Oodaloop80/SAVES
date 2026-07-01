import asyncio
import logging
import os
import sys

from src.config import load_config
from src.credentials import load_credentials
from src.discord_bot.bot import SAVESBot
from src.processor import run_processor
from src.queue_manager import ProcessingState, QueueManager
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
    queue_manager = QueueManager(queue, state)

    bot = SAVESBot(config, prefs, state)
    loop = asyncio.get_running_loop()

    def on_file_change():
        asyncio.ensure_future(queue_manager.enqueue_from_file(inbox_path))

    watcher = FileWatcher(inbox_path, loop, on_file_change)
    watcher.start()

    await queue_manager.enqueue_from_file(inbox_path)

    processor_task = asyncio.create_task(
        run_processor(queue, config, bot, state, prefs)
    )

    discord_token = os.environ["DISCORD_BOT_TOKEN"]
    try:
        await bot.start(discord_token)
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        processor_task.cancel()
        watcher.stop()
        await bot.close()


if __name__ == "__main__":
    asyncio.run(main())
