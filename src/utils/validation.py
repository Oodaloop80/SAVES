import logging
import os

logger = logging.getLogger(__name__)


class ConfigError(Exception):
    """Raised at startup when required configuration is missing or empty."""


# Path config keys the pipeline cannot run without.
_REQUIRED_PATHS = ("vault_root", "media_root", "inbox_file")
# Discord channel names every deploy needs (approvals, logging, alerts).
_REQUIRED_CHANNELS = ("channel_approvals", "channel_log", "channel_alerts")


def validate_startup(config: dict) -> None:
    """Fail fast on missing/empty configuration before the pipeline starts.

    Collects *all* problems and raises a single ConfigError listing them, so a
    misconfigured deploy surfaces every issue at once instead of one-per-restart.

    Only missing config *values* are hard failures. Directory *existence* is a soft
    warning: the NAS mount can lag at boot, the note-writer creates folders on demand,
    and the file watcher tolerates a missing inbox directory (see FileWatcher.start).
    Credential env vars are validated separately by ``credentials.load_credentials``.
    """
    problems: list[str] = []

    paths = config.get("paths", {})
    for key in _REQUIRED_PATHS:
        if not str(paths.get(key, "")).strip():
            problems.append(f"paths.{key} is not set in config.yaml")

    discord_cfg = config.get("discord", {})
    for key in _REQUIRED_CHANNELS:
        if not str(discord_cfg.get(key, "")).strip():
            problems.append(f"discord.{key} is not set in config.yaml")

    if problems:
        raise ConfigError(
            "Startup configuration invalid — fix these before running:\n  - "
            + "\n  - ".join(problems)
        )

    # Soft warnings: configured directories that don't exist yet. Not fatal (mount may
    # lag; folders are created on demand), but surfacing them early makes a silent
    # misconfiguration (e.g. an un-mounted NAS or a typo'd path) obvious in the log.
    for key in ("vault_root", "media_root"):
        d = str(paths.get(key, "")).strip()
        if d and not os.path.isdir(d):
            logger.warning("Configured paths.%s does not exist yet: %s", key, d)

    inbox_dir = os.path.dirname(str(paths.get("inbox_file", "")).strip())
    if inbox_dir and not os.path.isdir(inbox_dir):
        logger.warning(
            "Inbox directory does not exist yet: %s — the file watcher will not start "
            "until it exists (URLs can still be processed on the next restart).",
            inbox_dir,
        )
