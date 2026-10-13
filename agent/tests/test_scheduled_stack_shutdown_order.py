"""A run that finishes inside the shutdown drain must be recorded as the run it was (review vibe-scheduler).

``executor.stop()`` drains in-flight dispatches for a bounded grace and only then recovers whatever
is still RUNNING, precisely so a run that completes milliseconds after shutdown began is not
relabelled "recovered on executor shutdown" (2026-09-07-shutdown-recovery-relabels-a-completed-run).
``shutdown_scheduled_research_stack`` ran the store-level ``recover_scheduler_jobs_on_stack_shutdown``
BEFORE ``executor.stop()``, which reset every RUNNING job to PENDING and paused it at once, so the
drain was moot and the run's completion write found a PENDING job and was discarded.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

from src.api.scheduled_startup import shutdown_scheduled_research_stack
from src.scheduled_research.executor import ScheduledResearchExecutor
from src.scheduled_research.models import JobStatus, ScheduledResearchJob
from src.scheduled_research.store import ScheduledResearchJobStore


def test_a_run_that_finishes_inside_the_drain_keeps_its_completion(tmp_path: Path) -> None:
    store = ScheduledResearchJobStore(tmp_path / "jobs.json")
    now = int(time.time() * 1000)
    store.upsert(
        ScheduledResearchJob(
            id="quick", prompt="p", schedule="3600000", next_run_at=now - 1, created_at=now - 10,
            config={"job_type": "news_quality_eval"},
        )
    )

    async def dispatch(job: ScheduledResearchJob) -> None:
        await asyncio.sleep(0.3)  # still running when shutdown begins, done well inside the 5 s drain

    async def scenario() -> None:
        executor = ScheduledResearchExecutor(store, dispatch, shutdown_drain_grace_ms=3000)
        await executor.tick(now, wait=False)
        await asyncio.sleep(0.05)
        assert store.get("quick").status is JobStatus.RUNNING
        await shutdown_scheduled_research_stack(lambda: store, executor)

    asyncio.run(scenario())

    job = store.get("quick")
    assert job.status is JobStatus.COMPLETED, (job.status, job.last_error)
    assert job.failure_kind is None and job.last_error is None
    assert not job.paused, job.auto_paused_reason


def test_a_run_still_going_after_the_drain_is_recovered_and_paused(tmp_path: Path) -> None:
    store = ScheduledResearchJobStore(tmp_path / "jobs.json")
    now = int(time.time() * 1000)
    store.upsert(
        ScheduledResearchJob(
            id="slow", prompt="p", schedule="3600000", next_run_at=now - 1, created_at=now - 10,
            config={"job_type": "news_quality_eval"},
        )
    )

    async def dispatch(job: ScheduledResearchJob) -> None:
        await asyncio.sleep(30)

    async def scenario() -> None:
        executor = ScheduledResearchExecutor(store, dispatch, shutdown_drain_grace_ms=100)
        await executor.tick(now, wait=False)
        await asyncio.sleep(0.05)
        await shutdown_scheduled_research_stack(lambda: store, executor)

    asyncio.run(scenario())

    job = store.get("slow")
    assert job.status is JobStatus.PENDING and job.paused
    assert job.failure_kind == "dispatch" and "recovered" in (job.last_error or "")
