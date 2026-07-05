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

    def forget(self, url: str) -> bool:
        """Drop a URL's state entry so it can be reprocessed — deleting the note in
        Obsidian does NOT do this (the vault is never watched for deletions), which is
        why a deleted-then-repasted URL still reports as a duplicate. Exposed to the
        user via the /forget slash command. Returns True if an entry existed."""
        if url in self._state:
            self._state.pop(url)
            self._save()
            return True
        return False

    def entries(self) -> dict:
        """Read-only view of the state map (url → {status, path, timestamp}) for
        autocomplete over saved history."""
        return dict(self._state)


class QueueManager:
    def __init__(self, queue: asyncio.Queue, state: ProcessingState, skip_duplicates: bool = True):
        self._queue = queue
        self._state = state
        self._skip_duplicates = skip_duplicates
        self._queued: set[str] = set()
        # Normalized URLs we've already reported as duplicates this session. Prevents a
        # second "duplicate" Discord ping when the watcher re-fires between detection and
        # the caller removing the line from the inbox.
        self._reported_duplicates: set[str] = set()

    async def enqueue_from_file(self, inbox_path: str) -> list[tuple[str, str | None]]:
        """Queue new URLs from the inbox and return already-saved duplicates.

        Returns a list of ``(raw_url, existing_note_path)`` for URLs that were already
        saved to completion — the caller notifies the user and clears them from the inbox.
        ``raw_url`` is the URL exactly as it appears in the inbox (so it can be matched for
        removal); ``existing_note_path`` is the vault path recorded when it was first saved.
        """
        try:
            with open(inbox_path, "r", encoding="utf-8") as f:
                text = f.read()
        except FileNotFoundError:
            return []

        urls = extract_urls(text)
        new_count = 0
        duplicates: list[tuple[str, str | None]] = []
        for raw_url in urls:
            # Normalize before dedup so the key space matches ProcessingState, which is
            # keyed by the normalized URL (processor normalizes before mark_pending/done).
            # Without this, social share links with tracking params (igsh/fbclid/utm_*)
            # miss the state lookup and get re-enqueued after a restart → duplicate notes.
            url = normalize_url(raw_url)
            if url in self._queued:
                continue
            # Already saved before? Report it as a duplicate (once) so the user gets a
            # Discord heads-up BEFORE any extraction/AI tokens are spent — instead of the
            # old behaviour of silently skipping it.
            if self._skip_duplicates and self._state.is_done(url):
                if url not in self._reported_duplicates:
                    self._reported_duplicates.add(url)
                    duplicates.append((raw_url, self._state.path_for(url)))
                continue
            if self._state.is_processed(url):
                continue
            self._queued.add(url)
            await self._queue.put(url)
            new_count += 1

        if new_count:
            logger.info(f"Queued {new_count} new URL(s)")
        if duplicates:
            logger.info(f"Detected {len(duplicates)} already-saved duplicate(s)")
        return duplicates

    def forget(self, url: str) -> None:
        """Companion to ProcessingState.forget: clear the session-local dedup sets so a
        forgotten URL pasted again in the SAME process actually re-enqueues (without this,
        a URL saved earlier this session stays blocked by _queued until restart)."""
        self._queued.discard(url)
        self._reported_duplicates.discard(url)
