"""Single mutation path for a scheduled job's enabled/paused state.

The generic scheduler routes (``api/scheduled_routes.py``) and the
prediction-jobs panel (``trade/index_prediction_jobs.py``) both control the
same underlying jobs. Before this module existed they each mutated a
different field to represent "paused" (``paused`` vs. overloading
``status``), so a job visible in both surfaces could show contradictory
pause state depending on which one touched it last. Every pause/resume call
site must go through :func:`set_job_enabled` so that can't happen again.
"""

from __future__ import annotations

import os
import time
from typing import Optional

from .job_tier_policy import collection_job_dispatch_enabled, is_collection_job
from .models import JobStatus, ScheduledResearchJob
from .store import ScheduledResearchJobStore


class JobNotRunningError(ValueError):
    """Raised when a cancel is attempted on a job that isn't RUNNING."""


class JobPausedError(ValueError):
    """Raised when trigger-now is attempted on a paused job."""


class JobAlreadyRunningError(ValueError):
    """Raised when trigger-now is attempted on a job already RUNNING."""


class JobCollectionDispatchBlockedError(ValueError):
    """Raised when trigger-now is attempted on a release-exclusive collection
    job outside the release tier (see job_tier_policy.py). Distinct from
    JobPausedError/JobAlreadyRunningError: setting next_run_at to "now" here
    would silently never fire — the executor's tick loop excludes this job
    type on this profile entirely (executor.py's _collection_dispatch_blocked),
    with no error and no log line, so the caller needs a real signal instead
    of a 200 that looks like it worked."""


def set_job_enabled(
    job_id: str,
    enabled: bool,
    *,
    store: ScheduledResearchJobStore,
) -> Optional[ScheduledResearchJob]:
    """Enable or disable a job's recurring schedule.

    Disabling sets ``paused = True`` without touching ``status`` or
    ``next_run_at``, so a live run is unaffected and the original cadence
    resumes unchanged on resume. Enabling clears both ``paused`` and
    ``auto_paused_reason`` regardless of whether the pause was user- or
    system-initiated.

    Enabling a job that is currently ``FAILED`` (terminal — reached after
    ``max_consecutive_failures`` retries, see ``executor.py``'s
    ``is_due()``, which explicitly excludes ``FAILED`` from ever being
    picked up again) also resets it to ``PENDING`` with
    ``consecutive_failures``/``failure_kind`` cleared. Without this, a
    resumed job that happened to be FAILED stayed permanently invisible to
    the scheduler — ``paused`` would read ``False`` (looking "resumed" in
    the API/UI) while ``is_due()`` silently kept excluding it forever, with
    no path back except a direct store edit. Live-observed 2026-09-02: see
    [[2026-08-30-scheduler-sequential-dispatch-drains-slowly]]. A job that
    is merely ``PENDING``/``COMPLETED`` is untouched by this — only the
    terminal-FAILED case gets the extra reset, so ordinary pause/resume of
    a healthy job keeps working exactly as before.

    Args:
        job_id: The job to update.
        enabled: ``True`` to resume, ``False`` to pause.
        store: The store the job is persisted in.

    Returns:
        The updated job, or ``None`` if no job with that id exists.
    """
    job = store.get(job_id)
    if job is None:
        return None
    job.paused = not enabled
    if enabled:
        job.auto_paused_reason = None
        if job.status == JobStatus.FAILED:
            job.status = JobStatus.PENDING
            job.consecutive_failures = 0
            job.failure_kind = None
    store.upsert(job)
    return job


def cancel_running_job(
    job_id: str,
    *,
    store: ScheduledResearchJobStore,
) -> Optional[ScheduledResearchJob]:
    """Cancel a job's currently in-flight execution.

    Distinct from :func:`set_job_enabled`: this only affects the current run
    (sets ``status = CANCELLED``) and leaves ``paused`` untouched, so the
    job's future schedule is unaffected. Best-effort/cooperative — it sets a
    ``_cancel_requested`` scratch flag in ``config`` that a dispatch
    coroutine may poll between stages; it does not preemptively interrupt a
    dispatch already in flight.

    Args:
        job_id: The job to cancel.
        store: The store the job is persisted in.

    Returns:
        The updated job, or ``None`` if no job with that id exists.

    Raises:
        JobNotRunningError: If the job exists but isn't currently RUNNING.
    """
    job = store.get(job_id)
    if job is None:
        return None
    if job.status != JobStatus.RUNNING:
        raise JobNotRunningError(
            f"job {job_id} is not running (status={job.status.value})"
        )
    job.status = JobStatus.CANCELLED
    job.last_error = "cancelled by user"
    job.config["_cancel_requested"] = True
    store.upsert(job)
    return job


def trigger_job_now(
    job_id: str,
    *,
    store: ScheduledResearchJobStore,
) -> Optional[ScheduledResearchJob]:
    """Fire a job immediately without changing its enabled/paused state.

    Distinct from :func:`set_job_enabled`: this doesn't touch ``paused``, so
    a paused job stays paused (and is refused, see below) — it only pulls
    ``next_run_at`` forward to now so the executor's next due-check picks it
    up. Mirrors ``trade/index_prediction_jobs.trigger_index_prediction_job``'s
    guard logic, generalized to any job (no ``job_type`` gate).

    Args:
        job_id: The job to trigger.
        store: The store the job is persisted in.

    Returns:
        The updated job, or ``None`` if no job with that id exists.

    Raises:
        JobPausedError: If the job is currently paused.
        JobAlreadyRunningError: If the job is currently RUNNING.
        JobCollectionDispatchBlockedError: If this job type is release-exclusive
            and this process isn't running under STACK_PROFILE=release.
    """
    job = store.get(job_id)
    if job is None:
        return None
    if job.paused:
        raise JobPausedError(f"job {job_id} is paused; resume it before triggering")
    if job.status == JobStatus.RUNNING:
        raise JobAlreadyRunningError(f"job {job_id} is already running")
    job_type = str((job.config or {}).get("job_type") or "")
    stack_profile = os.environ.get("STACK_PROFILE", "dev")
    if is_collection_job(job_type) and not collection_job_dispatch_enabled(stack_profile):
        raise JobCollectionDispatchBlockedError(
            f"job {job_id} is a data-collection job type ({job_type!r}) and cannot dispatch "
            f"on this tier (STACK_PROFILE={stack_profile!r}); trigger it against the release "
            "tier instead"
        )
    job.next_run_at = int(time.time() * 1000)
    store.upsert(job)
    return job
