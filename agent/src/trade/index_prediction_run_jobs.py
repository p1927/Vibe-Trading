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
_JOBS_LOCK = threading.Lock()

_JOB_TTL_SECONDS = 60 * 60
_JOB_ID_RE = re.compile(r"^[a-f0-9]{32}$")
_ACTIVE_STATUSES = frozenset({"queued", "running"})


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


def _get_job_record(job_id: str) -> dict[str, Any] | None:
    with _JOBS_LOCK:
        job = INDEX_PREDICTION_RUN_JOBS.get(job_id)
        if job is not None:
            return job
    disk = _read_job_from_disk(job_id)
    if disk is not None:
        with _JOBS_LOCK:
            INDEX_PREDICTION_RUN_JOBS[job_id] = disk
            ticker = str(disk.get("ticker") or "").upper()
            if disk.get("status") in _ACTIVE_STATUSES and ticker:
                _ACTIVE_BY_TICKER[ticker] = job_id
    return disk


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


def _job_snapshot(job: dict[str, Any], *, include_logs: bool = True) -> dict[str, Any]:
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
    if include_logs:
        out["logs"] = list(job.get("logs") or [])
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
    job = _get_job_record(job_id)
    if job is None:
        return
    if job.get("status") == "queued":
        job["status"] = "running"
    job.setdefault("logs", []).append(dict(entry))
    _write_job_to_disk(job)


def complete_job(job_id: str, *, ticker: str, artifact: dict[str, Any]) -> None:
    job = _get_job_record(job_id)
    if job is None:
        return
    job["status"] = "done"
    job["artifact"] = artifact
    job["_finished_at"] = time.time()
    key = str(job.get("ticker") or ticker).upper()
    with _JOBS_LOCK:
        if _ACTIVE_BY_TICKER.get(key) == job_id:
            _ACTIVE_BY_TICKER.pop(key, None)
    _write_job_to_disk(job)


def fail_job(job_id: str, message: str) -> None:
    job = _get_job_record(job_id)
    if job is None:
        return
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
        from trade_integrations.dataflows.index_research.pipeline_cancel import PipelineCancelledError
        from trade_integrations.dataflows.index_research.pipeline_log import PipelineLogger
        from src.trade.hub_bridge import _index_doc_to_panel, ensure_trade_stack_path

        ensure_trade_stack_path()
        plog = PipelineLogger(on_entry=on_log)
        doc = run_index_research(
            key,
            horizon_days=horizon_days,
            refresh_constituents=refresh_constituents,
            run_forecast_lab=run_forecast_lab,
            pipeline=plog,
        )
        save_index_research(doc)
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
