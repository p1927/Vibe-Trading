"""Git-backed version history for the memory store.

Every content/quality-affecting mutation to ``~/.vibe-trading/memory/`` gets its own
git commit, so an entry's history is browsable with plain ``git log``/``git show``/
``git diff`` -- see .claude/backlog/items/2026-08-29-memory-agent-id-and-git-versioning.md.
Deliberately best-effort: a git failure must never block a memory write.
"""

from __future__ import annotations

import logging
import re
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

_SHA_RE = re.compile(r"^[0-9a-fA-F]{4,40}$")


class VersioningError(RuntimeError):
    """A git history/diff read failed. Unlike commit()/ensure_repo() (best-effort, used on the
    write path), these are read operations the API layer needs to translate into a real HTTP
    error rather than silently swallow."""


def _validate_sha(sha: str) -> str:
    """Reject anything that isn't a plain hex commit id, so a caller-supplied sha can never be
    interpreted as a git option (e.g. a string starting with "-")."""
    if not _SHA_RE.match(sha):
        raise VersioningError(f"invalid commit sha: {sha!r}")
    return sha


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


def log_for_path(memory_dir: Path, path: Path, *, limit: int = 50) -> list[dict]:
    """Commit history for one file, newest first: [{sha, date, message}, ...]. Empty list if
    the file has no history (no repo yet, or never committed)."""
    if not (memory_dir / ".git").exists():
        return []
    rel = str(path.relative_to(memory_dir))
    fmt = "%H%x1f%ad%x1f%s"
    result = subprocess.run(
        [
            "git", "log", f"--max-count={max(1, int(limit))}", "--follow",
            f"--pretty=format:{fmt}", "--date=iso-strict", "--", rel,
        ],
        cwd=memory_dir,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise VersioningError(result.stderr.strip() or "git log failed")
    commits = []
    for line in result.stdout.splitlines():
        if not line:
            continue
        sha, date, message = line.split("\x1f", 2)
        commits.append({"sha": sha, "date": date, "message": message})
    return commits


def show_at(memory_dir: Path, path: Path, sha: str) -> str:
    """Full file content as of a given commit."""
    sha = _validate_sha(sha)
    rel = str(path.relative_to(memory_dir))
    result = subprocess.run(
        ["git", "show", f"{sha}:{rel}"], cwd=memory_dir, capture_output=True, text=True
    )
    if result.returncode != 0:
        raise VersioningError(result.stderr.strip() or "git show failed")
    return result.stdout


def diff_between(memory_dir: Path, path: Path, from_sha: str, to_sha: str) -> str:
    """Unified diff of one file between two commits."""
    from_sha = _validate_sha(from_sha)
    to_sha = _validate_sha(to_sha)
    rel = str(path.relative_to(memory_dir))
    result = subprocess.run(
        ["git", "diff", from_sha, to_sha, "--", rel],
        cwd=memory_dir,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise VersioningError(result.stderr.strip() or "git diff failed")
    return result.stdout
