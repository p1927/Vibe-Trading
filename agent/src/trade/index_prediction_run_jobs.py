"""In-memory job store for manual index-prediction Run analysis.

Process-local state (same tradeoff as alpha bench jobs): server restart clears
jobs; users re-trigger. One active (queued/running) job per ticker.
"""

from __future__ import annotations

import logging
import re
import threading
import time
import uuid
from datetime import datetime, timezone
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
        "created_at": job.get("created_at"),
        "error": job.get("error"),
    }
    if include_logs:
        out["logs"] = list(job.get("logs") or [])
    if job.get("status") == "done" and job.get("artifact") is not None:
        out["artifact"] = job["artifact"]
    return out


def get_job(job_id: str) -> dict[str, Any] | None:
    with _JOBS_LOCK:
        job = INDEX_PREDICTION_RUN_JOBS.get(job_id)
        if job is None:
            return None
        return _job_snapshot(job)


def get_active_job(ticker: str) -> dict[str, Any] | None:
    key = (ticker or "NIFTY").strip().upper()
    with _JOBS_LOCK:
        job_id = _ACTIVE_BY_TICKER.get(key)
        if not job_id:
            return None
        job = INDEX_PREDICTION_RUN_JOBS.get(job_id)
        if job is None or job.get("status") not in _ACTIVE_STATUSES:
            _ACTIVE_BY_TICKER.pop(key, None)
            return None
        return _job_snapshot(job)


def start_job(
    *,
    ticker: str,
    horizon_days: int | None,
    refresh_constituents: bool,
) -> tuple[str, bool]:
    """Create or reuse an active job for *ticker*. Returns (job_id, reused)."""
    _prune_old_jobs()
    key = (ticker or "NIFTY").strip().upper()
    with _JOBS_LOCK:
        existing_id = _ACTIVE_BY_TICKER.get(key)
        if existing_id:
            existing = INDEX_PREDICTION_RUN_JOBS.get(existing_id)
            if existing and existing.get("status") in _ACTIVE_STATUSES:
                return existing_id, True

        job_id = uuid.uuid4().hex
        INDEX_PREDICTION_RUN_JOBS[job_id] = {
            "job_id": job_id,
            "status": "queued",
            "ticker": key,
            "horizon_days": horizon_days,
            "refresh_constituents": refresh_constituents,
            "created_at": _now_iso(),
            "logs": [],
            "artifact": None,
            "error": None,
            "_finished_at": None,
        }
        _ACTIVE_BY_TICKER[key] = job_id
        return job_id, False


def mark_running(job_id: str) -> None:
    with _JOBS_LOCK:
        job = INDEX_PREDICTION_RUN_JOBS.get(job_id)
        if job is not None and job["status"] == "queued":
            job["status"] = "running"


def append_log(job_id: str, entry: dict[str, Any]) -> None:
    with _JOBS_LOCK:
        job = INDEX_PREDICTION_RUN_JOBS.get(job_id)
        if job is None:
            return
        if job["status"] == "queued":
            job["status"] = "running"
        job.setdefault("logs", []).append(dict(entry))


def complete_job(job_id: str, *, ticker: str, artifact: dict[str, Any]) -> None:
    with _JOBS_LOCK:
        job = INDEX_PREDICTION_RUN_JOBS.get(job_id)
        if job is None:
            return
        job["status"] = "done"
        job["artifact"] = artifact
        job["_finished_at"] = time.time()
        key = str(job.get("ticker") or ticker).upper()
        if _ACTIVE_BY_TICKER.get(key) == job_id:
            _ACTIVE_BY_TICKER.pop(key, None)


def fail_job(job_id: str, message: str) -> None:
    with _JOBS_LOCK:
        job = INDEX_PREDICTION_RUN_JOBS.get(job_id)
        if job is None:
            return
        job["status"] = "error"
        job["error"] = message
        job["_finished_at"] = time.time()
        key = str(job.get("ticker") or "").upper()
        if _ACTIVE_BY_TICKER.get(key) == job_id:
            _ACTIVE_BY_TICKER.pop(key, None)


def run_worker(job_id: str) -> None:
    """Blocking worker — run in a background thread."""
    with _JOBS_LOCK:
        job = INDEX_PREDICTION_RUN_JOBS.get(job_id)
        if job is None:
            return
        key = str(job["ticker"]).upper()
        horizon_days = job.get("horizon_days")
        refresh_constituents = bool(job.get("refresh_constituents"))

    mark_running(job_id)

    def on_log(entry) -> None:
        append_log(job_id, entry.to_dict())

    try:
        from trade_integrations.context.hub import save_index_research
        from trade_integrations.dataflows.index_research.aggregator import run_index_research
        from trade_integrations.dataflows.index_research.pipeline_log import PipelineLogger
        from src.trade.hub_bridge import _index_doc_to_panel, ensure_trade_stack_path

        ensure_trade_stack_path()
        plog = PipelineLogger(on_entry=on_log)
        doc = run_index_research(
            key,
            horizon_days=horizon_days,
            refresh_constituents=refresh_constituents,
            pipeline=plog,
        )
        save_index_research(doc)
        artifact = _index_doc_to_panel(doc)
        artifact["asset_type"] = "index"
        complete_job(job_id, ticker=key, artifact=artifact)
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


def spawn_worker(job_id: str) -> None:
    threading.Thread(target=run_worker, args=(job_id,), daemon=True).start()
