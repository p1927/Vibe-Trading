"""File-backed job store for on-demand forecast_engine evaluation runs (a named historical run
over a user-chosen date range — see `trade_integrations.forecast_engine.artifact`'s
"Named historical runs" section, distinct from the single always-overwritten "latest" evaluation).

Jobs persist under ``log/forecast_engine_run_jobs/{job_id}/job.json`` so SSE polling survives API
hot-reload and workers run in a detached subprocess. The lifecycle is the shared
:class:`src.trade.job_store.JobStore`; this module keeps the composite ``(ticker, recipe)`` scope key
(two recipes run concurrently, the same recipe can't be double-started), its serialize/snapshot shape
and the worker body.
"""

from __future__ import annotations

import logging
import uuid
from pathlib import Path
from typing import Any

from src.trade import job_runner
from src.trade.job_store import JobStore, now_iso as _now_iso

logger = logging.getLogger(__name__)

# TimesFM evaluation runs are real, multi-minute model inference (one call per cutoff) — give
# this a generous budget rather than reusing index-prediction's/external-predictions' numbers,
# which cover very different workloads.
_WALL_CLOCK_SECONDS = 30 * 60
_QUEUED_NO_PID_SECONDS = 60


def _scope_key(ticker: str, recipe: str) -> str:
    return f"{(ticker or 'NIFTY').strip().upper()}:{(recipe or '').strip()}"


def _scope_for(job: dict[str, Any]) -> str:
    extra = job.get("extra") or {}
    return _scope_key(str(extra.get("ticker") or "NIFTY"), str(extra.get("recipe") or ""))


def _jobs_root() -> Path:
    from src.trade.hub_bridge import trade_repo_root

    root = trade_repo_root()
    if root is None:
        root = Path.cwd()
    return root / "log" / "forecast_engine_run_jobs"


def _serialize_job(job: dict[str, Any]) -> dict[str, Any]:
    return {
        "job_id": job["job_id"],
        "status": job.get("status"),
        "created_at": job.get("created_at"),
        "logs": list(job.get("logs") or []),
        "error": job.get("error"),
        "worker_pid": job.get("worker_pid"),
        "_finished_at": job.get("_finished_at"),
        "extra": job.get("extra") or {},
        "result": job.get("result"),
    }


FORECAST_ENGINE_RUN_JOBS: dict[str, dict[str, Any]] = {}
_ACTIVE_BY_SCOPE: dict[str, str] = {}

# Module-level lookups (not captured values) so tests can monkeypatch ``_jobs_root`` /
# ``_WALL_CLOCK_SECONDS`` / ``_QUEUED_NO_PID_SECONDS`` after import.
_STORE = JobStore(
    jobs_ref=lambda: FORECAST_ENGINE_RUN_JOBS,
    active_ref=lambda: _ACTIVE_BY_SCOPE,
    jobs_root=lambda: _jobs_root(),
    serialize=_serialize_job,
    scope_for=_scope_for,
    wall_clock_seconds=lambda job: _WALL_CLOCK_SECONDS,
    queued_no_pid_seconds=lambda: _QUEUED_NO_PID_SECONDS,
    result_key="result",
    normalize=lambda payload: payload.setdefault("extra", {}),
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
        "created_at": job.get("created_at"),
        "error": job.get("error"),
        "extra": job.get("extra") or {},
    }
    if include_logs:
        out["logs"] = list(job.get("logs") or [])
    if job.get("status") == "done" and job.get("result") is not None:
        out["result"] = job["result"]
    return out


def get_job(job_id: str) -> dict[str, Any] | None:
    job = _get_job_record(job_id)
    if job is None:
        return None
    return _job_snapshot(job)


def get_active_job(*, ticker: str, recipe: str) -> dict[str, Any] | None:
    job = _STORE.get_active(_scope_key(ticker, recipe))
    return None if job is None else _job_snapshot(job)


def start_job(*, ticker: str, recipe: str, start: str, end: str, horizon: int) -> tuple[str, str, bool]:
    """Create or reuse an active job for ``(ticker, recipe)``. Returns ``(job_id, run_id, reused)``.

    ``run_id`` is generated here, at job-start time — not at completion — so the caller (the API
    handler) can hand it back to the frontend immediately, and the frontend can navigate straight
    to the finished run's detail view once the job reports ``done`` without a second round-trip.
    """
    from trade_integrations.forecast_engine.artifact import make_run_id

    key = (ticker or "NIFTY").strip().upper()
    recipe_name = (recipe or "").strip()
    job, reused = _STORE.claim(
        _scope_key(key, recipe_name),
        lambda: {
            "job_id": uuid.uuid4().hex,
            "status": "queued",
            "created_at": _now_iso(),
            "logs": [],
            "result": None,
            "error": None,
            "worker_pid": None,
            "_finished_at": None,
            "extra": {
                "ticker": key,
                "recipe": recipe_name,
                "start": start,
                "end": end,
                "horizon": horizon,
                "run_id": make_run_id(start=start, end=end),
            },
        },
    )
    return job["job_id"], str((job.get("extra") or {}).get("run_id") or ""), reused


def append_log(job_id: str, message: str, *, level: str = "info") -> None:
    _STORE.append(job_id, {"message": message, "level": level, "at": _now_iso()})


def complete_job(job_id: str, *, result: dict[str, Any]) -> None:
    _STORE.complete(job_id, result=result)


def run_worker(job_id: str) -> None:
    job = _get_job_record(job_id)
    if job is None:
        return
    extra = job.get("extra") or {}
    ticker = str(extra.get("ticker") or "NIFTY")
    recipe_name = str(extra.get("recipe") or "")
    start = str(extra.get("start") or "")
    end = str(extra.get("end") or "")
    horizon = int(extra.get("horizon") or 5)
    run_id = str(extra.get("run_id") or "")

    mark_running(job_id)
    try:
        from src.trade.hub_bridge import ensure_trade_stack_path

        ensure_trade_stack_path()
        from trade_integrations.forecast_engine.artifact import build_evaluation_artifact, write_run_artifact
        from trade_integrations.forecast_engine.engines.timesfm_engine import CHECKPOINT, TimesFMEngine
        from trade_integrations.forecast_engine.evaluation import evaluate_recipe
        from trade_integrations.forecast_engine.registry import RECIPES

        recipe = RECIPES[recipe_name]

        append_log(job_id, f"loading TimesFM ({CHECKPOINT})...")
        engine = TimesFMEngine()

        def on_progress(message: str) -> None:
            append_log(job_id, message)

        # step_days=1: evaluate_recipe's own default (5) only yields ~3 cutoffs over a normal
        # few-week UI-picked window, below the 5-observation minimum it enforces -- found live,
        # running a real job through this exact code path against start=2026-08-25/end=2026-09-17
        # (.claude/backlog/items/2026-09-18-timesfm-forecast-engine-module.md). Daily cutoffs are
        # what every other real run in this feature's history has actually used successfully.
        report = evaluate_recipe(
            recipe,
            engine,
            start=start,
            end=end,
            horizon=horizon,
            step_days=1,
            client=None,
            on_progress=on_progress,
        )
        artifact = build_evaluation_artifact(
            report, ticker=ticker, recipe=recipe, engine_checkpoint=CHECKPOINT, horizon=horizon
        )
        write_run_artifact(artifact, run_id, run_start=start, run_end=end)
        append_log(
            job_id,
            f"done: n_observations={report.n_observations} mean_pinball_loss={report.mean_pinball_loss:.2f} "
            f"passes_gate={report.passes_gate}",
        )
        complete_job(
            job_id,
            result={
                "run_id": run_id,
                "ticker": ticker,
                "recipe": recipe_name,
                "n_observations": report.n_observations,
                "n_skipped_gaps": report.n_skipped_gaps,
                "mean_pinball_loss": report.mean_pinball_loss,
                "passes_gate": report.passes_gate,
            },
        )
    except Exception as exc:
        logger.exception("forecast-engine run worker failed (job=%s)", job_id)
        append_log(job_id, str(exc), level="error")
        fail_job(job_id, str(exc))


def _agent_dir() -> Path:
    return job_runner.agent_dir_from_here(__file__)


def spawn_worker(job_id: str) -> None:
    _STORE.spawn(job_id, worker_module="src.trade.forecast_engine_run_worker", agent_dir=_agent_dir())


def kick_forecast_engine_run(*, ticker: str, recipe: str, start: str, end: str, horizon: int) -> tuple[str, str, str, bool]:
    """Returns ``(job_id, run_id, job_status, reused)``."""
    job_id, run_id, reused = start_job(ticker=ticker, recipe=recipe, start=start, end=end, horizon=horizon)
    _STORE.ensure_worker(job_id, reused, spawn_worker)
    snap = get_job(job_id) or {}
    return job_id, run_id, str(snap.get("status") or "queued"), reused
