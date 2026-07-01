import asyncio
import json
import logging
import os
import tempfile
import time

from src.utils.url_parser import extract_urls, normalize_url

logger = logging.getLogger(__name__)


class ProcessingState:
    def __init__(self, path: str):
        self.path = path
        self._state: dict = {}
        self._load()

    def _load(self):
        if os.path.exists(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    self._state = json.load(f)
            except Exception:
                self._state = {}

    def _save(self):
        dir_name = os.path.dirname(os.path.abspath(self.path))
        fd, tmp = tempfile.mkstemp(dir=dir_name, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(self._state, f, indent=2)
            os.replace(tmp, self.path)
        except Exception:
            raise

    def is_processed(self, url: str) -> bool:
        status = self._state.get(url, {}).get("status")
        return status in ("done", "pending", "failed_permanent")

    def is_done(self, url: str) -> bool:
        return self._state.get(url, {}).get("status") == "done"

    def path_for(self, url: str) -> str | None:
        return self._state.get(url, {}).get("path")

    def mark_pending(self, url: str):
        self._state[url] = {"status": "pending", "timestamp": time.time()}
        self._save()

    def mark_done(self, url: str, path: str):
        self._state[url] = {"status": "done", "path": path, "timestamp": time.time()}
        self._save()

    def mark_failed(self, url: str, reason: str, permanent: bool = False):
        status = "failed_permanent" if permanent else "failed"
        self._state[url] = {"status": status, "reason": reason, "timestamp": time.time()}
        self._save()

    def mark_retry_after_auth(self, url: str, platform: str):
        self._state[url] = {"status": "retry_after_auth", "platform": platform, "timestamp": time.time()}
        self._save()


class QueueManager:
    def __init__(self, queue: asyncio.Queue, state: ProcessingState):
        self._queue = queue
        self._state = state
        self._queued: set[str] = set()

    async def enqueue_from_file(self, inbox_path: str):
        try:
            with open(inbox_path, "r", encoding="utf-8") as f:
                text = f.read()
        except FileNotFoundError:
            return

        urls = extract_urls(text)
        new_count = 0
        for url in urls:
            # Normalize before dedup so the key space matches ProcessingState, which is
            # keyed by the normalized URL (processor normalizes before mark_pending/done).
            # Without this, social share links with tracking params (igsh/fbclid/utm_*)
            # miss the state lookup and get re-enqueued after a restart → duplicate notes.
            url = normalize_url(url)
            if url in self._queued:
                continue
            if self._state.is_processed(url):
                continue
            self._queued.add(url)
            await self._queue.put(url)
            new_count += 1

        if new_count:
            logger.info(f"Queued {new_count} new URL(s)")
