import asyncio
import logging
from abc import ABC, abstractmethod

from src.utils.url_parser import normalize_url

logger = logging.getLogger(__name__)


class SiteCrawler(ABC):
    """Base class for per-site crawlers.

    A crawler turns ONE index/profile/creator page into a list of individual content URLs
    and feeds them through the existing pipeline. Only `discover_urls()` is site-specific;
    `partition()` and `enqueue_discovered()` are shared so every site dedups and enqueues
    identically. Downstream (extract → AI → Discord approval → vault) is unchanged — each
    discovered URL gets its own normal approval card.
    """

    #: short site name, used in logs / the Discord confirm card
    name = "generic"

    def __init__(self, config: dict):
        self.config = config

    @staticmethod
    def matches(url: str) -> bool:
        """True if this crawler handles `url` as an entry (index/profile) page. Overridden
        per site; the base never matches so it can't be selected by the registry."""
        return False

    @abstractmethod
    async def discover_urls(self, entry_url: str) -> list[str]:
        """Return the content URLs to save from `entry_url`.

        MUST stay scoped to that single entry (e.g. one creator's recipes) — a crawler never
        walks the whole site. Order is preserved; callers dedup via `partition()`.
        """
        raise NotImplementedError

    def partition(self, urls: list[str], state) -> tuple[list[str], list[str]]:
        """Split discovered URLs into (new, already_saved) against ProcessingState.

        Pure (no side effects): used to build the "found N, M already saved, queue K?" confirm
        card and the dry-run list. Dedups within the batch and matches state by NORMALIZED URL
        (the same key space processing_state.json uses), so tracking-param variants line up.
        """
        new: list[str] = []
        dup: list[str] = []
        seen: set[str] = set()
        for u in urls:
            key = normalize_url(u)
            if key in seen:
                continue
            seen.add(key)
            (dup if state.is_done(key) else new).append(u)
        return new, dup

    async def enqueue_discovered(self, urls: list[str], queue_manager, *,
                                 dry_run: bool = False,
                                 rate_limit_seconds: float = 0.0) -> dict:
        """Enqueue `urls` one at a time through the existing pipeline.

        Pass the already-partitioned `new` list. `enqueue_url()` still re-checks dedup, so a
        URL saved between discovery and confirmation is skipped safely. `rate_limit_seconds`
        paces the enqueues (processing is serial downstream, so this is gentle pacing, not the
        primary throttle). In `dry_run` nothing is queued. Returns a summary dict.
        """
        queued = 0
        for u in urls:
            if dry_run:
                continue
            if await queue_manager.enqueue_url(u):
                queued += 1
                if rate_limit_seconds:
                    await asyncio.sleep(rate_limit_seconds)
        return {"requested": len(urls), "queued": queued, "dry_run": dry_run}
