"""Schedule a recording job's deferred wake via the scheduled-research infra.

A recording that's waiting for market open (``status="waiting_for_open"``)
gets a one-shot ``ScheduledResearchJob`` whose dispatch callable runs
:func:`recording_jobs.wake_recording_job` at ``next_open_at``.

Dispatch is handled by a dedicated poll loop (:func:`start_recording_wake_poller`)
rather than the general scheduled-research executor. The general executor's
dispatch loop is *intentionally* left stopped on every process boot — it
gates LLM-cost-bearing jobs (agent sessions, index/options research) behind
an explicit "Resume scheduler" click so nothing fires automatically after a
restart. A recording wake is not an LLM-cost job (it only flips a status and
spawns a data-recording subprocess the user already asked for), so tying it
to that same gate means a recording silently never wakes up unless the user
also happens to visit the unrelated Scheduled-Research panel and click
Resume. This module's poller runs unconditionally so ``wait_for_open``
recordings wake up on their own, matching the feature's original contract.

Single source of truth for the schedule expression. ``recording_wake:``
prefix on the job id namespaces these jobs alongside other scheduled-
research entries.

Idempotent: re-registering for the same recording job id cancels any
prior schedule first, so a re-pressed Record button doesn't leave a
stale schedule in the store.
"""

from __future__ import annotations

import asyncio
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


_POLL_INTERVAL_S = 30.0
_poller_task: "asyncio.Task | None" = None


async def _recording_wake_poll_tick(store: "ScheduledResearchJobStore") -> None:
    """Dispatch any due ``recording_wake`` schedule entries and clear them.

    Single-shot semantics: whether or not the recording actually woke
    (it may already be running/stopped/errored — see
    :func:`recording_jobs.wake_recording_job`), the schedule entry is
    removed so it isn't dispatched again on the next tick.
    """
    from src.trade.recording_jobs import wake_recording_job

    try:
        jobs = store.load()
    except Exception:
        logger.exception("recording_wake poller: failed to load store")
        return
    now_ms = int(datetime.now(tz=timezone.utc).timestamp() * 1000)
    due = [
        job
        for job in jobs.values()
        if str(job.config.get("job_type") or "") == JOB_TYPE_RECORDING_WAKE
        and job.next_run_at <= now_ms
    ]
    for job in due:
        recording_job_id = str(job.config.get("recording_job_id") or "").strip()
        if not recording_job_id:
            logger.error("recording_wake poller: job %s missing recording_job_id", job.id)
            _cancel_existing(store, job.id)
            continue
        logger.info("recording_wake poller: firing for recording job %s", recording_job_id)
        try:
            woke = wake_recording_job(recording_job_id)
        except Exception:
            logger.exception(
                "recording_wake poller: wake_recording_job failed for %s", recording_job_id
            )
            woke = False
        if not woke:
            logger.warning(
                "recording_wake poller: recording job %s was not in a wakeable "
                "state (already running/done/errored?)",
                recording_job_id,
            )
        _cancel_existing(store, job.id)


async def _maybe_rearm_auto_record() -> None:
    """When Auto Record is on and nothing is currently recording, kick a
    fresh ``wait_for_open`` job using the persisted config template.

    Self-limiting: ``kick_recording`` computes ``next_open_at`` from *now*,
    so calling this right after today's session ends (market closed)
    naturally schedules tomorrow's session rather than looping today.
    While a job is queued/waiting_for_open/running, ``get_active_job()``
    returns non-``None`` and this is a no-op — so a job that's merely
    *waiting* for tomorrow's open is not re-kicked every tick.
    """
    from src.trade.recording_auto import load_auto_record
    from src.trade.recording_jobs import get_active_job, kick_recording

    state = load_auto_record()
    if not state.get("enabled"):
        return
    config = state.get("config") or {}
    try:
        if get_active_job() is not None:
            return
    except Exception:
        logger.exception("auto-record poller: get_active_job failed")
        return
    logger.info("auto-record poller: no active recording — re-arming for next session")
    try:
        kick_recording(
            underlyings=list(config.get("underlyings") or []),
            equities=list(config.get("equities") or []),
            poll_interval_s=int(config.get("poll_interval_s") or 10),
            category_intervals=config.get("category_intervals"),
            equity_intervals=config.get("equity_intervals"),
            ws_throttle_hz=config.get("ws_throttle_hz"),
            historical_config=config.get("historical_config"),
            wait_for_open=True,
        )
    except Exception:
        logger.exception("auto-record poller: kick_recording failed")


async def _recording_wake_poll_loop(store: "ScheduledResearchJobStore") -> None:
    while True:
        await _recording_wake_poll_tick(store)
        await _maybe_rearm_auto_record()
        await asyncio.sleep(_POLL_INTERVAL_S)


def start_recording_wake_poller(store: "ScheduledResearchJobStore | None" = None) -> None:
    """Start the recording-wake poll loop, if not already running.

    Deliberately independent of the general scheduled-research executor's
    enable/resume gate (see module docstring) — a ``wait_for_open``
    recording must wake up on its own regardless of whether the user has
    ever touched the Scheduled-Research panel. Safe to call repeatedly
    (e.g. on every API startup, including uvicorn --reload respawns);
    a second call while a task is already running is a no-op.
    """
    global _poller_task
    if _poller_task is not None and not _poller_task.done():
        return
    store = store if store is not None else _get_store()
    loop = asyncio.get_running_loop()
    _poller_task = loop.create_task(_recording_wake_poll_loop(store))


async def stop_recording_wake_poller() -> None:
    global _poller_task
    task = _poller_task
    _poller_task = None
    if task is None:
        return
    task.cancel()
    try:
        await task
    except (asyncio.CancelledError, Exception):
        pass
