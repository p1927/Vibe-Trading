"""In-memory + file-backed job store for manual index-prediction Run analysis.

Jobs persist under ``log/index_prediction_jobs/{job_id}/job.json`` so SSE polling
survives API hot-reload. Workers run in a detached subprocess (see
``index_prediction_run_worker``).
"""

from __future__ import annotations

import json
import logging
import os
import re
import signal
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

INDEX_PREDICTION_RUN_JOBS: dict[str, dict[str, Any]] = {}
_ACTIVE_BY_TICKER: dict[str, str] = {}
_JOBS_LOCK = threading.RLock()

_JOB_TTL_SECONDS = 60 * 60
_JOB_ID_RE = re.compile(r"^[a-f0-9]{32}$")
_ACTIVE_STATUSES = frozenset({"queued", "running"})
_STALE_LOG_SECONDS = int(os.getenv("INDEX_PREDICTION_STALE_LOG_SECONDS", "1800"))
_WALL_CLOCK_SECONDS = int(os.getenv("INDEX_PREDICTION_RUN_WALL_CLOCK_SECONDS", "2700"))
_QUEUED_NO_PID_SECONDS = int(os.getenv("INDEX_PREDICTION_QUEUED_NO_PID_SECONDS", "60"))


def _is_pid_alive(pid: int | None) -> bool:
    if pid is None or pid <= 0:
        return False
    try:
        os.kill(int(pid), 0)
    except OSError:
        return False
    return True


def worker_alive(job: dict[str, Any] | None) -> bool:
    """Return whether the detached worker subprocess is still running."""
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
    """Mark an active job failed when its worker exited. Returns True if reconciled."""
    job = _get_job_record(job_id)
    if job is None or job.get("status") not in _ACTIVE_STATUSES:
        return False
    if worker_alive(job):
        return False
    fail_job(job_id, "worker process exited unexpectedly")
    return True


def _parse_iso_timestamp(ts: str | None) -> float | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _job_age_seconds(job: dict[str, Any]) -> float:
    created = _parse_iso_timestamp(str(job.get("created_at") or ""))
    if created is None:
        return 0.0
    return max(0.0, datetime.now(timezone.utc).timestamp() - created)


def _last_log_age_seconds(job: dict[str, Any]) -> float | None:
    logs = list(job.get("logs") or [])
    if not logs:
        return None
    last_at = _parse_iso_timestamp(str(logs[-1].get("at") or ""))
    if last_at is None:
        return None
    return max(0.0, datetime.now(timezone.utc).timestamp() - last_at)


def reconcile_queued_job(job_id: str) -> bool:
    """Fail queued jobs whose worker never received a PID."""
    job = _get_job_record(job_id)
    if job is None or job.get("status") != "queued":
        return False
    if job.get("worker_pid") is not None:
        return False
    if _job_age_seconds(job) <= _QUEUED_NO_PID_SECONDS:
        return False
    fail_job(job_id, f"worker never spawned after {_QUEUED_NO_PID_SECONDS}s", terminate_worker=True)
    return True


def reconcile_stale_job(job_id: str) -> bool:
    """Fail running jobs that exceed wall-clock budget (log-stale only as secondary signal)."""
    job = _get_job_record(job_id)
    if job is None or job.get("status") != "running":
        return False
    if not worker_alive(job):
        return False
    wall_age = _job_age_seconds(job)
    if wall_age > _WALL_CLOCK_SECONDS:
        fail_job(
            job_id,
            f"run exceeded wall-clock budget ({int(wall_age)}s > {_WALL_CLOCK_SECONDS}s)",
            terminate_worker=True,
        )
        return True
    # Secondary: no logs at all for extended period after start (not per-stage silence).
    logs = list(job.get("logs") or [])
    if not logs and wall_age > _QUEUED_NO_PID_SECONDS:
        fail_job(job_id, f"worker produced no logs after {int(wall_age)}s", terminate_worker=True)
        return True
    return False


def reconcile_job(job_id: str) -> bool:
    """Run all reconciliation checks. Returns True if job was terminalized."""
    if reconcile_zombie_job(job_id):
        return True
    if reconcile_queued_job(job_id):
        return True
    if reconcile_stale_job(job_id):
        return True
    return False


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _jobs_root() -> Path:
    from src.trade.hub_bridge import trade_repo_root

    root = trade_repo_root()
    if root is None:
        root = Path.cwd()
    return root / "log" / "index_prediction_jobs"


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
        "refresh_constituents": bool(job.get("refresh_constituents")),
        "run_forecast_lab": bool(job.get("run_forecast_lab")),
        "created_at": job.get("created_at"),
        "logs": list(job.get("logs") or []),
        "artifact": job.get("artifact"),
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
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(_serialize_job(job), ensure_ascii=False, default=str), encoding="utf-8")
    tmp.replace(path)


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
    """Prefer fresher on-disk state when the worker subprocess updates job.json."""
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
    if disk.get("artifact") is not None and memory.get("artifact") is None:
        merged = dict(memory)
        merged.update(
            {
                "status": disk_status or mem_status,
                "artifact": disk.get("artifact"),
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
        memory = INDEX_PREDICTION_RUN_JOBS.get(job_id)
    merged = _merge_job_from_disk(job_id, memory)
    if merged is not None:
        with _JOBS_LOCK:
            INDEX_PREDICTION_RUN_JOBS[job_id] = merged
            ticker = str(merged.get("ticker") or "").upper()
            if merged.get("status") in _ACTIVE_STATUSES and ticker:
                _ACTIVE_BY_TICKER[ticker] = job_id
            elif _ACTIVE_BY_TICKER.get(ticker) == job_id:
                _ACTIVE_BY_TICKER.pop(ticker, None)
    return merged


def hydrate_jobs_from_disk() -> None:
    """Reload active jobs after API restart so SSE can resume polling."""
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
            INDEX_PREDICTION_RUN_JOBS[job_id] = job
            ticker = str(job.get("ticker") or "").upper()
            if job.get("status") in _ACTIVE_STATUSES and ticker:
                _ACTIVE_BY_TICKER[ticker] = job_id


def _prune_old_jobs() -> None:
    cutoff = time.time() - _JOB_TTL_SECONDS
    with _JOBS_LOCK:
        stale: list[str] = []
        for job_id, job in INDEX_PREDICTION_RUN_JOBS.items():
            if job.get("status") in ("done", "error") and job.get("_finished_at", 0) < cutoff:
                stale.append(job_id)
        for job_id in stale:
            job = INDEX_PREDICTION_RUN_JOBS.pop(job_id, None)
            if job:
                ticker = str(job.get("ticker") or "").upper()
                if _ACTIVE_BY_TICKER.get(ticker) == job_id:
                    _ACTIVE_BY_TICKER.pop(ticker, None)


def job_id_valid(job_id: str | None) -> bool:
    return bool(job_id and _JOB_ID_RE.fullmatch(job_id))


def _job_progress_from_logs(logs: list[dict[str, Any]] | None) -> dict[str, Any]:
    """Derive live progress fields from the latest pipeline log entry."""
    if not logs:
        return {}
    latest = logs[-1]
    detail = latest.get("detail") or {}
    progress: dict[str, Any] = {
        "current_stage": latest.get("stage"),
        "last_log_at": latest.get("at"),
        "last_log_message": latest.get("message"),
    }
    if detail.get("elapsed_ms") is not None:
        progress["stage_elapsed_ms"] = detail.get("elapsed_ms")
    if detail.get("track_id") is not None:
        progress["current_track_id"] = detail.get("track_id")
    return progress


def _job_snapshot(job: dict[str, Any], *, include_logs: bool = True) -> dict[str, Any]:
    logs = list(job.get("logs") or [])
    out: dict[str, Any] = {
        "job_id": job["job_id"],
        "status": job["status"],
        "ticker": job["ticker"],
        "horizon_days": job.get("horizon_days"),
        "refresh_constituents": bool(job.get("refresh_constituents")),
        "run_forecast_lab": bool(job.get("run_forecast_lab")),
        "created_at": job.get("created_at"),
        "error": job.get("error"),
    }
    out.update(_job_progress_from_logs(logs))
    if include_logs:
        out["logs"] = logs
    if job.get("status") == "done" and job.get("artifact") is not None:
        out["artifact"] = job["artifact"]
    return out


def get_job(job_id: str) -> dict[str, Any] | None:
    job = _get_job_record(job_id)
    if job is None:
        return None
    return _job_snapshot(job)


def get_active_job(ticker: str) -> dict[str, Any] | None:
    key = (ticker or "NIFTY").strip().upper()
    with _JOBS_LOCK:
        job_id = _ACTIVE_BY_TICKER.get(key)
    if not job_id:
        return None
    job = _get_job_record(job_id)
    if job is None or job.get("status") not in _ACTIVE_STATUSES:
        with _JOBS_LOCK:
            if _ACTIVE_BY_TICKER.get(key) == job_id:
                _ACTIVE_BY_TICKER.pop(key, None)
        return None
    if not worker_alive(job):
        reconcile_job(job_id)
        job = _get_job_record(job_id)
        if job is not None and job.get("status") == "error":
            return _job_snapshot(job)
        return None
    reconcile_job(job_id)
    job = _get_job_record(job_id)
    if job is None or job.get("status") not in _ACTIVE_STATUSES:
        if job is not None and job.get("status") == "error":
            return _job_snapshot(job)
        return None
    return _job_snapshot(job)


def start_job(
    *,
    ticker: str,
    horizon_days: int | None,
    refresh_constituents: bool,
    run_forecast_lab: bool = True,
) -> tuple[str, bool]:
    """Create or reuse an active job for *ticker*. Returns (job_id, reused)."""
    _prune_old_jobs()
    key = (ticker or "NIFTY").strip().upper()
    with _JOBS_LOCK:
        existing_id = _ACTIVE_BY_TICKER.get(key)
        if existing_id:
            existing = INDEX_PREDICTION_RUN_JOBS.get(existing_id) or _read_job_from_disk(existing_id)
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
                    if age_seconds > _QUEUED_NO_PID_SECONDS:
                        fail_job(existing_id, "worker never started")
                    else:
                        if existing_id not in INDEX_PREDICTION_RUN_JOBS:
                            INDEX_PREDICTION_RUN_JOBS[existing_id] = existing
                        return existing_id, True
                else:
                    if existing_id not in INDEX_PREDICTION_RUN_JOBS:
                        INDEX_PREDICTION_RUN_JOBS[existing_id] = existing
                    return existing_id, True

        job_id = uuid.uuid4().hex
        job = {
            "job_id": job_id,
            "status": "queued",
            "ticker": key,
            "horizon_days": horizon_days,
            "refresh_constituents": refresh_constituents,
            "run_forecast_lab": run_forecast_lab,
            "created_at": _now_iso(),
            "logs": [],
            "artifact": None,
            "error": None,
            "worker_pid": None,
            "_finished_at": None,
        }
        INDEX_PREDICTION_RUN_JOBS[job_id] = job
        _ACTIVE_BY_TICKER[key] = job_id
    _write_job_to_disk(job)
    return job_id, False


def mark_running(job_id: str) -> None:
    job = _get_job_record(job_id)
    if job is None:
        return
    if job.get("status") == "queued":
        job["status"] = "running"
        _write_job_to_disk(job)


def append_log(job_id: str, entry: dict[str, Any]) -> None:
    with _JOBS_LOCK:
        job = INDEX_PREDICTION_RUN_JOBS.get(job_id) or _read_job_from_disk(job_id)
        if job is None:
            return
        if job.get("status") == "queued":
            job["status"] = "running"
        job.setdefault("logs", []).append(dict(entry))
        INDEX_PREDICTION_RUN_JOBS[job_id] = job
    _write_job_to_disk(job)


def complete_job(job_id: str, *, ticker: str, artifact: dict[str, Any]) -> None:
    job = _get_job_record(job_id)
    if job is None:
        return
    if str(job.get("status") or "") not in _ACTIVE_STATUSES:
        logger.info(
            "complete_job skipped — job already terminal (job=%s status=%s)",
            job_id,
            job.get("status"),
        )
        return
    job["status"] = "done"
    job["artifact"] = artifact
    job["_finished_at"] = time.time()
    key = str(job.get("ticker") or ticker).upper()
    with _JOBS_LOCK:
        if _ACTIVE_BY_TICKER.get(key) == job_id:
            _ACTIVE_BY_TICKER.pop(key, None)
    _write_job_to_disk(job)


def _terminate_worker(job: dict[str, Any] | None) -> None:
    """Best-effort SIGTERM for a detached worker subprocess."""
    if job is None:
        return
    pid = job.get("worker_pid")
    if pid is None:
        return
    try:
        pid_int = int(pid)
    except (TypeError, ValueError):
        return
    if not _is_pid_alive(pid_int):
        return
    if pid_int == os.getpid():
        return
    try:
        os.kill(pid_int, signal.SIGTERM)
    except OSError:
        pass


def fail_job(job_id: str, message: str, *, terminate_worker: bool = False) -> None:
    job = _get_job_record(job_id)
    if job is None:
        return
    if str(job.get("status") or "") not in _ACTIVE_STATUSES:
        return
    if terminate_worker:
        _terminate_worker(job)
    job["status"] = "error"
    job["error"] = message
    job["_finished_at"] = time.time()
    key = str(job.get("ticker") or "").upper()
    with _JOBS_LOCK:
        if _ACTIVE_BY_TICKER.get(key) == job_id:
            _ACTIVE_BY_TICKER.pop(key, None)
    _write_job_to_disk(job)


def run_worker(job_id: str) -> None:
    """Blocking worker — run in subprocess or background thread."""
    job = _get_job_record(job_id)
    if job is None:
        return
    key = str(job["ticker"]).upper()
    horizon_days = job.get("horizon_days")
    refresh_constituents = bool(job.get("refresh_constituents"))
    run_forecast_lab = bool(job.get("run_forecast_lab"))

    mark_running(job_id)

    def on_log(entry) -> None:
        append_log(job_id, entry.to_dict())

    try:
        from trade_integrations.context.hub import save_index_research
        from trade_integrations.dataflows.index_research.aggregator import run_index_research
        from trade_integrations.dataflows.index_research.pipeline_cancel import (
            PipelineCancelledError,
            clear_pipeline_cancel,
            set_pipeline_job_id,
        )
        from trade_integrations.dataflows.index_research.pipeline_log import PipelineLogger
        from src.trade.hub_bridge import _index_doc_to_panel, ensure_trade_stack_path

        clear_pipeline_cancel(job_id=job_id)
        set_pipeline_job_id(job_id)
        ensure_trade_stack_path()
        plog = PipelineLogger(on_entry=on_log)
        doc = run_index_research(
            key,
            horizon_days=horizon_days,
            refresh_constituents=refresh_constituents,
            run_forecast_lab=run_forecast_lab,
            pipeline=plog,
        )
        with plog.stage_timer("persist", "Save hub artifact"):
            save_index_research(doc)
        try:
            from trade_integrations.dataflows.index_research.playground_context import (
                build_playground_context,
                save_playground_context,
            )

            with plog.stage_timer("persist", "Build playground context cache"):
                playground_ctx = build_playground_context(doc, ticker=key, live_fetch=False)
                save_playground_context(playground_ctx, ticker=key)
        except Exception:
            logger.debug("playground context cache write skipped (job=%s)", job_id, exc_info=True)
        artifact = _index_doc_to_panel(doc)
        artifact["asset_type"] = "index"
        complete_job(job_id, ticker=key, artifact=artifact)
    except PipelineCancelledError as exc:
        message = f"Pipeline cancelled: {exc.reason}"
        logger.info("index-prediction run cancelled (job=%s ticker=%s reason=%s)", job_id, key, exc.reason)
        append_log(
            job_id,
            {
                "stage": "error",
                "message": message,
                "level": "error",
                "at": _now_iso(),
            },
        )
        fail_job(job_id, message)
    except Exception as exc:
        logger.exception("index-prediction run worker failed (job=%s ticker=%s)", job_id, key)
        append_log(
            job_id,
            {
                "stage": "error",
                "message": str(exc),
                "level": "error",
                "at": _now_iso(),
            },
        )
        fail_job(job_id, str(exc))
    finally:
        from trade_integrations.dataflows.index_research.pipeline_cancel import (
            clear_pipeline_cancel,
            set_pipeline_job_id,
        )

        set_pipeline_job_id(None)
        clear_pipeline_cancel(job_id=job_id)


def _agent_dir() -> Path:
    here = Path(__file__).resolve()
    return here.parents[1]


def spawn_worker(job_id: str) -> None:
    """Launch pipeline in a detached subprocess (survives API hot-reload)."""
    agent_dir = _agent_dir()
    worker_log = _job_dir(job_id) / "worker.log"
    worker_log.parent.mkdir(parents=True, exist_ok=True)
    log_handle = worker_log.open("ab")

    env = os.environ.copy()
    proc = subprocess.Popen(
        [sys.executable, "-m", "src.trade.index_prediction_run_worker", job_id],
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
