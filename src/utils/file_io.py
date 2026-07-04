import os
import tempfile

from src.utils.url_parser import extract_urls, normalize_url


def read_inbox(path: str) -> list[str]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.readlines()
    except FileNotFoundError:
        return []


def remove_url_from_inbox(path: str, url: str) -> None:
    """Atomically remove a URL line from the inbox file. Never deletes the file.

    Uses normalized URL comparison so a URL that is a substring or prefix of another
    URL on a different line does not cause that line to be removed.
    """
    target_norm = normalize_url(url)
    lines = read_inbox(path)
    filtered = [
        line for line in lines
        if not any(normalize_url(u) == target_norm for u in extract_urls(line))
    ]
    if len(filtered) == len(lines):
        return  # URL not found — nothing to do
    dir_name = os.path.dirname(os.path.abspath(path))
    fd, tmp_path = tempfile.mkstemp(dir=dir_name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.writelines(filtered)
        os.replace(tmp_path, path)
    except Exception:
        raise  # tmp file is orphaned but never deletes user content
