import json
import logging
import os
import tempfile

logger = logging.getLogger(__name__)


def get_source_key(platform: str, metadata: dict, author: str | None) -> str | None:
    """Build the preferences.json lookup key for a piece of content."""
    if platform == "reddit":
        subreddit = metadata.get("subreddit")
        if subreddit:
            return f"reddit:r/{subreddit}"
    elif platform == "youtube":
        if author:
            return f"youtube:{author}"
    elif platform in ("instagram", "tiktok", "facebook"):
        if author:
            handle = author.lstrip("@")
            return f"{platform}:{handle}"
    elif platform == "generic":
        domain = metadata.get("domain")
        if domain:
            return f"domain:{domain}"
    return None


class PreferencesStore:
    def __init__(self, path: str, enabled: bool = True):
        self.path = path
        self.enabled = enabled
        self._data: dict[str, str] = {}
        if enabled:
            self._load()

    def _load(self) -> None:
        if not os.path.exists(self.path):
            return
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            self._data = raw.get("sources", {})
        except Exception as e:
            logger.warning(f"Could not load preferences.json: {e}")

    def _save(self) -> None:
        dir_name = os.path.dirname(os.path.abspath(self.path)) or "."
        fd, tmp = tempfile.mkstemp(dir=dir_name, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump({"sources": self._data}, f, indent=2)
            os.replace(tmp, self.path)
        except Exception:
            raise

    def get(self, source_key: str | None) -> str | None:
        if not self.enabled or not source_key:
            return None
        return self._data.get(source_key)

    def set(self, source_key: str | None, folder_path: str) -> None:
        if not self.enabled or not source_key:
            return
        self._data[source_key] = folder_path
        self._save()
        logger.info(f"Preference saved: {source_key} → {folder_path}")

    def hint(self, source_key: str | None) -> str | None:
        """Return a human-readable hint string for injection into Claude's prompt."""
        path = self.get(source_key)
        if path:
            return f"Previously filed at {path}. Use this path unless content clearly belongs elsewhere."
        return None
