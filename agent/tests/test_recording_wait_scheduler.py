"""Tests for the recording-wake scheduled-research adapter.

The adapter (``src.trade.recording_wait_scheduler``) registers one-shot
``ScheduledResearchJob`` entries that fire :func:`wake_recording_job`
at ``next_open_at``. These tests verify the schedule-id naming,
idempotent re-registration, cancellation, and dispatch wiring.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from src.scheduled_research.models import JobStatus
from src.scheduled_research.store import ScheduledResearchJobStore
from src.trade.recording_wait_scheduler import (
    JOB_TYPE_RECORDING_WAKE,
    _job_is_fast_failure,
    cancel_recording_wake,
    schedule_recording_wake,
)


def _store(tmp_path: Path) -> ScheduledResearchJobStore:
    return ScheduledResearchJobStore(path=tmp_path / "scheduled_jobs.json")


def _ms(year: int, month: int, day: int, hour: int, minute: int) -> int:
    return int(datetime(year, month, day, hour, minute, tzinfo=timezone.utc).timestamp() * 1000)


def test_schedule_creates_job_with_expected_shape(tmp_path: Path) -> None:
    """schedule_recording_wake persists a PENDING job with the
    recording_job_id in config and the wake deadline as next_run_at.
    """
    store = _store(tmp_path)
    target = datetime(2099, 1, 1, 9, 20, tzinfo=timezone.utc)
    schedule_id = schedule_recording_wake(
        recording_job_id="rec-job-abc",
        next_open_at=target,
        store=store,
    )
    assert schedule_id == "recording_wake:rec-job-abc"

    persisted = store.load()
    assert len(persisted) == 1
    job = persisted[schedule_id]
    assert job.status == JobStatus.PENDING
    assert job.config["job_type"] == JOB_TYPE_RECORDING_WAKE
    assert job.config["recording_job_id"] == "rec-job-abc"
    assert job.next_run_at == _ms(2099, 1, 1, 9, 20)
    # Interval-ms schedule string ("60000" = 1 min re-check). The
    # executor fires when ``next_run_at <= now``, so the schedule
    # string itself is just a safety re-poll interval.
    assert job.schedule == "60000"
    # No agent session is run for these jobs.
    assert job.prompt == ""


def test_schedule_is_idempotent_on_re_registration(tmp_path: Path) -> None:
    """Pressing Record twice for the same job must not leave two
    schedules in the store — re-registering cancels the prior one.
    """
    store = _store(tmp_path)
    target1 = datetime(2099, 1, 1, 9, 20, tzinfo=timezone.utc)
    target2 = datetime(2099, 1, 1, 9, 21, tzinfo=timezone.utc)

    schedule_recording_wake(
        recording_job_id="rec-job-dup",
        next_open_at=target1,
        store=store,
    )
    schedule_recording_wake(
        recording_job_id="rec-job-dup",
        next_open_at=target2,
        store=store,
    )
    persisted = store.load()
    assert len(persisted) == 1, (
        f"expected exactly one schedule for the same recording, got "
        f"{len(persisted)}: {list(persisted)}"
    )
    schedule_id = "recording_wake:rec-job-dup"
    assert persisted[schedule_id].next_run_at == _ms(2099, 1, 1, 9, 21)


def test_cancel_removes_existing_schedule(tmp_path: Path) -> None:
    """``cancel_recording_wake`` returns True when a schedule existed."""
    store = _store(tmp_path)
    target = datetime(2099, 1, 1, 9, 20, tzinfo=timezone.utc)
    schedule_recording_wake(
        recording_job_id="rec-job-cancel",
        next_open_at=target,
        store=store,
    )
    assert cancel_recording_wake(
        recording_job_id="rec-job-cancel", store=store,
    ) is True
    assert store.load() == {}


def test_cancel_returns_false_when_no_schedule(tmp_path: Path) -> None:
    """``cancel_recording_wake`` returns False when there's nothing
    to cancel — already fired, already cancelled, or never registered.
    """
    store = _store(tmp_path)
    assert cancel_recording_wake(
        recording_job_id="rec-job-never-scheduled", store=store,
    ) is False


def test_schedule_with_two_different_recordings(tmp_path: Path) -> None:
    """Two recordings on the same store coexist; their schedule ids
    are namespaced and don't collide."""
    store = _store(tmp_path)
    target = datetime(2099, 1, 1, 9, 20, tzinfo=timezone.utc)
    schedule_recording_wake(
        recording_job_id="rec-job-1",
        next_open_at=target,
        store=store,
    )
    schedule_recording_wake(
        recording_job_id="rec-job-2",
        next_open_at=target,
        store=store,
    )
    persisted = store.load()
    assert set(persisted) == {
        "recording_wake:rec-job-1",
        "recording_wake:rec-job-2",
    }


def test_schedule_validates_interval_ms(tmp_path: Path) -> None:
    """The interval-ms schedule string must pass
    ``ScheduledResearchJobStore.upsert``'s ``validate_schedule`` call.
    We pin the constant at "60000" — change-detect guard in case a
    future refactor accidentally drops a digit or adds a typo.
    """
    store = _store(tmp_path)
    target = datetime(2099, 1, 1, 9, 20, tzinfo=timezone.utc)
    schedule_recording_wake(
        recording_job_id="rec-job-validate",
        next_open_at=target,
        store=store,
    )
    persisted = store.load()
    assert persisted["recording_wake:rec-job-validate"].schedule == "60000"


def test_fast_failure_counts_a_zombie_reconciled_error_with_no_result() -> None:
    """Regression for
    ``.claude/backlog/items/2026-09-11-recording-job-workers-die-before-run.md``:
    a job that dies via the zombie/queued/stale reconciler (``fail_job()``)
    never gets a ``result`` -- only ``complete_job()`` sets one, on a session
    that actually ran. Before the fix, ``cycles is None`` always short-
    circuited to ``None`` ("undetermined") for this entire failure class, so
    the circuit breaker's streak counter never incremented and a broken
    recorder could re-kick forever instead of backing off after 3 failures.
    """
    job = {
        "status": "error",
        "result": None,
        "created_at": "2026-09-17T05:01:40+00:00",
        "_finished_at": datetime(2026, 9, 17, 5, 1, 45, tzinfo=timezone.utc).timestamp(),
    }
    assert _job_is_fast_failure(job) is True


def test_fast_failure_still_undetermined_for_a_job_still_in_flight() -> None:
    """A ``queued``/``running``/``waiting_for_open`` job (no result yet,
    hasn't reached a terminal status) must stay undetermined, not get
    misclassified as a fast failure."""
    assert _job_is_fast_failure({"status": "running", "result": None}) is None


def test_fast_failure_false_when_real_cycles_were_recorded() -> None:
    """A job that completed via ``complete_job()`` with real cycles recorded
    must not be misclassified as a fast failure just because it's ``done``
    rather than ``error``."""
    job = {"status": "done", "result": {"cycles": 12}}
    assert _job_is_fast_failure(job) is False
