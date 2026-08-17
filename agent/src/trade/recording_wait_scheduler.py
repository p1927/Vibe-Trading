"""Schedule a recording job's deferred wake via the scheduled-research infra.

A recording that's waiting for market open (``status="waiting_for_open"``)
gets a one-shot ``ScheduledResearchJob`` whose dispatch callable runs
:func:`recording_jobs.wake_recording_job` at ``next_open_at``. The
executor polls every ~60 s (existing infra) and dispatches at the
deadline; the dispatch wakes the recording by spawning a fresh worker.

Single source of truth for the schedule expression. ``recording_wake:``
prefix on the job id namespaces these jobs alongside other scheduled-
research entries.

Idempotent: re-registering for the same recording job id cancels any
prior schedule first, so a re-pressed Record button doesn't leave a
stale schedule in the store.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from src.scheduled_research.models import (
    JobStatus,
    ScheduledResearchJob,
    validate_schedule,
)

if TYPE_CHECKING:
    from src.scheduled_research.store import ScheduledResearchJobStore

logger = logging.getLogger(__name__)

JOB_TYPE_RECORDING_WAKE = "recording_wake"


def schedule_recording_wake(
    *,
    recording_job_id: str,
    next_open_at: datetime,
    store: "ScheduledResearchJobStore | None" = None,
) -> str:
    """Register a one-shot schedule that fires
    :func:`recording_jobs.wake_recording_job` at ``next_open_at``.

    Returns the schedule job id (``recording_wake:<recording_job_id>``).
    Cancels any prior schedule for the same recording first so re-pressing
    Record doesn't leave a stale entry.
    """
    store = store if store is not None else _get_store()
    schedule_id = _schedule_id_for(recording_job_id)
    _cancel_existing(store, schedule_id)

    # Schedule is the interval-ms string form (the only non-cron form).
    # We use a 60s interval so the executor re-checks after each tick,
    # and set ``next_run_at`` to the actual deadline so the executor
    # doesn't dispatch early.
    interval_ms = "60000"
    validate_schedule(interval_ms)
    now_ms = int(datetime.now(tz=timezone.utc).timestamp() * 1000)
    target_ms = int(next_open_at.timestamp() * 1000)
    schedule_job = ScheduledResearchJob(
        id=schedule_id,
        prompt="",  # executor doesn't run an agent session for this
        schedule=interval_ms,
        next_run_at=target_ms,
        status=JobStatus.PENDING,
        created_at=now_ms,
        config={
            "job_type": JOB_TYPE_RECORDING_WAKE,
            "recording_job_id": recording_job_id,
        },
    )
    store.upsert(schedule_job)
    logger.info(
        "scheduled recording wake: recording_job_id=%s schedule_id=%s "
        "next_open_at=%s",
        recording_job_id,
        schedule_id,
        next_open_at.isoformat(),
    )
    return schedule_id


def cancel_recording_wake(
    *, recording_job_id: str, store: "ScheduledResearchJobStore | None" = None
) -> bool:
    """Cancel any pending scheduled-research wake for this recording.

    Returns ``True`` if a schedule was cancelled, ``False`` if none
    existed (already fired, already cancelled, or never registered).
    """
    store = store if store is not None else _get_store()
    return _cancel_existing(store, _schedule_id_for(recording_job_id))


def _schedule_id_for(recording_job_id: str) -> str:
    """Stable schedule id for a recording job — namespaces under
    ``recording_wake:`` so other scheduled-research entries don't
    collide, and is reconstructable so re-registration cancels the
    prior schedule."""
    return f"recording_wake:{recording_job_id}"


def _cancel_existing(store: "ScheduledResearchJobStore", schedule_id: str) -> bool:
    """Remove any prior schedule with the same id from the store."""
    try:
        jobs = store.load()
    except Exception:
        logger.exception("recording_wake: failed to load store")
        return False
    if schedule_id in jobs:
        del jobs[schedule_id]
        try:
            store.save(jobs)
        except Exception:
            logger.exception("recording_wake: failed to save store after cancel")
            return False
        return True
    return False


def _get_store() -> "ScheduledResearchJobStore":
    """Return the singleton ScheduledResearchJobStore, importing lazily
    so this module is safe to import from contexts where the store is
    not yet configured (tests)."""
    from src.scheduled_research.store import ScheduledResearchJobStore

    return ScheduledResearchJobStore()
