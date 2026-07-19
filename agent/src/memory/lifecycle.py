"""Memory lifecycle management: quality scoring, decay, and garbage collection.

Provides reinforcement learning-style quality updates, Ebbinghaus-inspired
importance decay, and capacity-based garbage collection. All write operations
are guarded by file-level locking (single-writer model).

Feature flags (env vars):
    VT_MEMORY_QUALITY  – enable quality scoring / access tracking
    VT_MEMORY_GC       – enable garbage collection
    VT_MEMORY_DECAY    – enable importance decay formula
"""

from __future__ import annotations

import fcntl
import logging
import math
import os
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Generator

from src.memory.persistent import MemoryEntry, PersistentMemory

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Feature flags
# ---------------------------------------------------------------------------


def is_quality_enabled() -> bool:
    """Check if quality scoring is enabled via VT_MEMORY_QUALITY env var."""
    return os.environ.get("VT_MEMORY_QUALITY", "0") == "1"


def is_gc_enabled() -> bool:
    """Check if garbage collection is enabled via VT_MEMORY_GC env var."""
    return os.environ.get("VT_MEMORY_GC", "0") == "1"


def is_decay_enabled() -> bool:
    """Check if importance decay is enabled via VT_MEMORY_DECAY env var."""
    return os.environ.get("VT_MEMORY_DECAY", "0") == "1"


# ---------------------------------------------------------------------------
# File lock
# ---------------------------------------------------------------------------

LOCK_TIMEOUT_S = 5.0


@contextmanager
def memory_lock(memory_dir: Path) -> Generator[bool, None, None]:
    """Acquire exclusive file lock for write operations.

    Single-writer model: only one agent session writes at a time.
    On timeout (5s), the operation is skipped with a warning.

    Yields:
        True if lock acquired, False if timed out.
    """
    lock_path = memory_dir / ".lock"
    lock_path.touch(exist_ok=True)
    fd = None
    try:
        fd = open(lock_path, "w")  # noqa: SIM115
        deadline = time.monotonic() + LOCK_TIMEOUT_S
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                yield True
                return
            except (OSError, BlockingIOError):
                if time.monotonic() >= deadline:
                    logger.warning(
                        "memory_lock: timeout after %.1fs, skipping write",
                        LOCK_TIMEOUT_S,
                    )
                    yield False
                    return
                time.sleep(0.1)
    finally:
        if fd:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            except OSError:
                pass
            fd.close()


# ---------------------------------------------------------------------------
# Importance / decay
# ---------------------------------------------------------------------------

HALF_LIFE_DAYS = 14.0
DECAY_LAMBDA = math.log(2) / HALF_LIFE_DAYS
ACCESS_BOOST = 0.1
MAX_IMPORTANCE = 1.0


def compute_importance(
    quality_score: float,
    access_count: int,
    days_since_last_access: float,
) -> float:
    """Compute importance score using Ebbinghaus-inspired decay.

    importance = quality * retention
    retention  = exp(-lambda * t) + access_bonus

    Args:
        quality_score: Memory quality in [0.0, 1.0].
        access_count: Number of times recalled.
        days_since_last_access: Days since last access.

    Returns:
        Importance in [0.0, 1.0].
    """
    if not is_decay_enabled():
        return quality_score  # Fallback: importance = quality when decay disabled
    retention = math.exp(-DECAY_LAMBDA * max(0.0, days_since_last_access))
    access_bonus = min(0.3, access_count * ACCESS_BOOST)
    raw = quality_score * (retention + access_bonus)
    return min(MAX_IMPORTANCE, max(0.0, raw))


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    """Current time as ISO-8601 string."""
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())


# ---------------------------------------------------------------------------
# MemoryLifecycle
# ---------------------------------------------------------------------------


class MemoryLifecycle:
    """Lifecycle management for persistent memory: quality scoring, decay, GC.

    Wraps a PersistentMemory instance and provides reinforcement, garbage
    collection, and access tracking. All write operations are guarded by
    file-level locking.
    """

    # Event -> delta mapping
    _EVENT_DELTAS: dict[str, float] = {
        "task_success": 0.1,
        "task_failure": -0.15,
        "user_confirm": 0.2,
        "user_reject": -0.3,
        "passive_decay": -0.05,
    }

    # Safety: per-memory per-session cap
    _MAX_SESSION_DELTA = 0.5

    # GC thresholds
    ARCHIVE_THRESHOLD = 0.15
    DELETE_THRESHOLD = 0.05
    MIN_AGE_DAYS = 7
    MAX_MEMORY_COUNT = 500
    ENABLE_DELETE = False  # Tier 1: archive only

    def __init__(self, memory: PersistentMemory) -> None:
        self._memory = memory
        self._session_deltas: dict[str, float] = {}  # name -> cumulative delta

    @property
    def memory_dir(self) -> Path:
        """Return the underlying memory directory."""
        return self._memory._dir

    # ------------------------------------------------------------------
    # Reinforcement
    # ------------------------------------------------------------------

    def reinforce(self, name: str, event: str, source: str = "system") -> bool:
        """Update quality score based on usage feedback.

        Args:
            name: Memory entry name (exact match).
            event: One of "task_success", "task_failure", "user_confirm",
                   "user_reject", "passive_decay".
            source: "user" (full confidence) or "system" (0.7x discount).

        Returns:
            True if reinforced successfully, False if skipped.
        """
        if not is_quality_enabled():
            return False
        if event not in self._EVENT_DELTAS:
            logger.warning("reinforce: unknown event %r", event)
            return False

        delta = self._EVENT_DELTAS[event]
        if source == "system":
            delta *= 0.7

        # Session cap check
        current = self._session_deltas.get(name, 0.0)
        if abs(current + delta) > self._MAX_SESSION_DELTA:
            logger.info("reinforce(%s): session cap reached (%.2f)", name, current)
            return False

        entry = self._memory.find(name)
        if entry is None:
            logger.warning("reinforce(%s): not found", name)
            return False

        with memory_lock(self.memory_dir) as acquired:
            if not acquired:
                return False
            try:
                new_qs = max(0.0, min(1.0, entry.quality_score + delta))
                self._update_frontmatter_field(
                    entry.path, "quality_score", f"{new_qs:.2f}"
                )
                self._update_frontmatter_field(entry.path, "updated_at", _now_iso())
                self._session_deltas[name] = current + delta
                return True
            except (FileNotFoundError, IOError) as exc:
                logger.warning("reinforce(%s) skipped: %s", name, exc)
                return False

    # ------------------------------------------------------------------
    # Access tracking
    # ------------------------------------------------------------------

    def track_access(self, entry: MemoryEntry) -> None:
        """Increment access_count and update last_accessed for a recalled entry."""
        if not is_quality_enabled():
            return
        with memory_lock(self.memory_dir) as acquired:
            if not acquired:
                return
            try:
                self._update_frontmatter_field(
                    entry.path, "access_count", str(entry.access_count + 1)
                )
                self._update_frontmatter_field(
                    entry.path, "last_accessed", _now_iso()
                )
            except (FileNotFoundError, IOError) as exc:
                logger.warning("track_access(%s) skipped: %s", entry.title, exc)

    # ------------------------------------------------------------------
    # Garbage collection
    # ------------------------------------------------------------------

    def run_gc(self, dry_run: bool = True) -> list[dict]:
        """Run garbage collection on memory store.

        Args:
            dry_run: If True (default), log actions without modifying files.

        Returns:
            List of action records [{name, action, importance, reason}].
        """
        if not is_gc_enabled():
            return []

        entries = self._memory.list_entries()
        if len(entries) <= self.MAX_MEMORY_COUNT and dry_run:
            return []  # Under capacity in dry-run = nothing to report

        now = time.time()
        actions: list[dict] = []

        for entry in entries:
            age_days = (now - entry.created_at) / 86400.0
            if age_days < self.MIN_AGE_DAYS:
                continue

            days_since_access = (now - entry.last_accessed) / 86400.0
            imp = compute_importance(
                entry.quality_score, entry.access_count, days_since_access
            )

            action = None
            reason = ""
            if imp < self.DELETE_THRESHOLD and self.ENABLE_DELETE:
                action = "delete"
                reason = f"importance {imp:.3f} < delete threshold"
            elif imp < self.ARCHIVE_THRESHOLD:
                action = "archive"
                reason = f"importance {imp:.3f} < archive threshold"

            if action:
                record = {
                    "name": entry.title,
                    "action": action,
                    "importance": round(imp, 4),
                    "reason": reason,
                }
                actions.append(record)
                if not dry_run:
                    # Tier 1: force archive even if classified as delete
                    effective = "archive" if not self.ENABLE_DELETE else action
                    self._execute_gc_action(entry, effective)

        self._append_gc_log(actions, dry_run)
        return actions

    def _execute_gc_action(self, entry: MemoryEntry, action: str) -> None:
        """Execute a GC action (archive or delete) on an entry."""
        archive_dir = self.memory_dir / "archive"
        archive_dir.mkdir(exist_ok=True)

        with memory_lock(self.memory_dir) as acquired:
            if not acquired:
                return
            if action == "archive":
                dest = archive_dir / entry.path.name
                entry.path.rename(dest)
            elif action == "delete":
                dest = archive_dir / entry.path.name
                dest.write_text(
                    entry.path.read_text(encoding="utf-8"), encoding="utf-8"
                )
                entry.path.unlink()

        # Rebuild index after removal
        self._memory._rebuild_index()

    def _append_gc_log(self, actions: list[dict], dry_run: bool) -> None:
        """Append GC decisions to gc.log."""
        log_path = self.memory_dir / "gc.log"
        timestamp = _now_iso()
        mode = "dry_run" if dry_run else "execute"
        lines = [f"[{timestamp}] mode={mode} actions={len(actions)}"]
        for a in actions:
            lines.append(
                f"  {a['action']}: {a['name']} "
                f"(importance={a['importance']}, {a['reason']})"
            )
        lines.append("")

        with open(log_path, "a", encoding="utf-8") as f:
            f.write("\n".join(lines))

    # ------------------------------------------------------------------
    # Frontmatter manipulation
    # ------------------------------------------------------------------

    def _update_frontmatter_field(
        self, path: Path, field: str, value: str
    ) -> None:
        """Update a single frontmatter field in a memory file."""
        text = path.read_text(encoding="utf-8")
        lines = text.split("\n")

        # Find frontmatter boundaries
        if not lines or lines[0].strip() != "---":
            return
        end_idx = None
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                end_idx = i
                break
        if end_idx is None:
            return

        # Update or insert field
        field_found = False
        for i in range(1, end_idx):
            if lines[i].startswith(f"{field}:"):
                lines[i] = f"{field}: {value}"
                field_found = True
                break
        if not field_found:
            lines.insert(end_idx, f"{field}: {value}")

        path.write_text("\n".join(lines), encoding="utf-8")
