"""File-backed job store for manual index-prediction Run analysis.

Jobs persist under ``log/index_prediction_jobs/{job_id}/job.json`` so SSE polling survives API
hot-reload. Workers run in a detached subprocess (see ``index_prediction_run_worker``). The lifecycle
(disk-first mutation, reconcile, prune, start/reuse) is the shared :class:`src.trade.job_store.JobStore`;
this module keeps the per-ticker scope, the refresh-vs-run wall-clock budgets, the "no logs" stale
rule, its snapshot shape and the worker body.
"""

from __future__ import annotations

import logging
import time
import uuid
from pathlib import Path
from typing import Any

from src.config.accessor import get_env_config
from src.trade import job_runner
from src.trade.job_store import ACTIVE_STATUSES as _ACTIVE_STATUSES
from src.trade.job_store import JobStore, now_iso as _now_iso
from src.trade.job_store import worker_alive  # noqa: F401  (re-exported: trade_routes / index_jobs import it from here)

logger = logging.getLogger(__name__)

_STALE_LOG_SECONDS = int(get_env_config().trade.index_prediction_stale_log_seconds)
_WALL_CLOCK_SECONDS = int(get_env_config().trade.index_prediction_run_wall_clock_seconds)
_REFRESH_WALL_CLOCK_SECONDS = int(
    get_env_config().trade.index_prediction_refresh_wall_clock_seconds
)
_QUEUED_NO_PID_SECONDS = int(get_env_config().trade.index_prediction_queued_no_pid_seconds)


def _jobs_root() -> Path:
    from src.trade.hub_bridge import trade_repo_root

    root = trade_repo_root()
    if root is None:
        root = Path.cwd()
    return root / "log" / "index_prediction_jobs"


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
        "warnings": job.get("warnings") or [],
        "error": job.get("error"),
        "worker_pid": job.get("worker_pid"),
        "_finished_at": job.get("_finished_at"),
    }


def _stale_reason(job: dict[str, Any], wall_age: float) -> str | None:
    """Secondary stale signal: no logs at all for extended period after start (not per-stage silence)."""
    if not (job.get("logs") or []) and wall_age > _QUEUED_NO_PID_SECONDS:
        return f"worker produced no logs after {int(wall_age)}s"
    return None


INDEX_PREDICTION_RUN_JOBS: dict[str, dict[str, Any]] = {}
_ACTIVE_BY_TICKER: dict[str, str] = {}

# Module-level lookups (not captured values) so tests can monkeypatch ``_jobs_root`` and the
# ``_*_SECONDS`` budgets after import.
_STORE = JobStore(
    jobs_ref=lambda: INDEX_PREDICTION_RUN_JOBS,
    active_ref=lambda: _ACTIVE_BY_TICKER,
    jobs_root=lambda: _jobs_root(),
    serialize=_serialize_job,
    scope_for=lambda job: str(job.get("ticker") or "").upper(),
    wall_clock_seconds=lambda job: _REFRESH_WALL_CLOCK_SECONDS if job.get("refresh_constituents") else _WALL_CLOCK_SECONDS,
    queued_no_pid_seconds=lambda: _QUEUED_NO_PID_SECONDS,
    running_no_pid_seconds=lambda: _QUEUED_NO_PID_SECONDS,
    result_key="artifact",
    terminal_statuses=frozenset({"done", "done_with_warnings", "error"}),
    stale_reason=_stale_reason,
)

_job_dir = _STORE.job_dir
_job_file = _STORE.job_file
_write_job_to_disk = _STORE.write
_read_job_from_disk = _STORE.read
_prune_old_jobs = _STORE.prune
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
        "warnings": job.get("warnings") or [],
    }
    out.update(_job_progress_from_logs(logs))
    if include_logs:
        out["logs"] = logs
    if job.get("status") in ("done", "done_with_warnings") and job.get("artifact") is not None:
        out["artifact"] = job["artifact"]
    return out


def get_job(job_id: str) -> dict[str, Any] | None:
    job = _get_job_record(job_id)
    if job is None:
        return None
    return _job_snapshot(job)


def get_active_job(ticker: str) -> dict[str, Any] | None:
    job = _STORE.get_active((ticker or "NIFTY").strip().upper(), report_failure=True)
    return None if job is None else _job_snapshot(job)


def start_job(
    *,
    ticker: str,
    horizon_days: int | None,
    refresh_constituents: bool,
    run_forecast_lab: bool = True,
) -> tuple[str, bool]:
    """Create or reuse an active job for *ticker*. Returns (job_id, reused)."""
    key = (ticker or "NIFTY").strip().upper()
    job, reused = _STORE.claim(
        key,
        lambda: {
            "job_id": uuid.uuid4().hex,
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
        },
    )
    return job["job_id"], reused


def mark_worker_pid(job_id: str, pid: int) -> None:
    """Record the PID actually executing this job, so ``worker_alive`` can tell
    a genuinely in-progress run (in-process or subprocess) from an abandoned one —
    used by callers that run the pipeline inline (e.g. the scheduler) rather than
    via ``spawn_worker``'s detached subprocess."""
    _STORE.mutate(job_id, lambda job: job.update(worker_pid=pid))


def append_log(job_id: str, entry: dict[str, Any]) -> None:
    _STORE.append(job_id, dict(entry))


def complete_job(
    job_id: str,
    *,
    ticker: str,
    artifact: dict[str, Any],
    warnings: list[str] | None = None,
) -> None:
    _STORE.complete(
        job_id,
        status="done_with_warnings" if warnings else "done",
        artifact=artifact,
        warnings=list(warnings or []),
    )


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
    started = time.monotonic()
    job_failed = False
    job_error = ""
    pipeline_logger = None

    def on_log(entry) -> None:
        append_log(job_id, entry.to_dict())
        try:
            from trade_integrations.observability.hooks import bridge_pipeline_entry

            bridge_pipeline_entry(entry)
        except ImportError:
            pass

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
        pipeline_logger = plog
        refresh_budget = None
        if refresh_constituents:
            # Leave a safety margin so the batch itself yields control before
            # reconcile_stale_job's wall-clock kill fires.
            # A scheduled dispatch binds its own (shorter) budget; whichever runs out first wins.
            from trade_integrations.job_deadline import bounded_by_job

            refresh_budget = bounded_by_job(max(60.0, _REFRESH_WALL_CLOCK_SECONDS - 120.0))
        doc = run_index_research(
            key,
            horizon_days=horizon_days,
            refresh_constituents=refresh_constituents,
            run_forecast_lab=run_forecast_lab,
            pipeline=plog,
            refresh_time_budget_seconds=refresh_budget,
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
            logger.warning("playground context cache write failed (job=%s); the playground serves a stale or empty cache", job_id, exc_info=True)
        artifact = _index_doc_to_panel(doc)
        artifact["asset_type"] = "index"
        complete_job(job_id, ticker=key, artifact=artifact, warnings=artifact.get("data_warnings") or [])
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
        job_failed = True
        job_error = message
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
        job_failed = True
        job_error = str(exc)
    finally:
        had_pipeline_errors = False
        if pipeline_logger is not None:
            had_pipeline_errors = any(
                str(getattr(entry, "level", "") or "").lower() == "error"
                for entry in pipeline_logger.entries
            )

        try:
            from trade_integrations.observability.hooks import emit_pipeline_job_done

            emit_pipeline_job_done(
                job_type="index_prediction",
                job_id=job_id,
                ticker=key,
                status="error" if job_failed else ("partial" if had_pipeline_errors else "ok"),
                had_errors=job_failed or had_pipeline_errors,
                had_work=True,
                duration_ms=int((time.monotonic() - started) * 1000),
                detail={"error": job_error[:400] if job_error else ""},
            )
        except ImportError:
            pass
        from trade_integrations.dataflows.index_research.pipeline_cancel import (
            clear_pipeline_cancel,
            set_pipeline_job_id,
        )

        set_pipeline_job_id(None)
        clear_pipeline_cancel(job_id=job_id)


def _agent_dir() -> Path:
    # parents[2] is the real .../agent root (see job_runner.agent_dir_from_here).
    return job_runner.agent_dir_from_here(__file__)


def spawn_worker(job_id: str) -> None:
    """Launch pipeline in a detached subprocess (survives API hot-reload)."""
    _STORE.spawn(job_id, worker_module="src.trade.index_prediction_run_worker", agent_dir=_agent_dir())

