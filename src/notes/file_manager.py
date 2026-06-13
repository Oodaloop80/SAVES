import hashlib
import logging
import os
import re
import shutil
import tempfile

logger = logging.getLogger(__name__)


# Cross-platform path budget. The vault is written on the NAS (Linux) but syncs
# to Windows, macOS, Android and iOS, each of which prepends its own vault root
# to the *relative* note path. The binding real-world limits are:
#   - single name component: 255 BYTES (ext4/F2FS on Linux & Android)
#   - full path: 260 CHARS (legacy Windows MAX_PATH) — the tightest constraint
# We byte-cap the filename for the component limit, and budget the relative path
# (folder/…/name.md) so a typical device vault prefix still fits under 260.
_MAX_FILENAME_BYTES = 200      # < 255-byte component limit, leaves headroom
_MAX_RELATIVE_PATH = 200       # chars; leaves ~60 for each device's vault prefix vs Win 260
_TRAILING_PUNCT = " ,;:-–—([{._"


def _truncate_to_bytes(s: str, max_bytes: int) -> str:
    """Truncate to at most max_bytes of UTF-8 without splitting a multi-byte char."""
    encoded = s.encode("utf-8")
    if len(encoded) <= max_bytes:
        return s
    return encoded[:max_bytes].decode("utf-8", "ignore")


def _safe_filename(filename: str, max_chars: int = 150) -> str:
    """Strip filesystem-illegal chars; preserve case and spaces for readable filenames.

    Truncation is OS-aware: cap at max_chars on a word boundary, then enforce the
    255-byte single-component limit (Linux/Android) via a byte cap, then strip any
    trailing separator punctuation so the name never ends mid-word or on a stray
    comma/dash/open-paren.
    """
    s = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '', filename)
    s = re.sub(r'\s+', ' ', s).strip()

    if len(s) > max_chars:
        cut = s[:max_chars]
        if " " in cut:
            cut = cut[:cut.rfind(" ")]
        s = cut

    if len(s.encode("utf-8")) > _MAX_FILENAME_BYTES:
        s = _truncate_to_bytes(s, _MAX_FILENAME_BYTES)
        if " " in s:
            s = s[:s.rfind(" ")]

    s = s.rstrip(_TRAILING_PUNCT)
    return s or "untitled"


def write_note(vault_root: str, folder_path: str, filename: str, content: str) -> str:
    """
    Create folder if needed, write note atomically. Never deletes anything.
    Returns the final absolute path of the written note.
    """
    folder_abs = os.path.join(vault_root, folder_path)
    os.makedirs(folder_abs, exist_ok=True)

    # Budget the relative path (folder/…/name.md) so the synced note stays under
    # Windows' 260-char path limit on every device. Reserve room for ".md" and a
    # possible "-NN" conflict suffix. Folders are fixed (Claude/preferences), so
    # the filename is what we shrink.
    rel_prefix = len(folder_path.replace("\\", "/")) + 1  # "<folder>/"
    name_budget = _MAX_RELATIVE_PATH - rel_prefix - len("-99") - len(".md")
    name_budget = max(16, min(150, name_budget))

    safe_name = _safe_filename(filename, max_chars=name_budget)
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
