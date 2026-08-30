"""HTTP routes for viewing, curating, and version-browsing the agent's persistent memory.

See .claude/backlog/items/2026-08-29-memory-management-http-api.md. Depends on the
agent_id/git-versioning foundation in src/memory/persistent.py and src/memory/versioning.py.
Every mutating route goes through PersistentMemory's own write path (never writes memory
files directly), so the FTS search index and git history stay in sync with the API.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from src.api.security import require_local_or_auth

logger = logging.getLogger(__name__)

memory_router = APIRouter(prefix="/memory", tags=["memory"])


class MemoryEntrySummary(BaseModel):
    id: str
    title: str
    description: str
    memory_type: str
    agent_id: Optional[str] = None
    created_at: float
    updated_at: float
    last_accessed: float
    quality_score: float
    access_count: int
    importance: float
    compression_level: str


class MemoryEntryDetail(MemoryEntrySummary):
    body: str


class MemoryEntryListResponse(BaseModel):
    entries: List[MemoryEntrySummary]


class MemoryEntryUpdateRequest(BaseModel):
    body: Optional[str] = None
    description: Optional[str] = None


class MemoryCommitInfo(BaseModel):
    sha: str
    date: str
    message: str


class MemoryHistoryResponse(BaseModel):
    entry_id: str
    commits: List[MemoryCommitInfo]


class MemoryDiffResponse(BaseModel):
    entry_id: str
    from_sha: str
    to_sha: str
    diff: str


def _to_summary(entry: Any) -> MemoryEntrySummary:
    return MemoryEntrySummary(
        id=entry.id,
        title=entry.title,
        description=entry.description,
        memory_type=entry.memory_type,
        agent_id=entry.agent_id,
        created_at=entry.created_at,
        updated_at=entry.updated_at,
        last_accessed=entry.last_accessed,
        quality_score=entry.quality_score,
        access_count=entry.access_count,
        importance=entry.importance,
        compression_level=entry.compression_level,
    )


def _to_detail(entry: Any) -> MemoryEntryDetail:
    return MemoryEntryDetail(**_to_summary(entry).model_dump(), body=entry.body)


def _get_memory():
    from src.memory.persistent import PersistentMemory

    return PersistentMemory()


def _find_or_404(entry_id: str):
    entry = _get_memory().find_by_id(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"memory entry not found: {entry_id}")
    return entry


_SORT_KEYS = {
    "updated": (lambda e: e.updated_at, True),
    "importance": (lambda e: e.importance, False),
    "last_accessed": (lambda e: e.last_accessed, False),
}


@memory_router.get("/entries", response_model=MemoryEntryListResponse)
def list_memory_entries(
    agent_id: Optional[str] = Query(None, description="Filter to one agent's entries"),
    memory_type: Optional[str] = Query(None, description="Filter by memory_type"),
    unscoped_only: bool = Query(False, description="Only entries with no agent_id (legacy)"),
    sort: str = Query(
        "updated",
        description="'updated' (newest first, default), 'importance' or 'last_accessed' "
        "(lowest/oldest first — surfaces stale entries for human review)",
    ),
) -> MemoryEntryListResponse:
    """List memory entries, optionally scoped to one agent."""
    entries = _get_memory().list_entries()
    if agent_id:
        entries = [e for e in entries if e.agent_id == agent_id]
    if unscoped_only:
        entries = [e for e in entries if e.agent_id is None]
    if memory_type:
        entries = [e for e in entries if e.memory_type == memory_type]
    key, reverse = _SORT_KEYS.get(sort, _SORT_KEYS["updated"])
    entries = sorted(entries, key=key, reverse=reverse)
    return MemoryEntryListResponse(entries=[_to_summary(e) for e in entries])


@memory_router.get("/entries/{entry_id}", response_model=MemoryEntryDetail)
def get_memory_entry(entry_id: str) -> MemoryEntryDetail:
    """Full content (frontmatter fields + body) of one memory entry."""
    return _to_detail(_find_or_404(entry_id))


@memory_router.patch(
    "/entries/{entry_id}", response_model=MemoryEntryDetail, dependencies=[Depends(require_local_or_auth)]
)
def update_memory_entry(entry_id: str, payload: MemoryEntryUpdateRequest) -> MemoryEntryDetail:
    """Human-edit an entry's body and/or description after review. Commits to git history and
    re-indexes the FTS entry so search reflects the edit."""
    if payload.body is None and payload.description is None:
        raise HTTPException(status_code=400, detail="body or description required")
    memory = _get_memory()
    entry = _find_or_404(entry_id)
    ok = memory.update_entry(entry, body=payload.body, description=payload.description)
    if not ok:
        raise HTTPException(status_code=500, detail=f"failed to update memory entry: {entry_id}")
    return _to_detail(_find_or_404(entry_id))


@memory_router.delete("/entries/{entry_id}", dependencies=[Depends(require_local_or_auth)])
def archive_memory_entry(entry_id: str) -> Dict[str, Any]:
    """Clear an entry from active memory (soft delete: moved to archive/, recoverable from git
    history) after a human has reviewed it and judged it stale or wrong."""
    memory = _get_memory()
    entry = _find_or_404(entry_id)
    ok = memory.archive_entry(entry)
    if not ok:
        raise HTTPException(status_code=500, detail=f"failed to archive memory entry: {entry_id}")
    return {"status": "ok", "id": entry_id, "action": "archived"}


@memory_router.get("/entries/{entry_id}/history", response_model=MemoryHistoryResponse)
def get_memory_entry_history(
    entry_id: str, limit: int = Query(50, ge=1, le=200)
) -> MemoryHistoryResponse:
    """Git commit history for one entry, newest first."""
    from src.memory.persistent import MEMORY_BASE
    from src.memory.versioning import VersioningError, log_for_path

    entry = _find_or_404(entry_id)
    try:
        commits = log_for_path(MEMORY_BASE, entry.path, limit=limit)
    except VersioningError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return MemoryHistoryResponse(
        entry_id=entry_id, commits=[MemoryCommitInfo(**c) for c in commits]
    )


@memory_router.post("/cache/invalidate", dependencies=[Depends(require_local_or_auth)])
def invalidate_memory_cache() -> Dict[str, Any]:
    """Force a full FTS search-index rebuild from the current on-disk state.

    The write path (update/archive above) already keeps the index in sync
    incrementally; this is an explicit escape hatch for after editing memory
    files directly on disk outside the API, or just to confirm the index
    reflects reality after reviewing a batch of entries."""
    count = _get_memory().rebuild_search_index()
    return {"status": "ok", "reindexed": count}


@memory_router.get("/entries/{entry_id}/diff", response_model=MemoryDiffResponse)
def get_memory_entry_diff(
    entry_id: str,
    from_sha: str = Query(..., alias="from"),
    to_sha: str = Query(..., alias="to"),
) -> MemoryDiffResponse:
    """Unified diff of one entry's file between two commits from its history."""
    from src.memory.persistent import MEMORY_BASE
    from src.memory.versioning import VersioningError, diff_between

    entry = _find_or_404(entry_id)
    try:
        diff_text = diff_between(MEMORY_BASE, entry.path, from_sha, to_sha)
    except VersioningError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return MemoryDiffResponse(entry_id=entry_id, from_sha=from_sha, to_sha=to_sha, diff=diff_text)
