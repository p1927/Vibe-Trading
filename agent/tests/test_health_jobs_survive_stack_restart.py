"""A read-only monitoring job must not auto-pause itself when the stack restarts.

`.claude/backlog/items/2026-09-07-a-paused-health-job-is-a-silent-monitor.md`.

Auto-pause on shutdown recovery protects against a job with half-applied side effects silently
re-running. A health check has no side effects to half-apply, and pausing it means the stack stops
being watched at exactly the moment it was restarted.

Measured 2026-09-07 on release: `factor-health` was the ONLY paused job of 61, carrying
`auto_paused_reason: "auto-paused: recovered on stack shutdown"`. A daily check that scans the
whole hub is disproportionately likely to be mid-run when a `trade release update` restarts the
tier. Nothing then reported that the global index-history writer had been dead for nine days, or
that 27 factors were stale on disk.
"""

from __future__ import annotations

import time

import pytest

from src.scheduled_research.factor_health_jobs import (
    JOB_TYPE_FACTOR_HEALTH,
    JOB_TYPE_FACTOR_HEALTH_LIVE,
    register_default_factor_health_jobs,
)
from src.scheduled_research.job_tier_policy import (
    SAFE_TO_AUTO_RESUME_JOB_TYPES,
    is_safe_to_auto_resume,
)
from src.scheduled_research.lifecycle import recover_persisted_scheduler_jobs
from src.scheduled_research.models import JobStatus, ScheduledResearchJob


class _Store:
    def __init__(self, jobs=()):
        self._jobs = {j.id: j for j in jobs}

    def get(self, job_id):
        return self._jobs.get(job_id)

    def upsert(self, job):
        self._jobs[job.id] = job

    def load(self):
        return self._jobs

    def save(self, jobs):
        self._jobs = jobs


def _running(job_id: str, job_type: str) -> ScheduledResearchJob:
    now = int(time.time() * 1000)
    return ScheduledResearchJob(
        id=job_id, prompt="p", schedule="0 7 * * *", next_run_at=now,
        status=JobStatus.RUNNING, created_at=now, config={"job_type": job_type},
    )


@pytest.mark.parametrize("job_type", sorted(SAFE_TO_AUTO_RESUME_JOB_TYPES))
def test_a_health_job_running_at_shutdown_comes_back_unpaused(job_type):
    store = _Store([_running("health", job_type)])
    recover_persisted_scheduler_jobs(
        store, mode="all_running", reason="recovered on stack shutdown", auto_pause=True,
    )
    job = store.load()["health"]
    assert job.status is JobStatus.PENDING
    assert job.paused is False, f"{job_type} auto-paused itself; the monitor goes silent"
    assert job.auto_paused_reason is None


def test_a_writing_job_is_still_auto_paused():
    """The protection this narrows must stay intact for everything else — otherwise the fix
    trades a silent monitor for an unattended re-run of a job with real side effects."""
    store = _Store([_running("ingest", "hub_news_ingest")])
    recover_persisted_scheduler_jobs(
        store, mode="all_running", reason="recovered on stack shutdown", auto_pause=True,
    )
    job = store.load()["ingest"]
    assert job.paused is True
    assert job.auto_paused_reason == "auto-paused: recovered on stack shutdown"


def test_an_unknown_job_type_keeps_the_safe_default():
    assert is_safe_to_auto_resume("something_nobody_classified") is False


def test_the_live_freshness_pass_registers_when_its_cron_is_set(monkeypatch):
    """It is opt-in via `FACTOR_HEALTH_LIVE_CRON`, and that variable was unset everywhere until
    2026-09-07 — so `check_factor_freshness()` had never once run on a schedule."""
    monkeypatch.setenv("FACTOR_HEALTH_LIVE_CRON", "0 4 * * 0")
    store = _Store()
    register_default_factor_health_jobs(store)
    assert "factor-health-live" in store.load()
    assert store.get("factor-health-live").config["job_type"] == JOB_TYPE_FACTOR_HEALTH_LIVE
    assert store.get("factor-health").config["job_type"] == JOB_TYPE_FACTOR_HEALTH


def test_without_the_cron_the_live_pass_silently_does_not_exist(monkeypatch):
    """Pins the trap itself: no error, no warning, the job simply never exists."""
    monkeypatch.delenv("FACTOR_HEALTH_LIVE_CRON", raising=False)
    store = _Store()
    register_default_factor_health_jobs(store)
    assert "factor-health-live" not in store.load()
