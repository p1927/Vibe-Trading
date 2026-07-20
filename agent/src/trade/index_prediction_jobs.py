"""List and control index-prediction scheduled jobs."""

from __future__ import annotations

import time
from typing import Any

from src.scheduled_research.executor import is_job_stale_running
from src.scheduled_research.index_jobs import (
    INDEX_JOB_TYPES,
    JOB_TYPE_HUB_NEWS_ENTITY,
    JOB_TYPE_HUB_NEWS_INGEST,
    register_default_index_jobs,
)
from src.scheduled_research.models import JobStatus, ScheduledResearchJob
from src.scheduled_research.store import ScheduledResearchJobStore

JOB_LABELS: dict[str, str] = {
    "index_factor_snapshot": "Daily factor snapshot",
    "index_research": "Full index research (weekly)",
    "index_plan_refresh": "Live prediction refresh (poll)",
    "index_calibration": "Ledger reconcile + model retrain",
    "company_research_archive": "Archive company research",
    "index_prediction_post_close": "Post-close prediction refresh",
    "hub_news_entity": "Hub news entity pipeline",
    "hub_news_ingest": "Hub news ingest",
}

JOB_DESCRIPTIONS: dict[str, str] = {
    "index_factor_snapshot": "Collects macro + constituent factors into the factor store (default 18:00 IST).",
    "index_research": "Runs full Nifty pipeline with constituent refresh (default Mon 08:00).",
    "index_plan_refresh": "Light refresh when news/macro drifts (default every 5 min when enabled).",
    "index_calibration": "Reconciles prediction ledger and retrains macro ridge (default 06:00).",
    "company_research_archive": "Copies latest company research to history/ for backtest replay (18:30).",
    "index_prediction_post_close": "Weekly flows, backtest, counterfactual, and data audit refresh.",
    "hub_news_entity": "Drains staging refs into distilled events; maintenance mode compacts and repairs.",
    "hub_news_ingest": "Fetches RSS, SearXNG, and watcher headlines into the staging queue.",
}


def _job_display_label(job: ScheduledResearchJob) -> str:
    job_type = str(job.config.get("job_type") or "")
    base = JOB_LABELS.get(job_type, job_type or job.id)
    mode = str(job.config.get("mode") or "").strip().lower()
    if job_type == JOB_TYPE_HUB_NEWS_ENTITY and mode:
        suffix = {"drain": " (drain)", "maintenance": " (maintenance)", "full": " (full)"}.get(mode, f" ({mode})")
        return f"{base}{suffix}"
    ingest_mode = str(job.config.get("mode") or "").strip().lower()
    if job_type == JOB_TYPE_HUB_NEWS_INGEST and ingest_mode:
        return f"{base} ({ingest_mode})"
    return base


def _entity_backpressure_threshold() -> int:
    try:
        import sys
        from pathlib import Path

        trade_root = Path(__file__).resolve().parents[4]
        integrations = trade_root / "integrations"
        if integrations.is_dir() and str(integrations) not in sys.path:
            sys.path.insert(0, str(integrations))
        from trade_integrations.hub_storage.news_pipeline_config import load_news_pipeline_config

        return int(load_news_pipeline_config().entity_backpressure_threshold)
    except Exception:
        return 400


def _hub_news_pipeline_health() -> dict[str, Any]:
    try:
        import sys
        from pathlib import Path

        trade_root = Path(__file__).resolve().parents[4]
        integrations = trade_root / "integrations"
        if integrations.is_dir() and str(integrations) not in sys.path:
            sys.path.insert(0, str(integrations))
        from trade_integrations.dataflows.index_research.news_entity_worker import load_worker_last_summary
        from trade_integrations.hub_storage.news_staging_store import pipeline_pause_status, staging_queue_detail

        pause = pipeline_pause_status(ticker="NIFTY")
        staging = staging_queue_detail(ticker="NIFTY")
        queued = int(staging.get("queued") or 0)
        threshold = _entity_backpressure_threshold()
        worker_last = load_worker_last_summary() or {}
        processed = int(worker_last.get("processed") or 0)
        drain_rate_per_hour = processed * 4.0 if processed > 0 else None
        estimated_drain_hours = None
        if drain_rate_per_hour and drain_rate_per_hour > 0 and queued > 0:
            estimated_drain_hours = round(queued / drain_rate_per_hour, 1)
        return {
            "queued": queued,
            "oldest_pending_seconds": staging.get("oldest_pending_seconds"),
            "pipeline_paused": bool(pause.get("pipeline_paused")),
            "pause_reason": str(pause.get("pause_reason") or ""),
            "minimax_configured": bool(pause.get("minimax_configured")),
            "worker_last": worker_last,
            "backpressure_active": queued >= threshold,
            "backpressure_threshold": threshold,
            "drain_rate_per_hour": drain_rate_per_hour,
            "estimated_drain_hours": estimated_drain_hours,
        }
    except Exception as exc:
        return {"error": str(exc)}


def _scheduler_master_enabled() -> bool:
    from src.config.accessor import get_env_config

    return bool(get_env_config().agent_tuning.vibe_trading_enable_scheduler)


def _env_flags() -> dict[str, Any]:
    from src.config.accessor import get_env_config

    tuning = get_env_config().agent_tuning
    return {
        "vibe_trading_enable_scheduler": bool(tuning.vibe_trading_enable_scheduler),
        "index_research_enable_scheduler": bool(tuning.index_research_enable_scheduler),
        "index_monitor_enable_scheduler": bool(tuning.index_monitor_enable_scheduler),
    }


def _executor_is_running() -> bool:
    if not _scheduler_master_enabled():
        return False
    try:
        from src.api.scheduled_routes import _get_scheduled_research_executor

        return bool(_get_scheduled_research_executor().is_running)
    except Exception:
        return False


def _wake_executor() -> None:
    try:
        from src.api.scheduled_routes import _get_scheduled_research_executor

        executor = _get_scheduled_research_executor()
        wake = getattr(executor, "wake", None)
        if callable(wake):
            wake()
    except Exception:
        pass


def _serialize_job(job: ScheduledResearchJob) -> dict[str, Any]:
    job_type = str(job.config.get("job_type") or "")
    paused = job.status == JobStatus.CANCELLED
    now_ms = int(time.time() * 1000)
    stale_running = is_job_stale_running(job, now_ms)
    return {
        "id": job.id,
        "prompt": job.prompt,
        "schedule": job.schedule,
        "status": job.status.value,
        "paused": paused,
        "stale_running": stale_running,
        "enabled": not paused and job.status != JobStatus.FAILED,
        "job_type": job_type,
        "label": _job_display_label(job),
        "description": JOB_DESCRIPTIONS.get(job_type, ""),
        "ticker": job.config.get("ticker", "NIFTY"),
        "next_run_at": job.next_run_at,
        "last_run_at": job.last_run_at,
        "created_at": job.created_at,
        "last_error": job.last_error,
        "last_result_summary": job.last_result_summary,
        "consecutive_failures": job.consecutive_failures,
        "config": dict(job.config),
    }


def list_index_prediction_jobs(store: ScheduledResearchJobStore | None = None) -> dict[str, Any]:
    """Return index prediction cron jobs and master scheduler flags."""
    store = store or ScheduledResearchJobStore()
    register_default_index_jobs(store)
    jobs = [
        job
        for job in store.list_jobs()
        if str(job.config.get("job_type") or "") in INDEX_JOB_TYPES
    ]
    jobs.sort(key=lambda j: j.id)
    master_env = _scheduler_master_enabled()
    executor_running = _executor_is_running()
    return {
        "status": "ok",
        "env": _env_flags(),
        "master_scheduler_env_enabled": master_env,
        "master_scheduler_running": executor_running,
        "executor_is_running": executor_running,
        "jobs": [_serialize_job(job) for job in jobs],
        "news_pipeline": _hub_news_pipeline_health(),
    }


def pause_index_prediction_job(job_id: str, store: ScheduledResearchJobStore | None = None) -> dict[str, Any]:
    store = store or ScheduledResearchJobStore()
    job = store.get(job_id)
    if job is None or str(job.config.get("job_type") or "") not in INDEX_JOB_TYPES:
        return {"status": "error", "message": f"index job {job_id} not found"}
    job.status = JobStatus.CANCELLED
    store.upsert(job)
    return {"status": "ok", "job": _serialize_job(job)}


def resume_index_prediction_job(job_id: str, store: ScheduledResearchJobStore | None = None) -> dict[str, Any]:
    store = store or ScheduledResearchJobStore()
    job = store.get(job_id)
    if job is None or str(job.config.get("job_type") or "") not in INDEX_JOB_TYPES:
        return {"status": "error", "message": f"index job {job_id} not found"}
    now_ms = int(time.time() * 1000)
    job.status = JobStatus.PENDING
    job.next_run_at = now_ms
    job.last_error = None
    job.consecutive_failures = 0
    store.upsert(job)
    _wake_executor()
    return {"status": "ok", "job": _serialize_job(job)}


def recover_index_prediction_job(job_id: str, store: ScheduledResearchJobStore | None = None) -> dict[str, Any]:
    """Reset a stuck RUNNING job to pending and schedule an immediate retry."""
    store = store or ScheduledResearchJobStore()
    job = store.get(job_id)
    if job is None or str(job.config.get("job_type") or "") not in INDEX_JOB_TYPES:
        return {"status": "error", "message": f"index job {job_id} not found"}
    if job.status != JobStatus.RUNNING:
        return {"status": "error", "message": f"index job {job_id} is not running (status={job.status.value})"}
    now_ms = int(time.time() * 1000)
    job.status = JobStatus.PENDING
    job.next_run_at = now_ms
    job.last_error = "recovered manually"
    store.upsert(job)
    _wake_executor()
    return {"status": "ok", "job": _serialize_job(job)}
