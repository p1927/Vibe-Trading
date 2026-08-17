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
