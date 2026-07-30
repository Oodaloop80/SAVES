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
    def __init__(
        self,
        queue: asyncio.Queue,
        state: ProcessingState,
        skip_duplicates: bool = True,
        serial: bool = True,
        queue_state_path: str = "queue_state.json",
    ):
        self._queue = queue
        self._state = state
        self._skip_duplicates = skip_duplicates
        self._queued: set[str] = set()
        # Normalized URLs we've already reported as duplicates this session. Prevents a
        # second "duplicate" Discord ping when the watcher re-fires between detection and
        # the caller removing the line from the inbox.
        self._reported_duplicates: set[str] = set()

        # ---- Serial approval queue (one card at a time) --------------------------------
        # When `serial` is on, the processor sends ONE approval card, then waits on `_gate`
        # until that card is approved/skipped before processing the next URL. This keeps the
        # Discord channel calm and lets each save's analysis pick up the folder preference +
        # tags learned from the one you just approved. All of it is persisted to
        # `queue_state.json` so a bot restart / long delay never loses the queue's place.
        self.serial = serial
        self._queue_state_path = queue_state_path
        self._gate = asyncio.Event()
        # URLs enqueued but not yet turned into a card (FIFO — mirrors the asyncio.Queue).
        self._waiting: list[str] = []
        # The URL whose card is currently on the board awaiting approval (the gate holder).
        self._active: str | None = None
        # "Save X of N" bookkeeping for the current non-empty streak (resets when the queue
        # fully drains). total counts everything enqueued in the streak; done counts approvals.
        self._streak_total = 0
        self._streak_done = 0
        self._load_queue_state()

    # ---- persistence -------------------------------------------------------------------

    def _load_queue_state(self) -> None:
        if not os.path.exists(self._queue_state_path):
            return
        try:
            with open(self._queue_state_path, "r", encoding="utf-8") as f:
                d = json.load(f)
            self._waiting = [u for u in d.get("waiting", []) if isinstance(u, str)]
            self._active = d.get("active")
            self._streak_total = int(d.get("streak_total", 0))
            self._streak_done = int(d.get("streak_done", 0))
        except Exception as e:
            logger.warning("Could not load queue_state (%s): %s", self._queue_state_path, e)

    def _save_queue_state(self) -> None:
        d = {
            "waiting": self._waiting,
            "active": self._active,
            "streak_total": self._streak_total,
            "streak_done": self._streak_done,
        }
        dir_name = os.path.dirname(os.path.abspath(self._queue_state_path))
        fd, tmp = tempfile.mkstemp(dir=dir_name, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(d, f, indent=2)
            os.replace(tmp, self._queue_state_path)
        except Exception as e:
            logger.warning("Could not save queue_state: %s", e)

    def _is_tracked(self, url: str) -> bool:
        """True if `url` is already waiting or is the active card (persistent dedup — survives
        restart, unlike the session-only `_queued` set)."""
        return url in self._waiting or url == self._active

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
            # Already saved before? Report it as a duplicate so the user gets a Discord
            # heads-up BEFORE any extraction/AI tokens are spent — instead of the old
            # behaviour of silently skipping it. This check MUST come before the _queued
            # session check: a URL saved earlier in this same session is still in _queued,
            # and checking _queued first silently ate the re-paste (no notice, line left
            # in the inbox forever). _reported_duplicates only guards the window while the
            # line is still in the inbox (watcher re-fires before the caller removes it);
            # duplicate_cleared() lifts it once the line is gone so a later deliberate
            # re-paste notifies again.
            if self._skip_duplicates and self._state.is_done(url):
                if url not in self._reported_duplicates:
                    self._reported_duplicates.add(url)
                    duplicates.append((raw_url, self._state.path_for(url)))
                continue
            if url in self._queued or self._is_tracked(url):
                continue
            if self._state.is_processed(url):
                continue
            self._enqueue(url)
            new_count += 1

        if new_count:
            logger.info(f"Queued {new_count} new URL(s)")
        if duplicates:
            logger.info(f"Detected {len(duplicates)} already-saved duplicate(s)")
        return duplicates

    async def enqueue_url(self, raw_url: str) -> bool:
        """Directly queue one URL, bypassing the inbox file — the duplicate notice's
        🔁 Re-save button uses this after forget() so the user doesn't have to re-paste.
        Same normalization/dedup as enqueue_from_file. Returns True if it was queued."""
        url = normalize_url(raw_url)
        if url in self._queued or self._is_tracked(url):
            return False
        if self._skip_duplicates and self._state.is_done(url):
            return False
        self._enqueue(url)
        logger.info(f"Queued 1 URL directly (re-save): {url}")
        return True

    def _enqueue(self, url: str) -> None:
        """Common enqueue: session dedup set + persistent waiting list + streak total +
        the runtime asyncio.Queue. `_queue.put_nowait` is safe — the queue is unbounded."""
        self._queued.add(url)
        self._waiting.append(url)
        self._streak_total += 1
        self._queue.put_nowait(url)
        self._save_queue_state()

    # ---- runtime + serial gate ---------------------------------------------------------

    async def get(self) -> str:
        return await self._queue.get()

    def task_done(self) -> None:
        self._queue.task_done()

    def restore_runtime(self) -> None:
        """On startup, re-put the persisted `waiting` URLs onto the fresh asyncio.Queue so the
        processor picks up where it left off (the active card, if any, is left alone — its card
        is already on Discord awaiting approval)."""
        for url in self._waiting:
            self._queue.put_nowait(url)
            self._queued.add(url)
        if self._waiting or self._active:
            logger.info(
                "Restored queue: %d waiting, active=%s", len(self._waiting), self._active
            )

    def mark_carded(self, url: str) -> None:
        """A card was sent for `url` — it becomes the active (gating) item."""
        if url in self._waiting:
            self._waiting.remove(url)
        self._active = url
        self._save_queue_state()

    def mark_uncarded(self, url: str) -> None:
        """`url` was processed but produced NO card (extraction/AI failed) — drop it from the
        waiting list without gating (the processor moves straight on)."""
        if url in self._waiting:
            self._waiting.remove(url)
        self._save_queue_state()

    def clear_active_if(self, url: str) -> None:
        """Clear the active marker if it points at `url` (used on restart when the active card
        turns out to be already-done or to have no card)."""
        if self._active == url:
            self._active = None
            self._maybe_reset_streak()
            self._save_queue_state()

    def resolve(self, url: str) -> None:
        """The active card was approved — release the gate and advance the counter. No-op for a
        non-active (e.g. legacy) card so approving those doesn't disturb the serial queue."""
        if url != self._active:
            return
        self._active = None
        self._streak_done += 1
        self._maybe_reset_streak()
        self._save_queue_state()
        self._gate.set()

    def skip(self, url: str) -> None:
        """Defer the active card: re-queue `url` to the BACK and release the gate so the next
        save shows now. The skipped save comes back around later (not counted as done)."""
        self._waiting.append(url)
        self._queue.put_nowait(url)
        self._queued.add(url)
        if url == self._active:
            self._active = None
        self._save_queue_state()
        self._gate.set()

    def _maybe_reset_streak(self) -> None:
        # Once nothing is active and nothing is waiting, the batch is done — reset the
        # "X of N" counters so the next batch numbers from 1.
        if self._active is None and not self._waiting:
            self._streak_total = 0
            self._streak_done = 0

    async def wait_for_gate(self) -> None:
        await self._gate.wait()
        self._gate.clear()

    @property
    def active_url(self) -> str | None:
        return self._active

    @property
    def waiting_count(self) -> int:
        return len(self._waiting)

    def snapshot(self) -> dict:
        """Queue position for the item about to be carded (the front of `_waiting`): where it
        sits in the current streak and how many trail behind it. Embedded on the approval card."""
        return {
            "position": self._streak_done + 1,
            "total": max(self._streak_total, self._streak_done + 1),
            "waiting": max(0, len(self._waiting) - 1),
        }

    def status(self) -> dict:
        """Live queue status for the /queue command."""
        return {
            "active": self._active,
            "waiting": len(self._waiting),
            "position": self._streak_done + 1 if (self._active or self._waiting) else 0,
            "total": self._streak_total,
            "serial": self.serial,
        }

    def duplicate_cleared(self, raw_url: str) -> None:
        """Called by the caller AFTER the duplicate line was removed from the inbox.
        Lifts the once-only report guard so that pasting the same URL again later gets a
        fresh duplicate notice (instead of being swallowed for the rest of the session).
        Safe because the guard's only job is the detection→line-removal window: with the
        line gone, the next watcher fire no longer sees the URL at all."""
        self._reported_duplicates.discard(normalize_url(raw_url))

    def forget(self, url: str) -> None:
        """Companion to ProcessingState.forget: clear the session-local dedup sets so a
        forgotten URL pasted again in the SAME process actually re-enqueues (without this,
        a URL saved earlier this session stays blocked by _queued until restart). Also drops
        it from the persistent waiting list / active slot and releases the gate if it was the
        active card, so forgetting the current save advances the serial queue."""
        self._queued.discard(url)
        self._reported_duplicates.discard(url)
        changed = False
        if url in self._waiting:
            self._waiting.remove(url)
            changed = True
        if url == self._active:
            self._active = None
            self._gate.set()
            changed = True
        if changed:
            self._maybe_reset_streak()
            self._save_queue_state()
