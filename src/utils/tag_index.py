"""Vault-wide tag index: what tags exist, and how often each is used.

Scans every note's YAML frontmatter for `tags:` and keeps a usage count. Powers:
  - the `/tag add` slash command's search-as-you-type autocomplete,
  - the Edit-Tags modal's "did you mean an existing tag?" fuzzy check,
  - the analysis prompt's existing-tags hint (so Claude reuses the vault's taxonomy
    instead of inventing near-duplicates like `airfryer` next to `air-fryer`).

Read-only on the vault. Refreshes lazily on a TTL; `add()` bumps counts incrementally
when SAVES writes a note so brand-new tags are searchable before the next rescan.
"""

import difflib
import logging
import os
import re
import time
from collections import Counter

import yaml

logger = logging.getLogger(__name__)

# Frontmatter lives at the top of the file; reading the whole note just for it would make
# large vault scans needlessly slow. 8KB covers any sane frontmatter block.
_HEAD_BYTES = 8192
_REFRESH_TTL_SECONDS = 300
# Vault housekeeping dirs that hold no user notes.
_SKIP_DIRS = {".obsidian", ".trash", ".git", ".stfolder", ".stversions"}


def _normalize(tag: str) -> str:
    """Comparison key for near-duplicate detection: `Air-Fryer` == `air_fryer` == `airfryer`."""
    return re.sub(r"[^a-z0-9]", "", tag.lower())


def _frontmatter_tags(path: str) -> list[str]:
    """Extract the frontmatter `tags:` list from one note. Empty list on any problem —
    a single malformed note must never break the vault scan."""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            head = f.read(_HEAD_BYTES)
    except OSError:
        return []
    if not head.startswith("---"):
        return []
    end = head.find("\n---", 3)
    if end == -1:
        return []
    try:
        fm = yaml.safe_load(head[3:end])
    except yaml.YAMLError:
        return []
    if not isinstance(fm, dict):
        return []
    raw = fm.get("tags")
    if raw is None:
        return []
    if isinstance(raw, str):
        # Inline forms: "tag1, tag2" or "tag1 tag2"
        raw = [t for t in re.split(r"[,\s]+", raw) if t]
    if not isinstance(raw, list):
        return []
    out = []
    for t in raw:
        if isinstance(t, str):
            t = t.strip().lstrip("#")
            if t:
                out.append(t)
    return out


class TagIndex:
    def __init__(self, vault_root: str):
        self.vault_root = vault_root
        self._counts: Counter[str] = Counter()
        self._scanned_at: float = 0.0

    def refresh(self, force: bool = False) -> None:
        if not force and (time.time() - self._scanned_at) < _REFRESH_TTL_SECONDS:
            return
        counts: Counter[str] = Counter()
        n_files = 0
        for dirpath, dirnames, filenames in os.walk(self.vault_root):
            dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
            for fname in filenames:
                if not fname.endswith(".md"):
                    continue
                n_files += 1
                for tag in _frontmatter_tags(os.path.join(dirpath, fname)):
                    counts[tag] += 1
        # Swap in one assignment so concurrent readers never see a half-built index.
        self._counts = counts
        self._scanned_at = time.time()
        logger.debug("Tag index: %d tags across %d notes", len(counts), n_files)

    def add(self, tags: list[str]) -> None:
        """Incremental bump when a note is written — keeps new tags searchable
        without waiting for the TTL rescan."""
        for t in tags or []:
            t = (t or "").strip().lstrip("#")
            if t:
                self._counts[t] += 1

    def search(self, query: str, limit: int = 25) -> list[tuple[str, int]]:
        """Substring match (case-insensitive), most-used first. Empty query → top tags."""
        self.refresh()
        q = query.strip().lstrip("#").lower()
        items = self._counts.most_common()
        if q:
            items = [(t, c) for t, c in items if q in t.lower()]
        return items[:limit]

    def top(self, n: int = 50) -> list[str]:
        self.refresh()
        return [t for t, _ in self._counts.most_common(n)]

    def close_matches(self, tag: str, n: int = 3) -> list[str]:
        """Existing tags that are near-duplicates of `tag` but not the same tag.
        Catches punctuation/case variants (airfryer → air-fryer) and typos."""
        self.refresh()
        key = _normalize(tag)
        if not key:
            return []
        out = []
        # Exact normalized collisions first — these are always worth flagging.
        for existing in self._counts:
            if existing != tag and _normalize(existing) == key:
                out.append(existing)
        # Then fuzzy (typo-distance) matches on the normalized forms.
        by_norm = {_normalize(t): t for t in self._counts if _normalize(t) != key}
        for m in difflib.get_close_matches(key, by_norm.keys(), n=n, cutoff=0.85):
            cand = by_norm[m]
            if cand not in out and cand != tag:
                out.append(cand)
        return out[:n]

    def count(self, tag: str) -> int:
        return self._counts.get(tag, 0)


_instances: dict[str, TagIndex] = {}


def get_tag_index(config: dict) -> TagIndex:
    """Process-wide singleton per vault root (processor, bot, and slash commands all
    share one index so incremental `add()` bumps are visible everywhere)."""
    root = os.path.realpath(config.get("paths", {}).get("vault_root", "/vault"))
    idx = _instances.get(root)
    if idx is None:
        idx = _instances[root] = TagIndex(root)
    return idx
