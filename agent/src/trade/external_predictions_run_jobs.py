"""File-backed job store for external-predictions refresh (Miscellaneous tab).

Jobs persist under ``log/external_predictions_jobs/{job_id}/job.json`` so SSE polling survives API
hot-reload. Workers run in a detached subprocess. The lifecycle (disk-first mutation, reconcile, prune,
start/reuse) is the shared :class:`src.trade.job_store.JobStore`; this module keeps the scope key, the
serialize/snapshot shape, log sanitising and the worker body.
"""

from __future__ import annotations

import logging
import time
import uuid
from pathlib import Path
from typing import Any

from src.config.accessor import get_env_config
from src.trade import job_runner
from src.trade.job_store import JobStore, now_iso as _now_iso

logger = logging.getLogger(__name__)

_WALL_CLOCK_SECONDS = int(get_env_config().trade.external_predictions_run_wall_clock_seconds)
_QUEUED_NO_PID_SECONDS = int(get_env_config().trade.external_predictions_queued_no_pid_seconds)


def _scope_key(ticker: str, horizon_days: int) -> str:
    return f"{(ticker or 'NIFTY').strip().upper()}:{int(horizon_days)}"


def _jobs_root() -> Path:
    from src.trade.hub_bridge import trade_repo_root

    root = trade_repo_root()
    if root is None:
        root = Path.cwd()
    return root / "log" / "external_predictions_jobs"


def _serialize_job(job: dict[str, Any]) -> dict[str, Any]:
    return {
        "job_id": job["job_id"],
        "status": job.get("status"),
        "ticker": job.get("ticker"),
        "horizon_days": job.get("horizon_days"),
        "created_at": job.get("created_at"),
        "logs": [_sanitize_log_entry(row) for row in list(job.get("logs") or [])],
        "snapshot": _sanitize_json_value(job.get("snapshot")),
        "partial_snapshot": _sanitize_json_value(job.get("partial_snapshot")),
        "error": _sanitize_json_text(job.get("error")),
        "worker_pid": job.get("worker_pid"),
        "_finished_at": job.get("_finished_at"),
    }


def _sanitize_json_text(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    return "".join(ch if ord(ch) >= 32 or ch in "\n\r\t" else " " for ch in value)


def _sanitize_json_value(value: Any) -> Any:
    if isinstance(value, str):
        return _sanitize_json_text(value)
    if isinstance(value, dict):
        return {k: _sanitize_json_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_sanitize_json_value(v) for v in value]
    return value


def _sanitize_log_entry(entry: dict[str, Any]) -> dict[str, Any]:
    sanitized = _sanitize_json_value(entry)
    return sanitized if isinstance(sanitized, dict) else dict(entry)



EXTERNAL_PREDICTIONS_RUN_JOBS: dict[str, dict[str, Any]] = {}
_ACTIVE_BY_SCOPE: dict[str, str] = {}

# Module-level lookups (not captured values) so tests can monkeypatch ``_jobs_root`` /
# ``_WALL_CLOCK_SECONDS`` / ``_QUEUED_NO_PID_SECONDS`` after import.
_STORE = JobStore(
    jobs_ref=lambda: EXTERNAL_PREDICTIONS_RUN_JOBS,
    active_ref=lambda: _ACTIVE_BY_SCOPE,
    jobs_root=lambda: _jobs_root(),
    serialize=_serialize_job,
    scope_for=lambda job: _scope_key(str(job.get("ticker") or "NIFTY"), int(job.get("horizon_days") or 14)),
    wall_clock_seconds=lambda job: _WALL_CLOCK_SECONDS,
    queued_no_pid_seconds=lambda: _QUEUED_NO_PID_SECONDS,
    result_key="snapshot",
)

_job_dir = _STORE.job_dir
_job_file = _STORE.job_file
_write_job_to_disk = _STORE.write
_mutate_job = _STORE.mutate
_get_job_record = _STORE.get_record
hydrate_jobs_from_disk = _STORE.hydrate
reconcile_zombie_job = _STORE.reconcile_zombie
reconcile_queued_job = _STORE.reconcile_queued
reconcile_stale_job = _STORE.reconcile_stale
reconcile_job = _STORE.reconcile
reconcile_all_active_jobs = _STORE.reconcile_all_active
mark_running = _STORE.mark_running
fail_job = _STORE.fail
job_id_valid = job_runner.job_id_valid


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
    job = _STORE.get_active(_scope_key(ticker, horizon_days))
    return None if job is None else _job_snapshot(job)


def start_job(*, ticker: str, horizon_days: int) -> tuple[str, bool]:
    """Create or reuse an active job. Returns (job_id, reused)."""
    key = (ticker or "NIFTY").strip().upper()
    hz = int(horizon_days)
    job, reused = _STORE.claim(
        _scope_key(key, hz),
        lambda: {
            "job_id": uuid.uuid4().hex,
            "status": "queued",
            "ticker": key,
            "horizon_days": hz,
            "created_at": _now_iso(),
            "logs": [],
            "snapshot": None,
            "error": None,
            "worker_pid": None,
            "_finished_at": None,
        },
    )
    return job["job_id"], reused


def append_log(job_id: str, entry: dict[str, Any]) -> None:
    _STORE.append(job_id, _sanitize_log_entry(dict(entry)))


def append_source_complete(
    job_id: str,
    *,
    source_id: str,
    record: dict[str, Any],
    partial_snapshot: dict[str, Any],
) -> None:
    """Record per-source completion for incremental SSE + partial UI updates."""
    entry = _sanitize_log_entry(
        {
            "stage": "source_complete",
            "level": "info",
            "message": f"{source_id} complete",
            "source_id": source_id,
            "record": record,
            "partial_snapshot": partial_snapshot,
            "at": _now_iso(),
        }
    )
    _STORE.append(job_id, entry, partial_snapshot=_sanitize_json_value(partial_snapshot))


def complete_job(job_id: str, *, ticker: str, snapshot: dict[str, Any]) -> None:
    _STORE.complete(job_id, snapshot=snapshot)


def run_worker(job_id: str) -> None:
    job = _get_job_record(job_id)
    if job is None:
        return
    key = str(job["ticker"]).upper()
    horizon_days = int(job.get("horizon_days") or 14)
    mark_running(job_id)
    started = time.monotonic()
    job_failed = False
    job_error = ""
    had_errors = False

    def on_log(entry) -> None:
        append_log(job_id, entry.to_dict())
        try:
            from trade_integrations.observability.hooks import bridge_pipeline_entry

            bridge_pipeline_entry(entry)
        except ImportError:
            pass

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
        had_errors = bool(getattr(snap, "had_errors", False))
        complete_job(job_id, ticker=key, snapshot=snap.to_dict())
    except Exception as exc:
        logger.exception("external-predictions refresh worker failed (job=%s)", job_id)
        append_log(
            job_id,
            {"stage": "error", "message": str(exc), "level": "error", "at": _now_iso()},
        )
        fail_job(job_id, str(exc))
        job_failed = True
        job_error = str(exc)
        had_errors = True
    finally:
        try:
            from trade_integrations.observability.hooks import emit_pipeline_job_done

            emit_pipeline_job_done(
                job_type="external_predictions",
                job_id=job_id,
                ticker=key,
                status="error" if job_failed else "ok",
                had_errors=had_errors or job_failed,
                had_work=True,
                duration_ms=int((time.monotonic() - started) * 1000),
                detail={"error": job_error[:400] if job_error else ""},
            )
        except ImportError:
            pass


def _agent_dir() -> Path:
    # parents[2] is the real .../agent root (see job_runner.agent_dir_from_here).
    return job_runner.agent_dir_from_here(__file__)


def spawn_worker(job_id: str) -> None:
    _STORE.spawn(job_id, worker_module="src.trade.external_predictions_run_worker", agent_dir=_agent_dir())


def kick_external_predictions_refresh(*, ticker: str, horizon_days: int) -> tuple[str, str, bool]:
    job_id, reused = start_job(ticker=ticker, horizon_days=horizon_days)
    _STORE.ensure_worker(job_id, reused, spawn_worker)
    snap = get_job(job_id) or {}
    return job_id, str(snap.get("status") or "queued"), reused
