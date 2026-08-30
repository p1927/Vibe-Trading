"""Regression test for
``.claude/backlog/items/2026-08-30-recording-wait-for-open-wall-clock-budget-uses-creation-time.md``:

``reconcile_stale_job``'s wall-clock-budget check used to measure elapsed
time from ``created_at`` (stamped once at job creation and never reset),
which for a ``wait_for_open`` job can be arbitrarily longer than the
budget itself since ``wait_for_open`` exists specifically to defer running
until market open, potentially days later. A job that had been sitting in
``waiting_for_open`` past the wall-clock budget would fail immediately the
moment it woke, before it had recorded anything.

The fix stamps a separate ``run_started_at`` field in ``mark_running``
and measures the wall-clock budget against that instead.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.trade import recording_jobs


def _patch_jobs_root(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(recording_jobs, "_jobs_root", lambda: tmp_path)
    # Each test creates its own job against its own tmp_path; clear the
    # module-level active-job cache so a job left running by a previous
    # test in this process doesn't get reused instead.
    with recording_jobs._JOBS_LOCK:
        recording_jobs.RECORDING_JOBS.clear()
        recording_jobs._ACTIVE_JOB_ID = None


def test_stale_job_woken_after_long_deferred_wait_is_not_immediately_failed(
    monkeypatch, tmp_path: Path
) -> None:
    _patch_jobs_root(monkeypatch, tmp_path)
    monkeypatch.setattr(recording_jobs, "_WALL_CLOCK_SECONDS", 28800)

    job_id, _reused = recording_jobs.start_job(underlyings=["NIFTY"], wait_for_open=True)

    # Simulate a job created ~66 hours ago (well past the 8h budget) that
    # has been sitting in `waiting_for_open` the whole time — exactly the
    # concretely-reproduced scenario in the backlog item.
    old_created_at = (datetime.now(timezone.utc) - timedelta(hours=66)).isoformat()

    def _backdate(job):
        job["created_at"] = old_created_at
        job["status"] = "waiting_for_open"
        return True

    recording_jobs._mutate_job_on_disk(job_id, _backdate)
    with recording_jobs._JOBS_LOCK:
        recording_jobs.RECORDING_JOBS.pop(job_id, None)

    # Wake it now (mirrors `wake_recording_job`'s call into `mark_running`
    # without actually spawning a worker subprocess).
    recording_jobs.mark_running(job_id)

    job = recording_jobs._get_job_record(job_id)
    assert job is not None
    assert job["status"] == "running"
    assert job.get("run_started_at") is not None

    # Immediately after waking, reconcile_stale_job must NOT fail the job —
    # this is the bug: it used to compute wall_age from the 66-hour-old
    # created_at and fail instantly.
    with recording_jobs._JOBS_LOCK:
        recording_jobs.RECORDING_JOBS[job_id]["worker_pid"] = None
    monkeypatch.setattr(recording_jobs, "worker_alive", lambda job: True)

    terminalized = recording_jobs.reconcile_stale_job(job_id)
    assert terminalized is False

    job = recording_jobs._get_job_record(job_id)
    assert job["status"] == "running"
    assert job.get("error") is None


def test_stale_job_still_fails_once_run_started_at_exceeds_budget(
    monkeypatch, tmp_path: Path
) -> None:
    _patch_jobs_root(monkeypatch, tmp_path)
    monkeypatch.setattr(recording_jobs, "_WALL_CLOCK_SECONDS", 28800)
    monkeypatch.setattr(recording_jobs, "worker_alive", lambda job: True)

    job_id, _reused = recording_jobs.start_job(underlyings=["NIFTY"])
    recording_jobs.mark_running(job_id)

    old_run_started_at = (datetime.now(timezone.utc) - timedelta(hours=10)).isoformat()

    def _backdate_run(job):
        job["run_started_at"] = old_run_started_at
        return True

    recording_jobs._mutate_job_on_disk(job_id, _backdate_run)
    with recording_jobs._JOBS_LOCK:
        recording_jobs.RECORDING_JOBS.pop(job_id, None)

    terminalized = recording_jobs.reconcile_stale_job(job_id)
    assert terminalized is True

    job = recording_jobs._get_job_record(job_id)
    assert job["status"] == "error"
    assert "wall-clock budget" in str(job.get("error") or "")
