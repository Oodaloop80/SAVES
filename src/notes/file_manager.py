import hashlib
import logging
import os
import re
import shutil
import tempfile

logger = logging.getLogger(__name__)


def _safe_filename(filename: str, max_len: int = 80) -> str:
    """Strip filesystem-illegal chars; preserve case and spaces for readable filenames."""
    s = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '', filename)
    s = re.sub(r'\s+', ' ', s).strip()
    return s[:max_len] or "untitled"


def write_note(vault_root: str, folder_path: str, filename: str, content: str) -> str:
    """
    Create folder if needed, write note atomically. Never deletes anything.
    Returns the final absolute path of the written note.
    """
    folder_abs = os.path.join(vault_root, folder_path)
    os.makedirs(folder_abs, exist_ok=True)

    safe_name = _safe_filename(filename)
    dest = os.path.join(folder_abs, safe_name + ".md")

    # Conflict resolution — never overwrite
    if os.path.exists(dest):
        n = 2
        while True:
            candidate = os.path.join(folder_abs, f"{safe_name}-{n}.md")
            if not os.path.exists(candidate):
                dest = candidate
                break
            n += 1

    # Atomic write
    dir_name = os.path.dirname(dest)
    fd, tmp = tempfile.mkstemp(dir=dir_name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp, dest)
    except Exception:
        raise

    logger.info(f"Note written: {dest}")
    return dest


def move_note(src: str, new_vault_path: str, vault_root: str) -> str:
    """
    Move/rename a note. Atomic on same filesystem; copy+verify on cross-volume.
    Never deletes — renames source to .bak on cross-volume.
    Returns new path.
    """
    dest = os.path.join(vault_root, new_vault_path)
    os.makedirs(os.path.dirname(dest), exist_ok=True)

    try:
        os.rename(src, dest)
        return dest
    except OSError:
        # Cross-volume: copy → verify → rename source to .bak
        shutil.copy2(src, dest)
        src_hash = _sha256(src)
        dest_hash = _sha256(dest)
        if src_hash == dest_hash:
            os.rename(src, src + ".bak")
        else:
            raise RuntimeError(f"Copy verification failed: {src} → {dest}")
        return dest


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()
