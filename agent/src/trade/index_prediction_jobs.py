"""List and control index-prediction scheduled jobs."""

from __future__ import annotations

import time
from typing import Any

from src.scheduled_research.index_jobs import (
    INDEX_JOB_TYPES,
    INDEX_MONITOR_ENABLE_SCHEDULER_ENV,
    INDEX_RESEARCH_ENABLE_SCHEDULER_ENV,
    is_index_monitor_scheduler_enabled,
    is_index_scheduler_enabled,
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
}

JOB_DESCRIPTIONS: dict[str, str] = {
    "index_factor_snapshot": "Collects macro + constituent factors into the factor store (default 18:00 IST).",
    "index_research": "Runs full Nifty pipeline with constituent refresh (default Mon 08:00).",
    "index_plan_refresh": "Light refresh when news/macro drifts (default every 5 min when enabled).",
    "index_calibration": "Reconciles prediction ledger and retrains macro ridge (default 06:00).",
    "company_research_archive": "Copies latest company research to history/ for backtest replay (18:30).",
}


def _scheduler_master_enabled() -> bool:
    from src.config.accessor import get_env_config

    return bool(get_env_config().agent_tuning.vibe_trading_enable_scheduler)


def _env_flags() -> dict[str, Any]:
    return {
        "vibe_trading_enable_scheduler": _scheduler_master_enabled(),
        "index_research_enable_scheduler": is_index_scheduler_enabled(),
        "index_monitor_enable_scheduler": is_index_monitor_scheduler_enabled(),
    }


def _serialize_job(job: ScheduledResearchJob) -> dict[str, Any]:
    job_type = str(job.config.get("job_type") or "")
    paused = job.status == JobStatus.CANCELLED
    return {
        "id": job.id,
        "prompt": job.prompt,
        "schedule": job.schedule,
        "status": job.status.value,
        "paused": paused,
        "enabled": not paused and job.status != JobStatus.FAILED,
        "job_type": job_type,
        "label": JOB_LABELS.get(job_type, job_type or job.id),
        "description": JOB_DESCRIPTIONS.get(job_type, ""),
        "ticker": job.config.get("ticker", "NIFTY"),
        "next_run_at": job.next_run_at,
        "last_run_at": job.last_run_at,
        "created_at": job.created_at,
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
    return {
        "status": "ok",
        "env": _env_flags(),
        "master_scheduler_running": _scheduler_master_enabled(),
        "jobs": [_serialize_job(job) for job in jobs],
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
    store.upsert(job)
    return {"status": "ok", "job": _serialize_job(job)}
