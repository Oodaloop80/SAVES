import json
import logging
import os
import tempfile
import time
import uuid
from dataclasses import dataclass, field, asdict
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class PendingApproval:
    id: str
    url: str
    platform: str
    ai_result: dict
    content_summary: dict   # lightweight: title, author, platform — not full ExtractedContent
    media_paths: list[str]
    transcript: str | None
    discord_message_id: int | None
    created_at: float = field(default_factory=time.time)


class PendingApprovalsStore:
    def __init__(self, path: str):
        self.path = path
        self._items: dict[str, PendingApproval] = {}
        self._load()

    def _load(self) -> None:
        if not os.path.exists(self.path):
            return
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
            for item_data in data:
                item = PendingApproval(**item_data)
                self._items[item.id] = item
        except Exception as e:
            logger.warning(f"Could not load pending approvals: {e}")

    def _save(self) -> None:
        data = [asdict(item) for item in self._items.values()]
        dir_name = os.path.dirname(os.path.abspath(self.path))
        fd, tmp = tempfile.mkstemp(dir=dir_name, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            os.replace(tmp, self.path)
        except Exception:
            raise

    def add(self, item: PendingApproval) -> None:
        self._items[item.id] = item
        self._save()

    def remove(self, item_id: str) -> None:
        self._items.pop(item_id, None)
        self._save()

    def update(self, item: PendingApproval) -> None:
        self._items[item.id] = item
        self._save()

    def get_all(self) -> list[PendingApproval]:
        return list(self._items.values())

    def get_by_message_id(self, message_id: int) -> PendingApproval | None:
        for item in self._items.values():
            if item.discord_message_id == message_id:
                return item
        return None

    def get_by_id(self, item_id: str) -> PendingApproval | None:
        return self._items.get(item_id)


def new_pending(
    url: str, platform: str, ai_result: dict, content_summary: dict,
    media_paths: list[str], transcript: str | None,
) -> PendingApproval:
    return PendingApproval(
        id=str(uuid.uuid4()),
        url=url,
        platform=platform,
        ai_result=ai_result,
        content_summary=content_summary,
        media_paths=media_paths,
        transcript=transcript,
        discord_message_id=None,
    )
