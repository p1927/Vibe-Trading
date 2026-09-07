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
from src.scheduled_research.job_tier_policy import is_safe_to_auto_resume
from src.scheduled_research.models import JobStatus, ScheduledResearchJob
from src.scheduled_research.store import ScheduledResearchJobStore

logger = logging.getLogger(__name__)

RecoverMode = Literal["stale", "all_running"]

#: Exact ``reason`` strings passed to :func:`recover_persisted_scheduler_jobs` by the two
#: shutdown-only call sites (``recover_scheduler_jobs_on_stack_shutdown`` below, and
#: ``api/scheduled_startup.py``'s executor-shutdown pass). A job's ``auto_paused_reason`` is
#: stamped as ``f"auto-paused: {reason}"`` — see ``_advance_recovered_job``.
#:
#: Deliberately does NOT include ``"recovered on stack boot (stale running)"``
#: (``recover_scheduler_jobs_on_stack_boot``'s own reason): that pause is triggered by finding a
#: job stuck RUNNING at boot, i.e. a crash with genuinely unknown/possibly-half-applied side
#: effects, not a routine promotion window closing cleanly. Only a pause stamped with one of
#: these two reasons is known to be a *transient* safety measure for the promotion/restart window
#: itself — see `.claude/backlog/items/2026-09-07-four-jobs-silently-paused-by-a-shutdown.md`.
SHUTDOWN_AUTO_PAUSE_REASONS = frozenset(
    {
        "recovered on stack shutdown",
        "process restart (executor shutdown)",
    }
)


def _advance_recovered_job(
    job: ScheduledResearchJob,
    now_ms: int,
    *,
    tick_ms: int = 60_000,
    auto_pause_reason: str | None = None,
) -> None:
    job.status = JobStatus.PENDING
    if auto_pause_reason and not job.paused:
        # A process death mid-run is never a user's intent to pause — mark it
        # distinguishably so the UI can show "auto-paused (restart)" instead
        # of a plain "paused" a person would otherwise have to investigate.
        job.paused = True
        job.auto_paused_reason = auto_pause_reason
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
    auto_pause: bool = False,
) -> int:
    """Reset persisted RUNNING jobs so stack restarts do not inherit hung state.

    Args:
        store: Job store (default singleton path).
        mode: ``stale`` — only jobs past per-type stale threshold;
              ``all_running`` — every RUNNING job (shutdown cleanup).
        reason: Optional note stored in ``last_error`` when empty.
        auto_pause: When ``True``, also pause each recovered job's schedule
            and stamp ``auto_paused_reason`` with ``reason`` — used for the
            boot/shutdown recovery paths, where a stale/interrupted RUNNING
            job is exactly the signature of a hot-reload or crash, not a
            user decision to stop the schedule.
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
        # A read-only monitoring job is recovered UNPAUSED. Auto-pause protects against a job
        # with half-applied side effects silently re-running; a health check has none, and
        # pausing it means the stack stops being watched at exactly the moment it was restarted.
        # `factor-health` was found auto-paused this way on 2026-09-07 -- the only paused job of
        # 61 -- while 27 factors sat stale on disk and nothing reported it. See
        # `job_tier_policy.is_safe_to_auto_resume`.
        pause_this = auto_pause and not is_safe_to_auto_resume(
            str((job.config or {}).get("job_type") or "")
        )
        _advance_recovered_job(
            job,
            now_ms,
            auto_pause_reason=(f"auto-paused: {reason}" if pause_this and reason else None),
        )
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
        auto_pause=True,
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
        auto_pause=True,
    )
    if count:
        logger.info("stack shutdown recovered %d scheduled research job(s) from running", count)
    return count


def resume_jobs_auto_paused_by_shutdown(
    store: ScheduledResearchJobStore | None = None,
) -> int:
    """Auto-resume jobs that a stack shutdown/restart auto-paused, on the next successful boot.

    The shutdown auto-pause (``recover_scheduler_jobs_on_stack_shutdown``, and the
    executor-shutdown pass in ``api/scheduled_startup.py``) exists to stop a job with
    half-applied side effects from silently re-running mid-restart — a transient safety measure
    for the promotion/restart window, not a permanent operator decision. Before this, nothing
    ever cleared it: a `*/5` or `*/15` collection job caught RUNNING by a routine
    `trade release update` stayed paused across every subsequent restart forever, with no signal
    beyond a JSON field nobody polled. See
    `.claude/backlog/items/2026-09-07-four-jobs-silently-paused-by-a-shutdown.md`.

    Only resumes a job whose ``auto_paused_reason`` exactly matches one of
    :data:`SHUTDOWN_AUTO_PAUSE_REASONS` — i.e. one this process's own shutdown recovery paused.
    A job an operator deliberately paused (``paused=True``, ``auto_paused_reason=None``) is never
    touched, and neither is a job paused for a different system reason (e.g. found stuck RUNNING
    at boot, a crash with genuinely unknown side effects) — resuming those blindly is exactly the
    silent-re-run risk the auto-pause exists to prevent.

    Runs via :func:`pause_control.set_job_enabled` so this is the same single mutation path every
    other pause/resume call site uses, not a second writer of ``paused``/``auto_paused_reason``.

    Args:
        store: Job store (default singleton path).

    Returns:
        Count of jobs resumed.
    """
    from src.scheduled_research.pause_control import set_job_enabled

    store = store or ScheduledResearchJobStore()
    jobs = store.load()
    resumed = 0
    for job in jobs.values():
        if not job.paused or not job.auto_paused_reason:
            continue
        reason = job.auto_paused_reason
        if not any(
            reason == f"auto-paused: {r}" for r in SHUTDOWN_AUTO_PAUSE_REASONS
        ):
            continue
        set_job_enabled(job.id, True, store=store)
        resumed += 1
        logger.info(
            "stack boot auto-resumed shutdown-paused scheduled research job %s (was: %r)",
            job.id,
            reason,
        )
    return resumed


def stale_running_ms_for_job(job: ScheduledResearchJob) -> int:
    """Public alias for API serialization (matches executor recovery thresholds)."""
    return stale_running_ms_for(job)
