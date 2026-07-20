"""Tests for scheduled research stack lifecycle recovery."""

from __future__ import annotations

import time
from pathlib import Path

from src.scheduled_research.executor import is_job_stale_running, stale_running_ms_for
from src.scheduled_research.lifecycle import (
    recover_persisted_scheduler_jobs,
    recover_scheduler_jobs_on_stack_boot,
    recover_scheduler_jobs_on_stack_shutdown,
)
from src.scheduled_research.models import JobStatus, ScheduledResearchJob
from src.scheduled_research.store import ScheduledResearchJobStore


def _store(tmp_path: Path) -> ScheduledResearchJobStore:
    return ScheduledResearchJobStore(path=tmp_path / "jobs.json")


def _running_job(job_id: str, *, last_run_at: int = 0) -> ScheduledResearchJob:
    return ScheduledResearchJob(
        id=job_id,
        prompt="test",
        schedule="1000",
        next_run_at=0,
        status=JobStatus.RUNNING,
        created_at=0,
        last_run_at=last_run_at,
        config={"job_type": "index_plan_refresh"},
    )


def test_stack_boot_recovers_only_stale_running_jobs(tmp_path: Path) -> None:
    store = _store(tmp_path)
    now_ms = int(time.time() * 1000)
    threshold = stale_running_ms_for(_running_job("x"))
    fresh = _running_job("fresh", last_run_at=now_ms - 1_000)
    stale = _running_job("stale", last_run_at=now_ms - threshold - 1_000)
    store.upsert(fresh)
    store.upsert(stale)

    count = recover_scheduler_jobs_on_stack_boot(store)
    assert count == 1

    fresh_saved = store.get("fresh")
    stale_saved = store.get("stale")
    assert fresh_saved is not None
    assert stale_saved is not None
    assert fresh_saved.status == JobStatus.RUNNING
    assert stale_saved.status == JobStatus.PENDING


def test_stack_shutdown_recovers_all_running_jobs(tmp_path: Path) -> None:
    store = _store(tmp_path)
    now_ms = int(time.time() * 1000)
    store.upsert(_running_job("a", last_run_at=now_ms - 1_000))
    store.upsert(_running_job("b", last_run_at=now_ms - 5_000))

    count = recover_scheduler_jobs_on_stack_shutdown(store)
    assert count == 2
    assert store.get("a").status == JobStatus.PENDING  # type: ignore[union-attr]
    assert store.get("b").status == JobStatus.PENDING  # type: ignore[union-attr]


def test_index_plan_refresh_stale_threshold_matches_executor(tmp_path: Path) -> None:
    now_ms = int(time.time() * 1000)
    job = _running_job("poll", last_run_at=now_ms - 11 * 60 * 1000)
    assert is_job_stale_running(job, now_ms) is True
    store = _store(tmp_path)
    store.upsert(job)
    assert recover_persisted_scheduler_jobs(store=store, mode="stale") == 1
