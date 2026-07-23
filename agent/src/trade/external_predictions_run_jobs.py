"""File-backed job store for external-predictions refresh (Miscellaneous tab).

Jobs persist under ``log/external_predictions_jobs/{job_id}/job.json`` so SSE
polling survives API hot-reload. Workers run in a detached subprocess.
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

EXTERNAL_PREDICTIONS_RUN_JOBS: dict[str, dict[str, Any]] = {}
_ACTIVE_BY_SCOPE: dict[str, str] = {}
_JOBS_LOCK = threading.Lock()

_JOB_TTL_SECONDS = 60 * 60
_JOB_ID_RE = re.compile(r"^[a-f0-9]{32}$")
_ACTIVE_STATUSES = frozenset({"queued", "running"})


def _is_pid_alive(pid: int | None) -> bool:
    if pid is None or pid <= 0:
        return False
    try:
        os.kill(int(pid), 0)
    except OSError:
        return False
    return True


def worker_alive(job: dict[str, Any] | None) -> bool:
    if job is None:
        return False
    status = str(job.get("status") or "")
    pid = job.get("worker_pid")
    if status == "queued" and pid is None:
        return True
    if pid is None:
        return status not in _ACTIVE_STATUSES
    return _is_pid_alive(int(pid))


def reconcile_zombie_job(job_id: str) -> bool:
    job = _get_job_record(job_id)
    if job is None or job.get("status") not in _ACTIVE_STATUSES:
        return False
    if worker_alive(job):
        return False
    fail_job(job_id, "worker process exited unexpectedly")
    return True


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _scope_key(ticker: str, horizon_days: int) -> str:
    return f"{(ticker or 'NIFTY').strip().upper()}:{int(horizon_days)}"


def _jobs_root() -> Path:
    from src.trade.hub_bridge import trade_repo_root

    root = trade_repo_root()
    if root is None:
        root = Path.cwd()
    return root / "log" / "external_predictions_jobs"


def _job_dir(job_id: str) -> Path:
    return _jobs_root() / job_id


def _job_file(job_id: str) -> Path:
    return _job_dir(job_id) / "job.json"


def _serialize_job(job: dict[str, Any]) -> dict[str, Any]:
    return {
        "job_id": job["job_id"],
        "status": job.get("status"),
        "ticker": job.get("ticker"),
        "horizon_days": job.get("horizon_days"),
        "created_at": job.get("created_at"),
        "logs": list(job.get("logs") or []),
        "snapshot": job.get("snapshot"),
        "partial_snapshot": job.get("partial_snapshot"),
        "error": job.get("error"),
        "worker_pid": job.get("worker_pid"),
        "_finished_at": job.get("_finished_at"),
    }


def _write_job_to_disk(job: dict[str, Any]) -> None:
    job_id = str(job.get("job_id") or "")
    if not job_id_valid(job_id):
        return
    path = _job_file(job_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(_serialize_job(job), ensure_ascii=False, default=str)
    tmp = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        tmp.write_text(payload, encoding="utf-8")
        tmp.replace(path)
    finally:
        if tmp.is_file():
            try:
                tmp.unlink()
            except OSError:
                pass


def _mutate_job(job_id: str, mutator) -> dict[str, Any] | None:
    """Apply in-memory job mutation under lock, then persist atomically."""
    with _JOBS_LOCK:
        job = EXTERNAL_PREDICTIONS_RUN_JOBS.get(job_id)
        if job is None:
            job = _read_job_from_disk(job_id)
            if job is None:
                return None
            EXTERNAL_PREDICTIONS_RUN_JOBS[job_id] = job
        mutator(job)
        _write_job_to_disk(job)
        return job


def _read_job_from_disk(job_id: str) -> dict[str, Any] | None:
    path = _job_file(job_id)
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(payload, dict) or not job_id_valid(str(payload.get("job_id") or job_id)):
        return None
    payload.setdefault("job_id", job_id)
    payload.setdefault("logs", [])
    return payload


def _merge_job_from_disk(job_id: str, memory: dict[str, Any] | None) -> dict[str, Any] | None:
    disk = _read_job_from_disk(job_id)
    if disk is None:
        return memory
    if memory is None:
        return disk
    mem_logs = len(memory.get("logs") or [])
    disk_logs = len(disk.get("logs") or [])
    mem_status = str(memory.get("status") or "")
    disk_status = str(disk.get("status") or "")
    if disk_logs > mem_logs or disk_status != mem_status:
        merged = dict(disk)
        merged.setdefault("job_id", job_id)
        return merged
    if disk.get("snapshot") is not None and memory.get("snapshot") is None:
        merged = dict(memory)
        merged.update(
            {
                "status": disk_status or mem_status,
                "snapshot": disk.get("snapshot"),
                "error": disk.get("error"),
                "worker_pid": disk.get("worker_pid", memory.get("worker_pid")),
                "_finished_at": disk.get("_finished_at", memory.get("_finished_at")),
            }
        )
        if disk_logs >= mem_logs:
            merged["logs"] = list(disk.get("logs") or [])
        return merged
    return memory


def _get_job_record(job_id: str) -> dict[str, Any] | None:
    with _JOBS_LOCK:
        memory = EXTERNAL_PREDICTIONS_RUN_JOBS.get(job_id)
    merged = _merge_job_from_disk(job_id, memory)
    if merged is not None:
        with _JOBS_LOCK:
            EXTERNAL_PREDICTIONS_RUN_JOBS[job_id] = merged
            scope = _scope_key(str(merged.get("ticker") or "NIFTY"), int(merged.get("horizon_days") or 14))
            if merged.get("status") in _ACTIVE_STATUSES:
                _ACTIVE_BY_SCOPE[scope] = job_id
            elif _ACTIVE_BY_SCOPE.get(scope) == job_id:
                _ACTIVE_BY_SCOPE.pop(scope, None)
    return merged


def hydrate_jobs_from_disk() -> None:
    root = _jobs_root()
    if not root.is_dir():
        return
    for path in root.iterdir():
        if not path.is_dir():
            continue
        job_id = path.name
        if not job_id_valid(job_id):
            continue
        job = _read_job_from_disk(job_id)
        if job is None:
            continue
        with _JOBS_LOCK:
            EXTERNAL_PREDICTIONS_RUN_JOBS[job_id] = job
            scope = _scope_key(str(job.get("ticker") or "NIFTY"), int(job.get("horizon_days") or 14))
            if job.get("status") in _ACTIVE_STATUSES:
                _ACTIVE_BY_SCOPE[scope] = job_id


def _prune_old_jobs() -> None:
    cutoff = time.time() - _JOB_TTL_SECONDS
    with _JOBS_LOCK:
        stale: list[str] = []
        for job_id, job in EXTERNAL_PREDICTIONS_RUN_JOBS.items():
            if job.get("status") in ("done", "error") and job.get("_finished_at", 0) < cutoff:
                stale.append(job_id)
        for job_id in stale:
            job = EXTERNAL_PREDICTIONS_RUN_JOBS.pop(job_id, None)
            if job:
                scope = _scope_key(str(job.get("ticker") or "NIFTY"), int(job.get("horizon_days") or 14))
                if _ACTIVE_BY_SCOPE.get(scope) == job_id:
                    _ACTIVE_BY_SCOPE.pop(scope, None)


def job_id_valid(job_id: str | None) -> bool:
    return bool(job_id and _JOB_ID_RE.fullmatch(job_id))


def _job_snapshot(job: dict[str, Any], *, include_logs: bool = True) -> dict[str, Any]:
    out: dict[str, Any] = {
        "job_id": job["job_id"],
        "status": job["status"],
        "ticker": job["ticker"],
        "horizon_days": job.get("horizon_days"),
        "created_at": job.get("created_at"),
        "error": job.get("error"),
    }
    if include_logs:
        out["logs"] = list(job.get("logs") or [])
    if job.get("partial_snapshot") is not None:
        out["partial_snapshot"] = job["partial_snapshot"]
    if job.get("status") == "done" and job.get("snapshot") is not None:
        out["snapshot"] = job["snapshot"]
    return out


def get_job(job_id: str) -> dict[str, Any] | None:
    job = _get_job_record(job_id)
    if job is None:
        return None
    return _job_snapshot(job)


def get_active_job(ticker: str, *, horizon_days: int) -> dict[str, Any] | None:
    scope = _scope_key(ticker, horizon_days)
    with _JOBS_LOCK:
        job_id = _ACTIVE_BY_SCOPE.get(scope)
    if not job_id:
        return None
    job = _get_job_record(job_id)
    if job is None or job.get("status") not in _ACTIVE_STATUSES:
        with _JOBS_LOCK:
            if _ACTIVE_BY_SCOPE.get(scope) == job_id:
                _ACTIVE_BY_SCOPE.pop(scope, None)
        return None
    if not worker_alive(job):
        reconcile_zombie_job(job_id)
        return None
    return _job_snapshot(job)


def start_job(*, ticker: str, horizon_days: int) -> tuple[str, bool]:
    """Create or reuse an active job. Returns (job_id, reused)."""
    _prune_old_jobs()
    key = (ticker or "NIFTY").strip().upper()
    hz = int(horizon_days)
    scope = _scope_key(key, hz)
    with _JOBS_LOCK:
        existing_id = _ACTIVE_BY_SCOPE.get(scope)
        if existing_id:
            existing = EXTERNAL_PREDICTIONS_RUN_JOBS.get(existing_id) or _read_job_from_disk(existing_id)
            if existing and existing.get("status") in _ACTIVE_STATUSES:
                if existing.get("worker_pid") is not None and not worker_alive(existing):
                    fail_job(existing_id, "worker process exited unexpectedly")
                elif existing.get("status") == "running" and existing.get("worker_pid") is None:
                    created_at = str(existing.get("created_at") or "")
                    age_seconds = 999.0
                    if created_at:
                        try:
                            age_seconds = (
                                datetime.now(timezone.utc)
                                - datetime.fromisoformat(created_at.replace("Z", "+00:00"))
                            ).total_seconds()
                        except ValueError:
                            age_seconds = 999.0
                    if age_seconds > 30:
                        fail_job(existing_id, "worker never started")
                    else:
                        if existing_id not in EXTERNAL_PREDICTIONS_RUN_JOBS:
                            EXTERNAL_PREDICTIONS_RUN_JOBS[existing_id] = existing
                        return existing_id, True
                else:
                    if existing_id not in EXTERNAL_PREDICTIONS_RUN_JOBS:
                        EXTERNAL_PREDICTIONS_RUN_JOBS[existing_id] = existing
                    return existing_id, True

        job_id = uuid.uuid4().hex
        job = {
            "job_id": job_id,
            "status": "queued",
            "ticker": key,
            "horizon_days": hz,
            "created_at": _now_iso(),
            "logs": [],
            "snapshot": None,
            "error": None,
            "worker_pid": None,
            "_finished_at": None,
        }
        EXTERNAL_PREDICTIONS_RUN_JOBS[job_id] = job
        _ACTIVE_BY_SCOPE[scope] = job_id
    _write_job_to_disk(job)
    return job_id, False


def mark_running(job_id: str) -> None:
    def _apply(job: dict[str, Any]) -> None:
        if job.get("status") == "queued":
            job["status"] = "running"

    _mutate_job(job_id, _apply)


def append_log(job_id: str, entry: dict[str, Any]) -> None:
    def _apply(job: dict[str, Any]) -> None:
        if job.get("status") == "queued":
            job["status"] = "running"
        job.setdefault("logs", []).append(dict(entry))

    _mutate_job(job_id, _apply)


def append_source_complete(
    job_id: str,
    *,
    source_id: str,
    record: dict[str, Any],
    partial_snapshot: dict[str, Any],
) -> None:
    """Record per-source completion for incremental SSE + partial UI updates."""
    def _apply(job: dict[str, Any]) -> None:
        if job.get("status") == "queued":
            job["status"] = "running"
        entry = {
            "stage": "source_complete",
            "level": "info",
            "message": f"{source_id} complete",
            "source_id": source_id,
            "record": record,
            "partial_snapshot": partial_snapshot,
            "at": _now_iso(),
        }
        job.setdefault("logs", []).append(entry)
        job["partial_snapshot"] = partial_snapshot

    _mutate_job(job_id, _apply)


def complete_job(job_id: str, *, ticker: str, snapshot: dict[str, Any]) -> None:
    def _apply(job: dict[str, Any]) -> None:
        job["status"] = "done"
        job["snapshot"] = snapshot
        job["_finished_at"] = time.time()
        scope = _scope_key(str(job.get("ticker") or ticker), int(job.get("horizon_days") or 14))
        if _ACTIVE_BY_SCOPE.get(scope) == job_id:
            _ACTIVE_BY_SCOPE.pop(scope, None)

    _mutate_job(job_id, _apply)


def fail_job(job_id: str, message: str) -> None:
    def _apply(job: dict[str, Any]) -> None:
        job["status"] = "error"
        job["error"] = message
        job["_finished_at"] = time.time()
        scope = _scope_key(str(job.get("ticker") or "NIFTY"), int(job.get("horizon_days") or 14))
        if _ACTIVE_BY_SCOPE.get(scope) == job_id:
            _ACTIVE_BY_SCOPE.pop(scope, None)

    _mutate_job(job_id, _apply)


def run_worker(job_id: str) -> None:
    job = _get_job_record(job_id)
    if job is None:
        return
    key = str(job["ticker"]).upper()
    horizon_days = int(job.get("horizon_days") or 14)
    mark_running(job_id)

    def on_log(entry) -> None:
        append_log(job_id, entry.to_dict())

    def on_source_complete(source_id: str, record, partial_snapshot) -> None:
        append_source_complete(
            job_id,
            source_id=source_id,
            record=record.to_dict(),
            partial_snapshot=partial_snapshot.to_dict(),
        )

    try:
        from trade_integrations.dataflows.index_research.external_predictions.refresh import (
            refresh_all_external_predictions,
        )
        from trade_integrations.dataflows.index_research.pipeline_log import PipelineLogger
        from src.trade.hub_bridge import ensure_trade_stack_path

        ensure_trade_stack_path()
        plog = PipelineLogger(on_entry=on_log)
        snap = refresh_all_external_predictions(
            symbol=key,
            horizon_days=horizon_days,
            pipeline=plog,
            on_source_complete=on_source_complete,
        )
        complete_job(job_id, ticker=key, snapshot=snap.to_dict())
    except Exception as exc:
        logger.exception("external-predictions refresh worker failed (job=%s)", job_id)
        append_log(
            job_id,
            {"stage": "error", "message": str(exc), "level": "error", "at": _now_iso()},
        )
        fail_job(job_id, str(exc))


def _agent_dir() -> Path:
    here = Path(__file__).resolve()
    return here.parents[1]


def spawn_worker(job_id: str) -> None:
    agent_dir = _agent_dir()
    worker_log = _job_dir(job_id) / "worker.log"
    worker_log.parent.mkdir(parents=True, exist_ok=True)
    log_handle = worker_log.open("ab")
    env = os.environ.copy()
    proc = subprocess.Popen(
        [sys.executable, "-m", "src.trade.external_predictions_run_worker", job_id],
        cwd=str(agent_dir),
        env=env,
        start_new_session=True,
        stdout=log_handle,
        stderr=subprocess.STDOUT,
    )
    log_handle.close()
    job = _get_job_record(job_id)
    if job is not None:
        job["worker_pid"] = proc.pid
        _write_job_to_disk(job)


def kick_external_predictions_refresh(*, ticker: str, horizon_days: int) -> tuple[str, str, bool]:
    job_id, reused = start_job(ticker=ticker, horizon_days=horizon_days)
    if not reused:
        spawn_worker(job_id)
    else:
        existing = _get_job_record(job_id)
        if existing is not None and not worker_alive(existing):
            spawn_worker(job_id)
    snap = get_job(job_id) or {}
    return job_id, str(snap.get("status") or "queued"), reused
