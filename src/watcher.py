import asyncio
import logging
import threading

from watchdog.events import FileModifiedEvent, FileSystemEventHandler
from watchdog.observers import Observer

logger = logging.getLogger(__name__)


class _InboxHandler(FileSystemEventHandler):
    def __init__(self, inbox_path: str, loop: asyncio.AbstractEventLoop, callback):
        self._inbox_path = inbox_path
        self._loop = loop
        self._callback = callback
        self._timer: threading.Timer | None = None
        self._lock = threading.Lock()
        self._debounce_seconds = 3

    def on_modified(self, event: FileModifiedEvent):
        if event.is_directory:
            return
        if not event.src_path.endswith(self._inbox_path.lstrip("/")):
            return
        with self._lock:
            if self._timer:
                self._timer.cancel()
            self._timer = threading.Timer(self._debounce_seconds, self._fire)
            self._timer.start()

    def _fire(self):
        self._loop.call_soon_threadsafe(self._callback)


class FileWatcher:
    def __init__(self, inbox_path: str, loop: asyncio.AbstractEventLoop, on_change):
        self._inbox_path = inbox_path
        self._loop = loop
        self._on_change = on_change
        self._observer: Observer | None = None

    def start(self):
        import os
        watch_dir = os.path.dirname(self._inbox_path)
        # watchdog's Observer.schedule() raises FileNotFoundError if the directory is
        # missing (e.g. NAS not mounted yet). Degrade gracefully instead of crashing the
        # whole app at startup: log a clear warning and skip watching. The processor still
        # drains the queue, and enqueue_from_file already tolerates a missing inbox file, so
        # URLs will be picked up on the next restart once the path exists.
        if not os.path.isdir(watch_dir):
            logger.error(
                "Inbox directory does not exist: %s — file watcher NOT started. URLs added "
                "to the inbox will not be auto-detected until this path exists and the app "
                "is restarted.",
                watch_dir,
            )
            return
        handler = _InboxHandler(self._inbox_path, self._loop, self._on_change)
        self._observer = Observer()
        self._observer.schedule(handler, watch_dir, recursive=False)
        self._observer.start()
        logger.info(f"Watching {watch_dir} for changes to {self._inbox_path}")

    def stop(self):
        if self._observer:
            self._observer.stop()
            self._observer.join()
