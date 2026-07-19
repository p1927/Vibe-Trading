"""PersistentMemory: file-based cross-session memory, zero external dependencies.

Storage layout:
    ~/.vibe-trading/memory/
    +-- MEMORY.md          # Index (< 200 lines)
    +-- user_prefs.md      # Individual memory entries with YAML frontmatter
    +-- project_btc.md
    +-- ...
"""

from __future__ import annotations

import hashlib
import logging
import re
import time as _time
from dataclasses import dataclass
from pathlib import Path

from src.agent.frontmatter import parse_frontmatter as _parse_frontmatter
from typing import List, Optional

logger = logging.getLogger(__name__)

MEMORY_BASE = Path.home() / ".vibe-trading" / "memory"
MAX_INDEX_LINES = 200
MAX_ENTRY_CHARS = 8000
MAX_RESULTS = 5
METADATA_WEIGHT = 2.0
MEMORY_TYPES = ("user", "feedback", "project", "reference")

# Script ranges tokenized and slugged at char level (no word-boundary
# whitespace). Arabic/Hebrew narrowed to letter blocks to exclude bidi
# controls and combining marks from on-disk slugs.
_NON_LATIN_SCRIPT_RANGES = (
    "一-鿿"   # CJK Unified Ideographs   (U+4E00-U+9FFF)
    "㐀-䶿"   # CJK Extension A          (U+3400-U+4DBF)
    "฀-๿"   # Thai                     (U+0E00-U+0E7F)
    "ؠ-ي"   # Arabic letters           (U+0620-U+064A)
    "א-ת"   # Hebrew letters           (U+05D0-U+05EA)
    "Ѐ-ӿ"   # Cyrillic                 (U+0400-U+04FF)
)

_TOKEN_RE = re.compile(rf"[a-zA-Z0-9]{{3,}}|[{_NON_LATIN_SCRIPT_RANGES}]")
_SLUG_DISALLOWED_RE = re.compile(rf"[^a-z0-9_\-{_NON_LATIN_SCRIPT_RANGES}]")


@dataclass(frozen=True)
class MemoryEntry:
    """A single memory entry on disk."""

    path: Path
    title: str
    description: str
    memory_type: str
    body: str
    modified_at: float
    # Phase 1 fields — all have defaults for backward compatibility
    id: str = ""                    # 6-char hex; generated from name+mtime if empty
    created_at: float = 0.0         # epoch seconds; 0 means use modified_at
    updated_at: float = 0.0         # epoch seconds; 0 means use modified_at
    keywords: tuple[str, ...] = ()  # max 5 tags
    quality_score: float = 0.5      # [0.0, 1.0]; neutral default
    access_count: int = 0           # cumulative recall hits
    last_accessed: float = 0.0      # epoch; 0 means use modified_at
    importance: float = 0.5         # computed via decay formula
    related_memories: tuple[str, ...] = ()  # linked IDs


def _tokenize(text: str) -> set[str]:
    """Split text into searchable tokens.

    ASCII words >= 3 chars + individual characters from non-Latin scripts.
    Underscores are treated as word boundaries so snake_case titles match
    natural-language queries.
    """
    return set(_TOKEN_RE.findall(text.lower()))


# Strip C0 (U+0000-U+001F except \t \n) and C1 (U+0080-U+009F) bytes.
_CONTROL_CHAR_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")

# Truncation marker appended when content exceeds MAX_ENTRY_CHARS.
_TRUNCATION_MARKER = "\n\n[truncated at {limit} chars]\n"


def _sanitize_body(content: str) -> str:
    """Strip C0/C1 control bytes from `content` while keeping ``\\n`` and ``\\t``."""
    return _CONTROL_CHAR_RE.sub("", content)


def _truncate_body(content: str, limit: int = None) -> str:
    """Clip `content` to `limit` chars, leaving room for the marker."""
    if limit is None:
        limit = MAX_ENTRY_CHARS
    if len(content) <= limit:
        return content
    marker = _TRUNCATION_MARKER.format(limit=limit)
    head_len = max(0, limit - len(marker))
    return content[:head_len] + marker


def _coerce_str(value: object, default: str = "") -> str:
    """Coerce frontmatter values to a display string.

    Handles lists (``[a, b]``), bools, None, etc.
    """
    if value is None:
        return default
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, list):
        return ", ".join(str(v) for v in value)
    return str(value)


def _parse_timestamp(value: object, fallback: float) -> float:
    """Parse a timestamp from frontmatter. Returns epoch float."""
    if value is None:
        return fallback
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            from datetime import datetime
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return dt.timestamp()
        except (ValueError, TypeError):
            pass
    return fallback


class PersistentMemory:
    """File-based persistent memory that survives across sessions.

    Frozen snapshot injected into system prompt at session start.
    Disk writes via add()/remove() update files immediately but do NOT
    change the snapshot. Next session picks up the updated state.
    """

    def __init__(self, memory_dir: Optional[Path] = None) -> None:
        self._dir = memory_dir or MEMORY_BASE
        self._dir.mkdir(parents=True, exist_ok=True)
        self._index_path = self._dir / "MEMORY.md"
        self._snapshot: str = ""
        self._load_snapshot()

    def _load_snapshot(self) -> None:
        """Load index as frozen snapshot. Called once at init."""
        if self._index_path.exists():
            try:
                text = self._index_path.read_text(encoding="utf-8")
                lines = text.split("\n")[:MAX_INDEX_LINES]
                self._snapshot = "\n".join(lines)
            except OSError:
                self._snapshot = ""

    @property
    def snapshot(self) -> str:
        """Frozen memory index for system prompt injection."""
        return self._snapshot

    def _scan_entries(self) -> List[MemoryEntry]:
        """Scan all .md files (except MEMORY.md) and parse frontmatter."""
        entries: List[MemoryEntry] = []
        for path in sorted(self._dir.glob("*.md")):
            if path.name == "MEMORY.md":
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except OSError:
                continue
            meta, body = _parse_frontmatter(text)
            mtime = path.stat().st_mtime

            # Parse new fields with safe defaults
            raw_kw = meta.get("keywords", [])
            keywords = tuple(
                str(k)[:30] for k in (raw_kw if isinstance(raw_kw, list) else [])
            )[:5]

            raw_related = meta.get("related_memories", [])
            related = tuple(
                str(r) for r in (raw_related if isinstance(raw_related, list) else [])
                if isinstance(r, str) and len(r) == 6
            )

            qs = meta.get("quality_score", 0.5)
            try:
                qs = max(0.0, min(1.0, float(qs)))
            except (TypeError, ValueError):
                qs = 0.5

            ac = meta.get("access_count", 0)
            try:
                ac = max(0, int(ac))
            except (TypeError, ValueError):
                ac = 0

            # Generate id if missing
            entry_id = str(meta.get("id", ""))
            if not entry_id or len(entry_id) != 6:
                entry_id = hashlib.sha256(
                    f"{meta.get('name', path.stem)}{mtime}".encode()
                ).hexdigest()[:6]

            # Parse timestamps
            created = _parse_timestamp(meta.get("created_at"), mtime)
            updated = _parse_timestamp(meta.get("updated_at"), mtime)
            last_acc = _parse_timestamp(meta.get("last_accessed"), mtime)

            entries.append(MemoryEntry(
                path=path,
                title=_coerce_str(meta.get("name"), default=path.stem),
                description=_coerce_str(meta.get("description")),
                memory_type=_coerce_str(meta.get("type"), default="project"),
                body=body[:MAX_ENTRY_CHARS],
                modified_at=mtime,
                id=entry_id,
                created_at=created,
                updated_at=updated,
                keywords=keywords,
                quality_score=qs,
                access_count=ac,
                last_accessed=last_acc,
                importance=0.5,
                related_memories=related,
            ))
        return entries

    def list_entries(self) -> List[MemoryEntry]:
        """Return all persisted memory entries, filename-sorted."""
        return self._scan_entries()

    def find(self, name: str) -> Optional[MemoryEntry]:
        """Resolve a memory by exact title, then by on-disk filename stem.

        Stem fallback accepts both the full ``{type}_{slug}`` form and the
        bare ``slug`` suffix.
        """
        needle = name.strip()
        if not needle:
            return None
        entries = self._scan_entries()
        for entry in entries:
            if entry.title == needle:
                return entry
        for entry in entries:
            stem = entry.path.stem
            if stem == needle or stem.endswith(f"_{needle}"):
                return entry
        return None

    def remove_entry(self, entry: MemoryEntry) -> bool:
        """Delete a resolved entry without re-scanning to find it again."""
        try:
            entry.path.unlink(missing_ok=True)
        except OSError as exc:
            logger.warning("Failed to remove memory entry %s: %s", entry.path, exc)
            return False
        self._rebuild_index()
        return True

    def find_relevant(self, query: str, max_results: int = MAX_RESULTS) -> List[MemoryEntry]:
        """Keyword search across all memory entries.

        Scoring: (metadata_hits + keyword_hits) * 2.0 + body_hits * 1.0,
        weighted by importance.
        """
        query_tokens = _tokenize(query)
        if not query_tokens:
            return []

        scored: list[tuple[float, MemoryEntry]] = []
        for entry in self._scan_entries():
            meta_tokens = _tokenize(f"{entry.title} {entry.description}")
            body_tokens = _tokenize(entry.body)
            kw_tokens = _tokenize(" ".join(entry.keywords))
            token_score = (
                len(query_tokens & meta_tokens) * METADATA_WEIGHT
                + len(query_tokens & kw_tokens) * METADATA_WEIGHT
                + len(query_tokens & body_tokens)
            )
            if token_score > 0:
                final_score = token_score * (0.5 + 0.5 * entry.importance)
                scored.append((final_score, entry))

        scored.sort(key=lambda x: (-x[0], -x[1].modified_at))
        return [entry for _, entry in scored[:max_results]]

    def add(self, name: str, content: str, memory_type: str = "project",
            description: str = "") -> Path:
        """Save a new memory entry and update the index.

        Raises:
            ValueError: If `name` is empty/whitespace-only or type is invalid.
        """
        stripped_name = name.strip()
        if not stripped_name:
            raise ValueError("memory name must not be empty or whitespace-only")

        if memory_type not in MEMORY_TYPES:
            allowed = ", ".join(MEMORY_TYPES)
            raise ValueError(f"memory_type must be one of: {allowed}")

        # Preserve non-Latin script characters in the slug
        slug = _SLUG_DISALLOWED_RE.sub("_", stripped_name.lower())[:60]

        # If slug normalized to all underscores, append a hash
        if slug.strip("_") == "":
            digest = hashlib.sha256(stripped_name.encode("utf-8")).hexdigest()[:6]
            slug = f"{slug}_{digest}" if slug else digest

        filename = f"{memory_type}_{slug}.md"
        path = self._dir / filename

        safe_name = stripped_name.replace("\n", " ").replace("\r", " ")
        safe_desc = (description or stripped_name).replace("\n", " ").replace("\r", " ")

        clean_content = _truncate_body(_sanitize_body(content))

        # Generate Phase 1 metadata
        entry_id = hashlib.sha256(
            f"{stripped_name}{_time.time()}".encode()
        ).hexdigest()[:6]
        now_iso = _time.strftime("%Y-%m-%dT%H:%M:%S", _time.gmtime())

        frontmatter = (
            f"---\nname: {safe_name}\n"
            f"description: {safe_desc}\n"
            f"type: {memory_type}\n"
            f"id: {entry_id}\n"
            f"created_at: {now_iso}\n"
            f"updated_at: {now_iso}\n"
            f"keywords: []\n"
            f"quality_score: 0.5\n"
            f"access_count: 0\n"
            f"last_accessed: {now_iso}\n"
            f"importance: 0.5\n"
            f"related_memories: []\n"
            f"---\n\n"
            f"{clean_content}"
        )
        path.write_text(frontmatter, encoding="utf-8")
        self._update_index(stripped_name, filename, description or stripped_name)
        return path

    def remove(self, name: str) -> bool:
        """Remove a memory entry by name. Returns True if found and removed."""
        for entry in self._scan_entries():
            if entry.title == name:
                entry.path.unlink(missing_ok=True)
                self._rebuild_index()
                return True
        return False

    def _update_index(self, title: str, filename: str, description: str) -> None:
        """Append or update an entry in MEMORY.md."""
        new_line = f"- [{title}]({filename}) — {description}"

        if self._index_path.exists():
            lines = self._index_path.read_text(encoding="utf-8").split("\n")
            updated = False
            for i, line in enumerate(lines):
                if f"[{title}]" in line:
                    lines[i] = new_line
                    updated = True
                    break
            if not updated:
                lines.append(new_line)
            text = "\n".join(lines[:MAX_INDEX_LINES])
        else:
            text = new_line

        self._index_path.write_text(text, encoding="utf-8")

    def _rebuild_index(self) -> None:
        """Rebuild MEMORY.md from all existing entry files."""
        entries = self._scan_entries()
        lines = [f"- [{e.title}]({e.path.name}) — {e.description}" for e in entries]
        self._index_path.write_text("\n".join(lines[:MAX_INDEX_LINES]), encoding="utf-8")
