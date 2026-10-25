"""nudge_watch_job_after_plan_approval writes only the fields it owns, under the store lock."""
from __future__ import annotations

from pathlib import Path

import pytest

from src.scheduled_research import autonomous_agent_jobs as jobs
from src.scheduled_research import store as store_mod
from src.scheduled_research.models import JobStatus, ScheduledResearchJob


def _job(job_id: str, status: JobStatus) -> ScheduledResearchJob:
    return ScheduledResearchJob(
        id=job_id, prompt="p", schedule="60000", next_run_at=10**15, status=status, created_at=0,
        config={"job_type": "autonomous_agent_watch"},
    )


@pytest.fixture
def store(tmp_path: Path, monkeypatch):
    s = store_mod.ScheduledResearchJobStore(path=tmp_path / "jobs.json")
    monkeypatch.setattr(store_mod, "ScheduledResearchJobStore", lambda: s)
    monkeypatch.setattr(jobs, "is_autonomous_scheduler_enabled", lambda: True)
    return s


def test_pending_watch_job_is_pulled_due_now(store):
    store.upsert(_job("aa_1-watch", JobStatus.PENDING))
    jobs.nudge_watch_job_after_plan_approval("aa_1")
    got = store.get("aa_1-watch")
    assert got.status == JobStatus.PENDING and got.next_run_at < 10**15


@pytest.mark.parametrize("status", [JobStatus.RUNNING])
def test_running_watch_job_is_left_alone(store, status):
    store.upsert(_job("aa_1-watch", status))
    jobs.nudge_watch_job_after_plan_approval("aa_1")
    got = store.get("aa_1-watch")
    assert got.status == status and got.next_run_at == 10**15


def test_missing_job_is_a_noop(store):
    jobs.nudge_watch_job_after_plan_approval("aa_none")
    assert store.get("aa_none-watch") is None


def test_unrelated_run_state_written_since_the_read_survives(store, monkeypatch):
    """A whole-record upsert of the read snapshot reverted what the executor wrote in between."""
    store.upsert(_job("aa_1-watch", JobStatus.PENDING))
    real_get = store.get

    def get_then_executor_writes(job_id):
        snap = real_get(job_id)
        store.update_run_state(job_id, last_error="boom", consecutive_failures=2)
        return snap

    monkeypatch.setattr(store, "get", get_then_executor_writes)
    jobs.nudge_watch_job_after_plan_approval("aa_1")
    got = real_get("aa_1-watch")
    assert got.last_error == "boom" and got.consecutive_failures == 2
