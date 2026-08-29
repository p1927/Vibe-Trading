"""Git-backed version history for the memory store.

Every content/quality-affecting mutation to ``~/.vibe-trading/memory/`` gets its own
git commit, so an entry's history is browsable with plain ``git log``/``git show``/
``git diff`` -- see .claude/backlog/items/2026-08-29-memory-agent-id-and-git-versioning.md.
Deliberately best-effort: a git failure must never block a memory write.
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)


def ensure_repo(memory_dir: Path) -> None:
    """Idempotent safety net for a fresh memory dir with no git repo yet.

    The one-time import of a directory that already has entries on disk should be run
    explicitly (one deliberate "v0 checkpoint" commit covering everything at once) rather
    than relying on this firing correctly as a side effect of the first live write, which
    would silently skip old entries an agent never happens to touch again.
    """
    if (memory_dir / ".git").exists():
        return
    try:
        subprocess.run(["git", "init"], cwd=memory_dir, check=True, capture_output=True)
        gitignore = memory_dir / ".gitignore"
        if not gitignore.exists():
            gitignore.write_text(".lock\ngc.log\n*.tmp\n", encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=memory_dir, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "chore: initial memory store snapshot (v0 checkpoint)"],
            cwd=memory_dir,
            check=True,
            capture_output=True,
        )
    except Exception:
        logger.debug("memory versioning: ensure_repo failed", exc_info=True)


def commit(memory_dir: Path, paths: list[Path], message: str) -> None:
    """Best-effort commit of specific paths. Never raises -- a git failure degrades to
    "no history for this write", not a broken memory write."""
    if not (memory_dir / ".git").exists():
        return
    try:
        rels = [str(p.relative_to(memory_dir)) for p in paths]
        if not rels:
            return
        subprocess.run(["git", "add", *rels], cwd=memory_dir, check=True, capture_output=True)
        result = subprocess.run(
            ["git", "diff", "--cached", "--quiet"], cwd=memory_dir, capture_output=True
        )
        if result.returncode == 0:
            return  # nothing staged, avoid an empty commit
        subprocess.run(
            ["git", "commit", "-m", message], cwd=memory_dir, check=True, capture_output=True
        )
    except Exception:
        logger.debug("memory versioning: commit failed", exc_info=True)
