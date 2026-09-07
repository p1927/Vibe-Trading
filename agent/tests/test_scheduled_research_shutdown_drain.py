"""`stop()` must not relabel a run that finishes as shutdown begins.

See .claude/backlog/items/2026-09-07-shutdown-recovery-relabels-a-completed-run.md —
`stop()` used to call `recover_all_running_on_shutdown()` *before* touching the dispatch
tasks, so a job whose work landed milliseconds later was already marked PENDING and its
own completion write stood down. Live-measured 2026-09-07: two real completions landed
13-24ms after shutdown began and were both filed as `recovered on executor shutdown`
despite `had_work: True`.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest
from src.scheduled_research import executor as executor_module
from src.scheduled_research.executor import ScheduledResearchExecutor
from src.scheduled_research.models import JobStatus, ScheduledResearchJob
from src.scheduled_research.store import ScheduledResearchJobStore


@pytest.fixture(autouse=True)
def _no_startup_grace(monkeypatch: pytest.MonkeyPatch) -> None:
    """`_run()` sleeps SCHEDULED_RESEARCH_STARTUP_GRACE_MS (default 30s) before its first tick,
    to stop a uvicorn --reload save from cascading dispatches. These tests need a real dispatch
    actually in flight when `stop()` is called. Patched at the executor's own imported name
    rather than via the env var, because `get_env_config()` is a process-wide singleton that
    ignores env changes made after first access."""
    monkeypatch.setattr(executor_module, "_startup_grace_ms", lambda: 0)


def _store(tmp_path: Path) -> ScheduledResearchJobStore:
    return ScheduledResearchJobStore(path=tmp_path / "jobs.json")


def _job(job_id: str, *, next_run_at: int = 0) -> ScheduledResearchJob:
    return ScheduledResearchJob(
        id=job_id,
        prompt=f"prompt for {job_id}",
        schedule="1000",
        next_run_at=next_run_at,
        status=JobStatus.PENDING,
        created_at=0,
    )


def test_dispatch_finishing_during_shutdown_is_recorded_completed(tmp_path: Path) -> None:
    """The regression: a run that lands inside the drace grace keeps its own verdict."""
    store = _store(tmp_path)
    store.upsert(_job("slow-but-nearly-done"))
    released = asyncio.Event()

    async def dispatch(job: ScheduledResearchJob) -> None:
        released.set()
        # Outlives the start of stop() but lands well inside the grace window,
        # which is exactly the shape that used to be mislabelled.
        await asyncio.sleep(0.15)

    async def scenario() -> None:
        executor = ScheduledResearchExecutor(
            store, dispatch, tick_interval_ms=1, shutdown_drain_grace_ms=5_000
        )
        executor.start()
        await asyncio.wait_for(released.wait(), timeout=5)
        await executor.stop(auto_pause_reason="auto-paused: recovered on stack shutdown")

    asyncio.run(scenario())

    saved = store.get("slow-but-nearly-done")
    assert saved is not None
    assert saved.status == JobStatus.COMPLETED, (
        f"a run that finished during the shutdown drain was relabelled: "
        f"status={saved.status} last_error={saved.last_error!r}"
    )
    assert saved.last_error is None
    assert saved.paused is False
    assert saved.auto_paused_reason is None


def test_dispatch_outlasting_the_grace_is_still_recovered(tmp_path: Path) -> None:
    """The drain must not become a way for a long job to block or dodge shutdown."""
    store = _store(tmp_path)
    store.upsert(_job("genuinely-long"))
    released = asyncio.Event()

    async def dispatch(job: ScheduledResearchJob) -> None:
        released.set()
        await asyncio.sleep(30)  # far beyond the grace below

    async def scenario() -> None:
        executor = ScheduledResearchExecutor(
            store, dispatch, tick_interval_ms=1, shutdown_drain_grace_ms=50
        )
        executor.start()
        await asyncio.wait_for(released.wait(), timeout=5)
        await asyncio.wait_for(
            executor.stop(auto_pause_reason="auto-paused: recovered on stack shutdown"),
            timeout=10,
        )

    asyncio.run(scenario())

    saved = store.get("genuinely-long")
    assert saved is not None
    # Interrupted for real: recovered to pending and marked as such, exactly as before.
    assert saved.status == JobStatus.PENDING
    assert saved.paused is True
    assert saved.auto_paused_reason == "auto-paused: recovered on stack shutdown"


def test_stop_with_no_dispatch_in_flight_still_recovers_a_stranded_running_job(
    tmp_path: Path,
) -> None:
    """A job left RUNNING with no live dispatch must still be recovered by `stop()` —
    the reordering must not make recovery conditional on having drained something."""
    store = _store(tmp_path)

    async def dispatch(job: ScheduledResearchJob) -> None:  # pragma: no cover - never due
        raise AssertionError("no job should be dispatched in this test")

    async def scenario() -> None:
        executor = ScheduledResearchExecutor(store, dispatch, tick_interval_ms=1)
        executor.start()
        # Inserted AFTER start(): start() runs its own recover_stale_running(startup=True),
        # which would otherwise recover this job before stop() ever saw it, leaving the
        # assertion below testing startup recovery instead of shutdown recovery.
        stranded = _job("stranded", next_run_at=10**12)
        stranded.status = JobStatus.RUNNING
        # last_run_at = now, so the stale-running watchdog does not claim it either.
        stranded.last_run_at = int(time.time() * 1000)
        store.upsert(stranded)
        await asyncio.sleep(0.05)
        await executor.stop(auto_pause_reason="auto-paused: recovered on stack shutdown")

    asyncio.run(scenario())

    saved = store.get("stranded")
    assert saved is not None
    assert saved.status == JobStatus.PENDING
    assert saved.auto_paused_reason == "auto-paused: recovered on stack shutdown"
