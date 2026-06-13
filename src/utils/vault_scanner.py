import logging
import os

logger = logging.getLogger(__name__)


def scan_saves_folders(
    saves_root: str,
    max_depth: int = 5,
    max_folders: int = 400,
) -> list[str]:
    """List existing folders under the SAVES vault, as paths Claude can reuse directly.

    Returns paths relative to the vault root (e.g. "SAVES/COOKING/BBQ/SMOKING"), sorted
    alphabetically so they read as a tree. Folders only — files are ignored. Hidden (.)
    and system (_) directories such as .obsidian, _FAILED, and _UNSORTED are skipped.

    Best-effort: returns [] if the folder is missing or unreadable, so a NAS hiccup or a
    first-run empty vault never blocks processing.
    """
    if not saves_root:
        return []
    saves_root = saves_root.rstrip("/\\")
    if not os.path.isdir(saves_root):
        return []

    # folder_path values Claude returns are relative to the vault root (write_note joins
    # them onto vault_root). saves_root == vault_root/SAVES, so its parent is vault_root.
    vault_root = os.path.dirname(saves_root)
    base_depth = saves_root.count(os.sep)
    found: list[str] = []

    try:
        for dirpath, dirnames, _files in os.walk(saves_root):
            # Prune hidden/system dirs in place; sort for stable, tree-like output
            dirnames[:] = sorted(
                d for d in dirnames
                if not d.startswith(".") and not d.startswith("_")
            )
            # Stop descending past max_depth
            if dirpath.count(os.sep) - base_depth >= max_depth:
                dirnames[:] = []
                continue
            for d in dirnames:
                rel = os.path.relpath(os.path.join(dirpath, d), vault_root).replace("\\", "/")
                found.append(rel)
    except OSError as e:
        logger.warning("Vault folder scan failed for %s: %s", saves_root, e)
        return []

    found.sort()
    if len(found) > max_folders:
        logger.info("Vault has %d folders; truncating to %d for the prompt", len(found), max_folders)
    return found[:max_folders]
