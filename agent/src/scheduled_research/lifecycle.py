"""Stack lifecycle helpers for scheduled-research job state."""

from __future__ import annotations

import logging
import time
from typing import Literal

from src.scheduled_research.executor import (
    is_job_stale_running,
    next_due,
    stale_running_ms_for,
)
from src.scheduled_research.models import JobStatus, ScheduledResearchJob
from src.scheduled_research.store import ScheduledResearchJobStore

logger = logging.getLogger(__name__)

RecoverMode = Literal["stale", "all_running"]


def _advance_recovered_job(job: ScheduledResearchJob, now_ms: int, *, tick_ms: int = 60_000) -> None:
    job.status = JobStatus.PENDING
    try:
        job.next_run_at = next_due(job.schedule, now_ms)
    except Exception:
        logger.warning(
            "could not advance schedule for recovered job %s; deferring one tick",
            job.id,
            exc_info=True,
        )
        job.next_run_at = now_ms + tick_ms


def recover_persisted_scheduler_jobs(
    store: ScheduledResearchJobStore | None = None,
    *,
    mode: RecoverMode = "stale",
    reason: str = "",
) -> int:
    """Reset persisted RUNNING jobs so stack restarts do not inherit hung state.

    Args:
        store: Job store (default singleton path).
        mode: ``stale`` — only jobs past per-type stale threshold;
              ``all_running`` — every RUNNING job (shutdown cleanup).
        reason: Optional note stored in ``last_error`` when empty.
    """
    store = store or ScheduledResearchJobStore()
    now_ms = int(time.time() * 1000)
    jobs = store.load()
    recovered = 0
    for job in jobs.values():
        if job.status != JobStatus.RUNNING:
            continue
        if mode == "stale" and not is_job_stale_running(job, now_ms):
            continue
        _advance_recovered_job(job, now_ms)
        if reason and not job.last_error:
            job.last_error = reason
        recovered += 1
        logger.warning(
            "recovered scheduled research job %s from running to pending (%s, next_run_at=%s)",
            job.id,
            mode,
            job.next_run_at,
        )
    if recovered:
        store.save(jobs)
    return recovered


def recover_scheduler_jobs_on_stack_boot(store: ScheduledResearchJobStore | None = None) -> int:
    """Recover stale RUNNING jobs before any trade stack start/heal command."""
    count = recover_persisted_scheduler_jobs(
        store,
        mode="stale",
        reason="recovered on stack boot (stale running)",
    )
    if count:
        logger.info("stack boot recovered %d stale scheduled research job(s)", count)
    return count


def recover_scheduler_jobs_on_stack_shutdown(store: ScheduledResearchJobStore | None = None) -> int:
    """Reset all RUNNING jobs when the Vibe API or stack tier stops."""
    count = recover_persisted_scheduler_jobs(
        store,
        mode="all_running",
        reason="recovered on stack shutdown",
    )
    if count:
        logger.info("stack shutdown recovered %d scheduled research job(s) from running", count)
    return count


def stale_running_ms_for_job(job: ScheduledResearchJob) -> int:
    """Public alias for API serialization (matches executor recovery thresholds)."""
    return stale_running_ms_for(job)
