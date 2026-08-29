"""Scheduled-research dispatch for recording-wake jobs.

When a recording job is created with ``wait_for_open=True`` and the
market is closed, the API entry does NOT spawn a worker subprocess
(Phase C). Instead it registers a ``ScheduledResearchJob`` whose
dispatch callable calls :func:`recording_jobs.wake_recording_job`
at ``next_open_at``.

This module is the dispatch layer. The wake-registration layer is
:mod:`src.trade.recording_wait_scheduler`.

Job shape:
  - id: ``recording_wake:<recording_job_id>``
  - prompt: empty (the executor doesn't run an agent session for this)
  - schedule: interval-ms string (the recording-wake scheduler
    computes ``next_run_at`` from the recording's ``next_open_at``)
  - config: ``{"job_type": "recording_wake", "recording_job_id": "..."}``
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

JOB_TYPE_RECORDING_WAKE = "recording_wake"

RECORDING_WAKE_JOB_TYPES = frozenset({JOB_TYPE_RECORDING_WAKE})


async def dispatch_recording_wake_job(job) -> None:
    """Called by the scheduled-research executor when a
    ``recording_wake`` job is due.

    Flips the recording's status ``waiting_for_open → running`` and
    spawns a fresh worker. Idempotent — if the recording is no longer
    in a wakeable state, ``wake_recording_job`` returns False and we
    log a warning. The scheduled-research executor then marks the
    job COMPLETED either way (single-shot semantics).
    """
    from src.scheduled_research.run_log_buffer import run_logged

    await run_logged(job, _dispatch_recording_wake_job_inner, run_in_thread=False)


async def _dispatch_recording_wake_job_inner(job) -> None:
    from src.trade.recording_jobs import wake_recording_job

    config = getattr(job, "config", None) or {}
    recording_job_id = str(config.get("recording_job_id") or "").strip()
    if not recording_job_id:
        logger.error(
            "recording_wake job %s missing recording_job_id in config",
            job.id,
        )
        return
    logger.info(
        "recording_wake dispatch firing for recording job %s",
        recording_job_id,
    )
    woke = wake_recording_job(recording_job_id)
    if not woke:
        logger.warning(
            "recording_wake dispatch: recording job %s was not in a "
            "wakeable state (already running/done/errored?)",
            recording_job_id,
        )
