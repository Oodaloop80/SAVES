"""Vault-wide tag index: what tags exist, and how often each is used.

Scans every note for tags — frontmatter `tags:`/`tag:` plus inline body `#tags`, i.e.
everything Obsidian itself counts as a tag — and keeps a usage count. Powers:
  - the `/tag add` slash command's search-as-you-type autocomplete,
  - the Edit-Tags modal's "did you mean an existing tag?" fuzzy check,
  - the analysis prompt's existing-tags hint (so Claude reuses the vault's taxonomy
    instead of inventing near-duplicates like `airfryer` next to `air-fryer`).

Read-only on the vault. Refreshes lazily on a TTL; `add()` bumps counts incrementally
when SAVES writes a note so brand-new tags are searchable before the next rescan.

⚠️ Threading contract: `refresh()` (and the methods that trigger it — `search`, `top`,
`close_matches`) walk the ENTIRE vault and read every note, so on a large vault a rescan
can take seconds. Callers running on the asyncio event loop (the Discord autocomplete
callbacks and the Add-Tags modal) MUST invoke those methods via `asyncio.to_thread` so a
rescan never blocks the loop and freezes the bot. The processor already does. `count()` and
`add()` are O(1)/cheap and never rescan, so they're safe to call inline.
"""

import difflib
import logging
import os
import re
import threading
import time
from collections import Counter

import yaml

logger = logging.getLogger(__name__)


def clean_tags(tags) -> list[str]:
    """Normalize tags to the canonical form SAVES writes: lowercase, stripped, no leading
    '#'. Mirrors clean_folder_path for paths — applied at EVERY tag entry point (AI
    generation in the processor, Add-Tags modal, /tag add, NL edit, near-dup swap) so
    case-variant duplicate tags (BBQ vs bbq) can't happen. Order-preserving; collisions
    that only differ by case/whitespace dedupe to the first occurrence."""
    out: list[str] = []
    for t in tags or []:
        if not t:  # None/empty would otherwise stringify to a literal "none"/"" tag
            continue
        t = str(t).strip().lstrip("#").lower()
        if t and t not in out:
            out.append(t)
    return out

# Notes can be long (full localized articles); 256KB covers them while keeping a big
# vault scan bounded.
_MAX_NOTE_BYTES = 262144
_REFRESH_TTL_SECONDS = 300
# Vault housekeeping dirs that hold no user notes.
_SKIP_DIRS = {".obsidian", ".trash", ".git", ".stfolder", ".stversions"}

# Inline body tags, Obsidian-style: `#tag`, `#air-fryer`, `#food/recipes` — only when
# preceded by start-of-line/whitespace so URL anchors (example.com#section) don't match.
_INLINE_TAG_RE = re.compile(r"(?:^|(?<=\s))#([\w/-]+)")
# Obsidian requires at least one non-numeric character in a tag ("#2024" is not a tag).
_NUMERIC_ONLY_RE = re.compile(r"[\d/_-]+")
_CODE_FENCE_RE = re.compile(r"```.*?(?:```|\Z)", re.DOTALL)
_INLINE_CODE_RE = re.compile(r"`[^`\n]*`")


def _normalize(tag: str) -> str:
    """Comparison key for near-duplicate detection: `Air-Fryer` == `air_fryer` == `airfryer`."""
    return re.sub(r"[^a-z0-9]", "", tag.lower())


def _parse_frontmatter_tags(fm_text: str) -> list[str]:
    """Tags from a frontmatter YAML block — both `tags:` and Obsidian's legacy `tag:` key,
    in list form or inline string form ("tag1, tag2")."""
    try:
        fm = yaml.safe_load(fm_text)
    except yaml.YAMLError:
        return []
    if not isinstance(fm, dict):
        return []
    out: list[str] = []
    for key in ("tags", "tag"):
        raw = fm.get(key)
        if raw is None:
            continue
        if isinstance(raw, str):
            raw = [t for t in re.split(r"[,\s]+", raw) if t]
        if not isinstance(raw, list):
            continue
        for t in raw:
            if isinstance(t, str):
                t = t.strip().lstrip("#")
                if t and t not in out:
                    out.append(t)
    return out


def _note_tags(path: str) -> list[str]:
    """Every tag in one note, deduplicated: frontmatter `tags:`/`tag:` PLUS inline `#tags`
    in the body — Obsidian treats both as tags, so tags added by hand in the editor count
    the same as frontmatter ones written by SAVES. Code blocks are stripped first so
    `#include` in a fenced snippet isn't mistaken for a tag. Empty list on any problem —
    a single malformed note must never break the vault scan."""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            text = f.read(_MAX_NOTE_BYTES)
    except OSError:
        return []
    out: list[str] = []
    body = text
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            out.extend(_parse_frontmatter_tags(text[3:end]))
            body = text[end + 4:]
    body = _CODE_FENCE_RE.sub(" ", body)
    body = _INLINE_CODE_RE.sub(" ", body)
    for m in _INLINE_TAG_RE.finditer(body):
        t = m.group(1).strip("/")
        if t and not _NUMERIC_ONLY_RE.fullmatch(t) and t not in out:
            out.append(t)
    return out


class TagIndex:
    def __init__(self, vault_root: str):
        self.vault_root = vault_root
        self._counts: Counter[str] = Counter()
        self._scanned_at: float = 0.0
        # Collapses a burst of concurrent rescans into one vault walk: with autocomplete
        # reads now dispatched through asyncio.to_thread, several keystrokes could each land
        # in a worker thread with an expired TTL — the lock + double-check below means only
        # the first walks; the rest see the fresh index and return. Readers of an
        # already-built _counts never take the lock.
        self._lock = threading.Lock()

    def refresh(self, force: bool = False) -> None:
        """Rebuild the tag counts from the vault when the TTL has expired. See the module
        docstring's threading contract: on the event loop, call this (via search/top/
        close_matches) inside asyncio.to_thread — it can take seconds on a big vault."""
        if not force and (time.time() - self._scanned_at) < _REFRESH_TTL_SECONDS:
            return
        with self._lock:
            # Double-checked: another thread may have rebuilt the index while we waited for
            # the lock — if so, skip the (expensive) walk entirely.
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
                    for tag in _note_tags(os.path.join(dirpath, fname)):
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
