"""Tests for the scheduled research executor."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from src.scheduled_research.executor import (
    ScheduledResearchExecutor,
    defer_fresh_registrations,
    dispatch_timeout_ms_for,
    is_due,
    is_job_stale_running,
    next_due,
    scheduler_enabled_from_env,
    stale_running_ms_for,
)
from src.scheduled_research.models import JobStatus, ScheduledResearchJob
from src.scheduled_research.store import ScheduledResearchJobStore


def _ms(year: int, month: int, day: int, hour: int, minute: int) -> int:
    return int(datetime(year, month, day, hour, minute, tzinfo=timezone.utc).timestamp() * 1000)


def _local_ms(tz: str, year: int, month: int, day: int, hour: int, minute: int, *, fold: int = 0) -> int:
    """Epoch-ms of a wall-clock time in an IANA zone (``fold=0`` = first occurrence)."""
    local = datetime(year, month, day, hour, minute, tzinfo=ZoneInfo(tz), fold=fold)
    return int(local.timestamp() * 1000)


def _store(tmp_path: Path) -> ScheduledResearchJobStore:
    return ScheduledResearchJobStore(path=tmp_path / "jobs.json")


def _job(
    job_id: str = "job-001",
    *,
    schedule: str = "1000",
    next_run_at: int = 0,
    status: JobStatus = JobStatus.PENDING,
    created_at: int = 0,
    timezone: str | None = None,
) -> ScheduledResearchJob:
    return ScheduledResearchJob(
        id=job_id,
        prompt=f"prompt for {job_id}",
        schedule=schedule,
        next_run_at=next_run_at,
        status=status,
        created_at=created_at,
        timezone=timezone,
    )


def test_interval_job_fires_and_persists_completion(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.upsert(_job(schedule="5000", next_run_at=1000))
    calls: list[tuple[str, JobStatus]] = []

    async def dispatch(job: ScheduledResearchJob) -> None:
        calls.append((job.id, job.status))

    async def scenario() -> None:
        executor = ScheduledResearchExecutor(store, dispatch)
        await executor.tick(1500)

    asyncio.run(scenario())

    saved = store.get("job-001")
    assert saved is not None
    assert calls == [("job-001", JobStatus.RUNNING)]
    assert saved.status == JobStatus.COMPLETED
    assert saved.last_run_at == 1500
    assert saved.next_run_at == 6500


def test_executor_skips_completion_write_during_shutdown(tmp_path: Path) -> None:
    store = _store(tmp_path)
    job = _job(schedule="5000", next_run_at=1000, created_at=100)
    store.upsert(job)
    started = asyncio.Event()

    async def dispatch(running_job: ScheduledResearchJob) -> None:
        started.set()
        await asyncio.sleep(0.05)

    async def scenario() -> None:
        executor = ScheduledResearchExecutor(store, dispatch)
        tick_task = asyncio.create_task(executor.tick(1500))
        await started.wait()
        store.upsert(
            ScheduledResearchJob(
                id=job.id,
                prompt=job.prompt,
                schedule=job.schedule,
                next_run_at=job.next_run_at,
                status=JobStatus.PENDING,
                created_at=job.created_at,
                last_error="recovered on shutdown",
            )
        )
        executor._stopping = True
        await tick_task

    asyncio.run(scenario())

    saved = store.get("job-001")
    assert saved is not None
    assert saved.status == JobStatus.PENDING
    assert saved.last_error == "recovered on shutdown"
    store = _store(tmp_path)
    store.upsert(_job(schedule="5000", next_run_at=1000))
    calls: list[tuple[str, JobStatus]] = []

    async def dispatch(job: ScheduledResearchJob) -> None:
        calls.append((job.id, job.status))

    async def scenario() -> None:
        executor = ScheduledResearchExecutor(store, dispatch)
        await executor.tick(1500)

    asyncio.run(scenario())

    saved = store.get("job-001")
    assert saved is not None
    assert calls == [("job-001", JobStatus.RUNNING)]
    assert saved.status == JobStatus.COMPLETED
    assert saved.last_run_at == 1500
    assert saved.next_run_at == 6500


def test_cron_job_next_due_and_not_before_due_time(tmp_path: Path) -> None:
    store = _store(tmp_path)
    before_due = _ms(2026, 6, 20, 5, 59)
    due_at = _ms(2026, 6, 20, 6, 0)
    following_due = _ms(2026, 6, 20, 12, 0)
    assert next_due("0 */6 * * *", before_due) == due_at

    store.upsert(_job(schedule="0 */6 * * *", next_run_at=due_at))
    calls: list[str] = []

    async def dispatch(job: ScheduledResearchJob) -> None:
        calls.append(job.id)

    async def scenario() -> None:
        executor = ScheduledResearchExecutor(store, dispatch)
        await executor.tick(due_at - 1)
        assert calls == []
        await executor.tick(due_at)

    asyncio.run(scenario())

    saved = store.get("job-001")
    assert saved is not None
    assert calls == ["job-001"]
    assert saved.status == JobStatus.COMPLETED
    assert saved.last_run_at == due_at
    assert saved.next_run_at == following_due


def test_cron_uses_standard_or_semantics_for_restricted_day_fields() -> None:
    thursday = _ms(2026, 6, 11, 0, 1)

    # The 12th is a Friday, so it matches day-of-week even though it is not
    # the 13th day of the month.
    assert next_due("0 0 13 * 5", thursday) == _ms(2026, 6, 12, 0, 0)

    friday = _ms(2026, 6, 12, 0, 1)
    # The 13th is a Saturday, so the following run matches day-of-month even
    # though it is not Friday.
    assert next_due("0 0 13 * 5", friday) == _ms(2026, 6, 13, 0, 0)


def test_cron_wildcard_day_field_leaves_other_day_field_authoritative() -> None:
    thursday = _ms(2026, 6, 11, 0, 1)

    assert next_due("0 0 13 * *", thursday) == _ms(2026, 6, 13, 0, 0)
    assert next_due("0 0 * * 5", thursday) == _ms(2026, 6, 12, 0, 0)


def test_dispatch_failure_stays_retryable_and_tick_continues(tmp_path: Path) -> None:
    store = _store(tmp_path)
    bad_job = _job("bad", next_run_at=10)
    bad_job.config = {"failure_threshold": 1}
    store.upsert(bad_job)
    store.upsert(_job("good", next_run_at=20))
    calls: list[str] = []

    async def dispatch(job: ScheduledResearchJob) -> None:
        calls.append(job.id)
        if job.id == "bad":
            raise RuntimeError("boom")

    async def scenario() -> None:
        executor = ScheduledResearchExecutor(
            store,
            dispatch,
            max_consecutive_failures=3,
            retry_base_delay_ms=1000,
            retry_max_delay_ms=4000,
        )
        await executor.tick(100)

    asyncio.run(scenario())

    bad = store.get("bad")
    good = store.get("good")
    assert bad is not None
    assert good is not None
    assert calls == ["bad", "good"]
    assert bad.status == JobStatus.PENDING
    assert bad.consecutive_failures == 1
    assert bad.failure_kind == "dispatch"
    assert bad.last_error == "RuntimeError: boom"
    assert bad.next_run_at == 1100
    assert good.status == JobStatus.COMPLETED


def test_transient_dispatch_failure_retries_then_success_resets_state(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.upsert(_job(schedule="1000", next_run_at=0))
    calls = 0

    async def dispatch(job: ScheduledResearchJob) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise TimeoutError("provider timed out")

    async def scenario() -> None:
        executor = ScheduledResearchExecutor(
            store,
            dispatch,
            max_consecutive_failures=3,
            retry_base_delay_ms=1000,
            retry_max_delay_ms=4000,
        )
        await executor.tick(100)
        await executor.tick(1099)
        assert calls == 1
        await executor.tick(1100)

    asyncio.run(scenario())

    saved = store.get("job-001")
    assert saved is not None
    assert calls == 2
    assert saved.status == JobStatus.COMPLETED
    assert saved.consecutive_failures == 0
    assert saved.failure_kind is None
    assert saved.last_error is None
    assert saved.last_run_at == 1100
    assert saved.next_run_at == 2100


def test_repeated_dispatch_failures_become_terminal_at_threshold(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.upsert(_job(schedule="1000", next_run_at=0))
    calls = 0

    async def dispatch(job: ScheduledResearchJob) -> None:
        nonlocal calls
        calls += 1
        raise ConnectionError("provider unavailable")

    async def scenario() -> None:
        executor = ScheduledResearchExecutor(
            store,
            dispatch,
            max_consecutive_failures=2,
            retry_base_delay_ms=0,
            retry_max_delay_ms=0,
        )
        await executor.tick(100)
        await executor.tick(1100)
        await executor.tick(10_000)

    asyncio.run(scenario())

    saved = store.get("job-001")
    assert saved is not None
    assert calls == 2
    assert saved.status == JobStatus.FAILED
    assert saved.consecutive_failures == 2
    assert saved.failure_kind == "dispatch"
    assert saved.last_error == "ConnectionError: provider unavailable"
    assert saved.next_run_at == 2100


def test_persisted_dispatch_error_is_redacted_and_bounded(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.upsert(_job(next_run_at=0))
    secret = "scheduler-secret-value"
    raw_error = f"api_key={secret} path={Path.home()}/private/trace " + ("x" * 2000)

    async def dispatch(job: ScheduledResearchJob) -> None:
        raise RuntimeError(raw_error)

    async def scenario() -> None:
        executor = ScheduledResearchExecutor(
            store,
            dispatch,
            max_consecutive_failures=1,
            retry_base_delay_ms=0,
            retry_max_delay_ms=0,
        )
        await executor.tick(100)

    asyncio.run(scenario())

    saved = store.get("job-001")
    assert saved is not None
    assert saved.last_error is not None
    assert secret not in saved.last_error
    assert str(Path.home()) not in saved.last_error
    assert "[redacted]" in saved.last_error
    assert "<redacted>/private/trace" in saved.last_error
    assert len(saved.last_error) == 1000
    assert saved.last_error.endswith("...")


def test_stale_running_job_recovers_to_pending_and_fires_on_next_tick(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.upsert(_job("stale", schedule="1000", next_run_at=10, status=JobStatus.RUNNING))
    calls: list[tuple[str, JobStatus]] = []

    async def dispatch(job: ScheduledResearchJob) -> None:
        calls.append((job.id, job.status))

    async def scenario() -> None:
        executor = ScheduledResearchExecutor(store, dispatch)
        assert executor.recover_stale_running(0, startup=True) == 1
        recovered = store.get("stale")
        assert recovered is not None
        assert recovered.status == JobStatus.PENDING
        assert recovered.next_run_at == 1000
        await executor.tick(100)
        assert calls == []
        await executor.tick(1000)

    asyncio.run(scenario())

    saved = store.get("stale")
    assert saved is not None
    assert calls == [("stale", JobStatus.RUNNING)]
    assert saved.status == JobStatus.COMPLETED
    assert saved.last_run_at == 1000
    assert saved.next_run_at == 2000


def test_impossible_cron_marks_failed_and_tick_continues(tmp_path: Path) -> None:
    store = _store(tmp_path)
    now = _ms(2026, 2, 1, 0, 0)
    store.upsert(_job("bad", schedule="0 0 31 2 *", next_run_at=10))
    store.upsert(_job("good", schedule="1000", next_run_at=20))
    calls: list[str] = []

    with pytest.raises(ValueError):
        next_due("0 0 31 2 *", now)

    async def dispatch(job: ScheduledResearchJob) -> None:
        calls.append(job.id)

    async def scenario() -> None:
        executor = ScheduledResearchExecutor(store, dispatch)
        await executor.tick(now)

    asyncio.run(scenario())

    bad = store.get("bad")
    good = store.get("good")
    assert bad is not None
    assert good is not None
    assert calls == ["bad", "good"]
    assert bad.status == JobStatus.FAILED
    assert bad.last_run_at == now
    assert bad.next_run_at == 10
    assert bad.failure_kind == "schedule"
    assert "cron schedule has no matching time" in (bad.last_error or "")
    assert good.status == JobStatus.COMPLETED


def test_cancelled_and_running_jobs_are_skipped(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.upsert(_job("cancelled", next_run_at=0, status=JobStatus.CANCELLED))
    store.upsert(_job("pending", next_run_at=0, status=JobStatus.PENDING))
    calls: list[str] = []

    async def dispatch(job: ScheduledResearchJob) -> None:
        calls.append(job.id)

    async def scenario() -> None:
        executor = ScheduledResearchExecutor(store, dispatch)
        assert executor.recover_stale_running(startup=True) == 0
        store.upsert(_job("running", next_run_at=0, status=JobStatus.RUNNING))
        await executor.tick(100)

    asyncio.run(scenario())

    assert is_due(store.get("cancelled"), 100) is False  # type: ignore[arg-type]
    assert is_due(store.get("running"), 100) is False  # type: ignore[arg-type]
    assert calls == ["pending"]
    assert store.get("cancelled").status == JobStatus.CANCELLED  # type: ignore[union-attr]
    assert store.get("running").status == JobStatus.RUNNING  # type: ignore[union-attr]
    assert store.get("pending").status == JobStatus.COMPLETED  # type: ignore[union-attr]


def test_failed_job_is_not_redispatched(tmp_path: Path) -> None:
    store = _store(tmp_path)
    # A terminal FAILED job whose next_run_at is still in the past must not be
    # re-dispatched on the next tick (it would otherwise fire every poll).
    store.upsert(_job("failed", next_run_at=0, status=JobStatus.FAILED))
    calls: list[str] = []

    async def dispatch(job: ScheduledResearchJob) -> None:
        calls.append(job.id)

    async def scenario() -> None:
        executor = ScheduledResearchExecutor(store, dispatch)
        await executor.tick(100)

    asyncio.run(scenario())

    assert is_due(store.get("failed"), 100) is False  # type: ignore[arg-type]
    assert calls == []
    assert store.get("failed").status == JobStatus.FAILED  # type: ignore[union-attr]


def test_retry_backoff_is_exponential_and_capped(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.upsert(_job(schedule="100", next_run_at=0))

    async def dispatch(job: ScheduledResearchJob) -> None:
        raise TimeoutError("outage")

    async def scenario() -> None:
        executor = ScheduledResearchExecutor(
            store,
            dispatch,
            max_consecutive_failures=4,
            retry_base_delay_ms=1000,
            retry_max_delay_ms=1500,
        )
        await executor.tick(0)
        first = store.get("job-001")
        assert first is not None
        assert first.next_run_at == 1000
        await executor.tick(1000)

    asyncio.run(scenario())

    saved = store.get("job-001")
    assert saved is not None
    assert saved.consecutive_failures == 2
    assert saved.next_run_at == 2500


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"max_consecutive_failures": 0}, "max_consecutive_failures"),
        ({"retry_base_delay_ms": -1}, "retry_base_delay_ms"),
        (
            {"retry_base_delay_ms": 100, "retry_max_delay_ms": 99},
            "retry_max_delay_ms",
        ),
    ],
)
def test_invalid_retry_policy_is_rejected(
    tmp_path: Path, kwargs: dict[str, int], message: str
) -> None:
    async def dispatch(job: ScheduledResearchJob) -> None:
        return None

    with pytest.raises(ValueError, match=message):
        ScheduledResearchExecutor(_store(tmp_path), dispatch, **kwargs)


def test_job_deleted_during_dispatch_is_not_resurrected(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.upsert(_job("job-001", schedule="1000", next_run_at=0))

    async def dispatch(job: ScheduledResearchJob) -> None:
        # Simulate a user DELETE landing while the run is in flight.
        store.delete(job.id)

    async def scenario() -> None:
        executor = ScheduledResearchExecutor(store, dispatch)
        await executor.tick(100)

    asyncio.run(scenario())

    # The deleted job must not reappear after dispatch completes.
    assert store.get("job-001") is None


def test_job_replaced_during_dispatch_is_not_overwritten(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.upsert(_job("job-001", schedule="1000", next_run_at=0))

    async def dispatch(job: ScheduledResearchJob) -> None:
        # Simulate a user POST replacing the job mid-run. The API stamps a fresh
        # created_at on every create, which is how a replacement is told apart
        # from the in-flight original (even when the schedule is unchanged).
        store.upsert(_job("job-001", schedule="5000", next_run_at=900, created_at=999))

    async def scenario() -> None:
        executor = ScheduledResearchExecutor(store, dispatch)
        await executor.tick(100)

    asyncio.run(scenario())

    saved = store.get("job-001")
    assert saved is not None
    # The replacement definition is preserved, not clobbered by the old run.
    assert saved.schedule == "5000"
    assert saved.next_run_at == 900
    assert saved.created_at == 999
    assert saved.status == JobStatus.PENDING


def test_restart_after_missed_window_honors_persisted_next_run_at(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.upsert(_job(schedule="5000", next_run_at=1000))
    calls: list[str] = []

    async def dispatch(job: ScheduledResearchJob) -> None:
        calls.append(job.id)

    async def scenario() -> None:
        first = ScheduledResearchExecutor(store, dispatch)
        await first.tick(20_000)
        assert calls == ["job-001"]

        restarted = ScheduledResearchExecutor(store, dispatch)
        await restarted.tick(20_000)

    asyncio.run(scenario())

    saved = store.get("job-001")
    assert saved is not None
    assert calls == ["job-001"]
    assert saved.status == JobStatus.COMPLETED
    assert saved.last_run_at == 20_000
    assert saved.next_run_at == 25_000


def test_disabled_executor_start_stop_are_noops(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.upsert(_job(next_run_at=0))
    calls: list[str] = []

    async def dispatch(job: ScheduledResearchJob) -> None:
        calls.append(job.id)

    async def scenario() -> None:
        executor = ScheduledResearchExecutor(
            store,
            dispatch,
            tick_interval_ms=1,
            now_fn=lambda: 100,
            enabled=False,
        )
        executor.start()
        assert executor.is_running is False
        await executor.stop()

    asyncio.run(scenario())

    assert scheduler_enabled_from_env("") is False
    assert scheduler_enabled_from_env("true") is True
    assert calls == []
    assert store.get("job-001").status == JobStatus.PENDING  # type: ignore[union-attr]



def test_periodic_stale_running_recovery(tmp_path: Path) -> None:
    store = _store(tmp_path)
    stale = _job("stale", schedule="5000", next_run_at=10, status=JobStatus.RUNNING, created_at=0)
    stale.last_run_at = 0
    store.upsert(stale)
    now_ms = 10_000_000

    async def dispatch(job: ScheduledResearchJob) -> None:
        pass

    async def scenario() -> None:
        executor = ScheduledResearchExecutor(store, dispatch, now_fn=lambda: now_ms)
        assert executor.recover_stale_running(now_ms, startup=False) == 1
        recovered = store.get("stale")
        assert recovered is not None
        assert recovered.status == JobStatus.PENDING

    asyncio.run(scenario())


def test_dispatch_timeout_marks_completed_and_unblocks_next_job(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A dispatch timeout for an index_* job cooperatively cancels in-flight
    # pipeline work via pipeline_cancel.request_pipeline_cancel(), which
    # writes log/index_prediction_jobs/_pipeline_cancel.json under
    # TRADE_STACK_ROOT. Pin that root to this test's tmp_path so the real
    # repo's log directory is never touched and no stray cancel flag can
    # leak into other tests (see
    # .claude/backlog/items/2026-08-27-pipeline-cancel-test-pollution.md).
    monkeypatch.setenv("TRADE_STACK_ROOT", str(tmp_path))
    # index_plan_refresh is a collection-type job (job_tier_policy.py) — gated to
    # STACK_PROFILE=release only. This test is about dispatch-timeout handling, not tier
    # gating, so pin the profile to release rather than pick a non-collection job_type.
    monkeypatch.setenv("STACK_PROFILE", "release")
    store = _store(tmp_path)
    slow = _job("slow", schedule="1000", next_run_at=10)
    slow.config = {"job_type": "index_plan_refresh", "dispatch_timeout_ms": 50}
    store.upsert(slow)
    store.upsert(_job("fast", schedule="1000", next_run_at=10, created_at=1))

    async def dispatch(job: ScheduledResearchJob) -> None:
        if job.id == "slow":
            await asyncio.sleep(0.2)

    async def scenario() -> None:
        executor = ScheduledResearchExecutor(store, dispatch, tick_interval_ms=1)
        await executor.tick(100)

    asyncio.run(scenario())

    slow_saved = store.get("slow")
    fast_saved = store.get("fast")
    assert slow_saved is not None
    assert fast_saved is not None
    # A dispatch timeout is a dispatch failure like any other: it counts
    # toward the consecutive-failure threshold and retries on backoff rather
    # than staying COMPLETED forever, so one timeout with the default
    # threshold (3) leaves the job PENDING for a retry, not COMPLETED.
    assert slow_saved.status == JobStatus.PENDING
    assert slow_saved.failure_kind == "dispatch"
    assert "timed out" in (slow_saved.last_error or "")
    assert slow_saved.consecutive_failures == 1
    assert slow_saved.config.get("_timed_out") is True
    assert fast_saved.status == JobStatus.COMPLETED


def test_due_jobs_dispatch_concurrently_up_to_the_configured_bound(tmp_path: Path) -> None:
    store = _store(tmp_path)
    for i in range(3):
        store.upsert(_job(f"job-{i}", schedule="1000", next_run_at=10, created_at=i))

    in_flight = 0
    max_in_flight = 0

    async def dispatch(job: ScheduledResearchJob) -> None:
        nonlocal in_flight, max_in_flight
        in_flight += 1
        max_in_flight = max(max_in_flight, in_flight)
        await asyncio.sleep(0.05)
        in_flight -= 1

    async def scenario() -> None:
        executor = ScheduledResearchExecutor(store, dispatch, dispatch_concurrency=3)
        await executor.tick(100)

    asyncio.run(scenario())

    # All three were due in the same tick and share nothing but the store;
    # bounded concurrency should overlap their awaited dispatches instead of
    # forcing job N+1 to wait for job N to fully finish.
    assert max_in_flight == 3
    for i in range(3):
        saved = store.get(f"job-{i}")
        assert saved is not None
        assert saved.status == JobStatus.COMPLETED
        assert saved.last_run_at == 100


def test_concurrent_dispatch_does_not_corrupt_other_jobs_store_state(tmp_path: Path) -> None:
    store = _store(tmp_path)
    for i in range(5):
        store.upsert(_job(f"job-{i}", schedule="1000", next_run_at=10, created_at=i))

    async def dispatch(job: ScheduledResearchJob) -> None:
        # Stagger completion order so writes land interleaved, not in the
        # order jobs were dispatched.
        await asyncio.sleep(0.01 * (5 - int(job.id.split("-")[1])))

    async def scenario() -> None:
        executor = ScheduledResearchExecutor(store, dispatch, dispatch_concurrency=5)
        await executor.tick(100)

    asyncio.run(scenario())

    for i in range(5):
        saved = store.get(f"job-{i}")
        assert saved is not None
        assert saved.status == JobStatus.COMPLETED
        assert saved.last_run_at == 100
        assert saved.consecutive_failures == 0


def test_index_plan_refresh_uses_shorter_stale_threshold(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("INDEX_PLAN_REFRESH_STALE_MS", "600000")
    poll = _job("poll", schedule="*/5 * * * *", status=JobStatus.RUNNING, created_at=0)
    poll.last_run_at = 0
    poll.config = {"job_type": "index_plan_refresh"}
    heavy = _job("heavy", schedule="0 6 * * *", status=JobStatus.RUNNING, created_at=0)
    heavy.last_run_at = 0
    heavy.config = {"job_type": "index_calibration"}
    now_ms = 11 * 60 * 1000

    assert stale_running_ms_for(poll) == 600_000
    assert is_job_stale_running(poll, now_ms) is True
    assert is_job_stale_running(heavy, now_ms) is False
    assert dispatch_timeout_ms_for(poll) == 10 * 60 * 1000


def test_stale_threshold_always_exceeds_dispatch_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression: stale-running threshold must be >= dispatch timeout + buffer.

    Previously ``stale_running_ms_for`` returned the global default (45 min) for
    every job_type not in the special-case list. ``hub_news_entity`` has a
    dispatch timeout of 20 min and ``hub_news_ingest`` has 10 min — both
    shorter than 45 min. A long-running dispatch finishing near its timeout
    boundary would have ``last_run_at`` close to the threshold and the watchdog
    could recover it mid-cleanup, discarding the completion write. The fix
    guarantees the threshold is at least ``dispatch_timeout + watchdog_buffer``.
    """
    monkeypatch.setenv("SCHEDULED_RESEARCH_STALE_RUNNING_MS", "10000")  # 10s default
    monkeypatch.setenv(
        "SCHEDULED_RESEARCH_WATCHDOG_INTERVAL_MS", "60000"
    )  # 60s — matches default; clamp floor is 60s

    # hub_news_entity: 20 min dispatch
    drain = _job(
        "drain",
        schedule="*/15 * * * *",
        status=JobStatus.RUNNING,
        created_at=0,
    )
    drain.last_run_at = 0
    drain.config = {"job_type": "hub_news_entity"}
    drain_timeout = dispatch_timeout_ms_for(drain)
    drain_stale = stale_running_ms_for(drain)
    assert drain_timeout == 20 * 60 * 1000
    assert drain_stale >= drain_timeout + 60_000, (
        f"stale ({drain_stale}) must be >= dispatch_timeout ({drain_timeout}) + 60s buffer"
    )

    # hub_news_ingest: 10 min dispatch
    ingest = _job(
        "ingest",
        schedule="0 */4 * * *",
        status=JobStatus.RUNNING,
        created_at=0,
    )
    ingest.last_run_at = 0
    ingest.config = {"job_type": "hub_news_ingest"}
    ingest_timeout = dispatch_timeout_ms_for(ingest)
    ingest_stale = stale_running_ms_for(ingest)
    assert ingest_timeout == 10 * 60 * 1000
    assert ingest_stale >= ingest_timeout + 60_000, (
        f"stale ({ingest_stale}) must be >= dispatch_timeout ({ingest_timeout}) + 60s buffer"
    )

    # Custom dispatch_timeout in config overrides and is also covered
    custom = _job(
        "custom",
        schedule="1000",
        status=JobStatus.RUNNING,
        created_at=0,
    )
    custom.last_run_at = 0
    custom.config = {"job_type": "company_research_archive", "dispatch_timeout_ms": 30 * 60 * 1000}
    custom_stale = stale_running_ms_for(custom)
    custom_timeout = dispatch_timeout_ms_for(custom)
    assert custom_timeout == 30 * 60 * 1000
    assert custom_stale >= custom_timeout + 60_000

    # Watchdog cannot recover a dispatch still within its dispatch_timeout + buffer
    # window. After last_run_at = T, watchdog at T + dispatch_timeout + buffer/2
    # must NOT mark stale.
    now_ms = drain_timeout + 30_000  # half-buffer into the safe window
    assert is_job_stale_running(drain, now_ms) is False, (
        "watchdog must not recover a dispatch within its timeout + buffer window"
    )
    # But the watchdog CAN recover once the safe window is exceeded (this is
    # the genuine crash-recovery path that must still work).
    now_ms = drain_timeout + 2 * 60_000  # well past timeout + buffer
    assert is_job_stale_running(drain, now_ms) is True, (
        "watchdog must still recover a hung dispatch after timeout + buffer"
    )


def test_watchdog_recovers_stale_job_independently(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SCHEDULED_RESEARCH_WATCHDOG_INTERVAL_MS", "20")
    store = _store(tmp_path)
    stale = _job("stale", schedule="1000", next_run_at=10, status=JobStatus.RUNNING, created_at=0)
    stale.last_run_at = 0
    stale.config = {"job_type": "index_plan_refresh"}
    store.upsert(stale)
    now_ms = 11 * 60 * 1000

    async def dispatch(job: ScheduledResearchJob) -> None:
        await asyncio.sleep(3600)

    async def scenario() -> None:
        executor = ScheduledResearchExecutor(
            store,
            dispatch,
            now_fn=lambda: now_ms,
            tick_interval_ms=1_000_000,
        )
        executor.start()
        await asyncio.sleep(0.05)
        await executor.stop()

    asyncio.run(scenario())

    recovered = store.get("stale")
    assert recovered is not None


def test_defer_fresh_registrations_pushes_unrun_jobs_forward(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Hot-reload safety: a brand-new PENDING job that has never fired
    should be deferred by ``SCHEDULED_RESEARCH_FRESH_DEFER_MS`` so that
    ``next_run_at`` is not in the past when the next executor tick runs.

    Jobs that have fired at least once must NOT be touched — pushing an
    already-cadenced job forward would desync its schedule.
    """
    monkeypatch.setenv("SCHEDULED_RESEARCH_FRESH_DEFER_MS", "1800000")  # 30 min
    store = _store(tmp_path)
    fresh = _job("fresh", schedule="0 6 * * *", next_run_at=100)
    fresh.last_run_at = None
    store.upsert(fresh)
    matured = _job("matured", schedule="0 6 * * *", next_run_at=200)
    matured.last_run_at = 50
    store.upsert(matured)

    deferred = defer_fresh_registrations(store, now_ms=1_000)

    assert deferred == 1
    fresh_after = store.get("fresh")
    matured_after = store.get("matured")
    assert fresh_after is not None
    assert matured_after is not None
    assert fresh_after.next_run_at == 1_000 + 1_800_000
    assert matured_after.next_run_at == 200  # unchanged


def test_defer_fresh_registrations_disabled_with_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Set ``SCHEDULED_RESEARCH_FRESH_DEFER_MS=0`` to opt out (preserve
    pre-fix behaviour for tests / legacy stacks).
    """
    monkeypatch.setenv("SCHEDULED_RESEARCH_FRESH_DEFER_MS", "0")
    store = _store(tmp_path)
    fresh = _job("fresh", schedule="0 6 * * *", next_run_at=100)
    store.upsert(fresh)

    deferred = defer_fresh_registrations(store, now_ms=1_000)

    assert deferred == 0
    assert store.get("fresh").next_run_at == 100


def test_defer_startup_backlog_defers_autonomous_watch_when_agent_not_running(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An autonomous_agent_watch job whose bound agent is not running
    must be deferred one tick instead of firing and returning
    ``agent_not_running`` on every hot reload.
    """
    import sys
    from pathlib import Path as _Path

    # Provide a stub for trade_integrations.autonomous_agents.store so the
    # helper resolves without the full integrations package being installed.
    stub_module = type(sys)("trade_integrations.autonomous_agents.store")
    _AGENTS: dict[str, dict[str, str]] = {}

    def _get_agent(agent_id: str) -> dict[str, str] | None:
        return _AGENTS.get(agent_id)

    stub_module.get_agent = _get_agent  # type: ignore[attr-defined]
    # Use monkeypatch.setitem (not a bare sys.modules[...] = ...) so these fake
    # stub modules are removed again at test teardown. A prior version of this
    # test left them installed permanently, which broke every later test in
    # the same process that imports anything else under
    # trade_integrations.autonomous_agents (e.g. intent_capabilities) — the
    # stub has no __path__, so those imports raised, and callers with broad
    # except-and-degrade handling (like classify_prefetch_widget_intent)
    # silently fell back to a wrong default instead of surfacing the error.
    if "trade_integrations" not in sys.modules:
        monkeypatch.setitem(sys.modules, "trade_integrations", type(sys)("trade_integrations"))
    monkeypatch.setitem(
        sys.modules,
        "trade_integrations.autonomous_agents",
        type(sys)("trade_integrations.autonomous_agents"),
    )
    monkeypatch.setitem(sys.modules, "trade_integrations.autonomous_agents.store", stub_module)

    store = _store(tmp_path)
    watch = _job("aa_stopped-watch", schedule="420000", next_run_at=10)
    watch.config = {"job_type": "autonomous_agent_watch", "autonomous_agent_id": "aa_stopped"}
    store.upsert(watch)

    calls: list[str] = []

    async def dispatch(job: ScheduledResearchJob) -> None:
        calls.append(job.id)

    async def scenario() -> None:
        # Default tick interval is 60s; defer-fallback pushes to 50 + 60_000.
        executor = ScheduledResearchExecutor(
            store,
            dispatch,
            now_fn=lambda: 50,
            tick_interval_ms=60_000,
        )
        # Backlog runs once: agent "aa_stopped" is not running, so the watch
        # is deferred by one tick interval (50 + 60_000).
        deferred = executor.defer_startup_backlog(50)
        assert deferred == 1
        # Tick at 50: defer pushed next_run_at to 50 + 60_000, so the job is
        # not due and dispatch is NOT called. Without the fix, this tick
        # would have fired and returned agent_not_running.
        await executor.tick(50)
        assert calls == [], (
            "defer must push next_run_at past now so the reload-tick does NOT "
            "fire the watch while agent_not_running"
        )

    asyncio.run(scenario())

    saved = store.get("aa_stopped-watch")
    assert saved is not None
    # The defer must have pushed past the original 10.
    assert saved.next_run_at > 10
    assert saved.status == JobStatus.PENDING


def test_running_job_with_no_last_run_at_is_not_stale(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression: a freshly dispatched job with a fresh last_run_at must
    survive the watchdog until completion can persist.

    Previously the stale watchdog would recover a RUNNING job whose
    ``last_run_at`` was older than the stale threshold WHILE the dispatch
    coroutine was awaiting inside ``_run_job``. The watchdog reset
    ``status`` to PENDING and advanced ``next_run_at``; when dispatch
    completed, ``_persist_completion`` silently discarded the completion
    write (because ``current.status != RUNNING``), so ``last_run_at`` stayed
    frozen at the old timestamp even though the job ran successfully. The
    fix stamps ``last_run_at = now_ms`` BEFORE the dispatch await so the
    watchdog sees a fresh timestamp.
    """
    # hub_news_entity is a collection-type job (job_tier_policy.py) — gated to
    # STACK_PROFILE=release only. This test is about watchdog/staleness handling, not tier
    # gating, so pin the profile to release rather than pick a non-collection job_type.
    monkeypatch.setenv("STACK_PROFILE", "release")
    store = _store(tmp_path)
    # A PENDING job with a 30-day-old last_run_at — the watchdog would
    # previously have flagged this stale on the first watchdog tick after
    # dispatch started.
    old_completed_ms = 30 * 24 * 60 * 60 * 1000
    job = _job(
        "fresh-runner",
        schedule="*/15 * * * *",
        next_run_at=old_completed_ms,
        created_at=old_completed_ms,
    )
    job.last_run_at = old_completed_ms
    job.config = {"job_type": "hub_news_entity", "mode": "drain", "ticker": "NIFTY"}
    store.upsert(job)

    # First tick at now_ms: dispatches the due job. Inside _run_job, the
    # fix stamps last_run_at = now_ms BEFORE awaiting dispatch. A watchdog
    # tick interleaved between the upsert and the dispatch await would
    # otherwise see the old last_run_at and recover the job.
    now_ms = old_completed_ms + 60 * 1000
    calls: list[str] = []

    async def dispatch(j: ScheduledResearchJob) -> None:
        calls.append(j.id)

    async def scenario() -> None:
        executor = ScheduledResearchExecutor(store, dispatch)
        await executor.tick(now_ms)

    asyncio.run(scenario())

    saved = store.get("fresh-runner")
    assert saved is not None
    assert calls == ["fresh-runner"], "dispatch must run exactly once"
    assert saved.last_run_at == now_ms, (
        "last_run_at must be the tick timestamp, not the old 30-day value — "
        "the bug froze it at the old value because the watchdog reset status "
        "to PENDING before _persist_completion could write the new value"
    )
    assert saved.status == JobStatus.COMPLETED
    assert saved.next_run_at > now_ms, "next_run_at must advance to the next 15-min slot"


def test_mid_dispatch_watchdog_does_not_recover_job(tmp_path: Path) -> None:
    """Regression: the watchdog must leave a freshly-started dispatch alone.

    With the fix, ``_run_job`` stamps ``last_run_at = now_ms`` before
    awaiting the dispatch. A watchdog tick that runs while the dispatch is
    in flight must see ``now_ms - last_run_at < stale_threshold`` and leave
    the job's RUNNING status alone so completion can persist.
    """
    store = _store(tmp_path)
    now_ms = 1_000_000
    # 30-day-old job that just transitioned to RUNNING
    job = _job(
        "in-flight",
        schedule="*/15 * * * *",
        next_run_at=now_ms,
        status=JobStatus.RUNNING,
        created_at=0,
    )
    job.last_run_at = now_ms  # _run_job just stamped this
    job.config = {"job_type": "hub_news_entity"}
    store.upsert(job)

    # 1 minute later, watchdog ticks
    watchdog_now = now_ms + 60 * 1000

    async def scenario() -> None:
        async def dispatch(j: ScheduledResearchJob) -> None:
            pass

        executor = ScheduledResearchExecutor(store, dispatch)
        recovered = executor.recover_stale_running(watchdog_now, startup=False)

    asyncio.run(scenario())
    saved = store.get("in-flight")
    assert saved is not None
    assert saved.status == JobStatus.RUNNING, (
        "watchdog must NOT recover a job that just started dispatching — "
        "last_run_at is fresh and within the stale threshold"
    )


def test_stale_running_still_recovers_when_last_run_at_is_old(tmp_path: Path) -> None:
    """Regression: the fix must not regress the original recovery path.

    A RUNNING job whose ``last_run_at`` is genuinely older than the stale
    threshold (i.e. an actual hang from a previous dispatch) must still be
    recovered.
    """
    store = _store(tmp_path)
    long_ago = 24 * 60 * 60 * 1000
    job = _job(
        "hung",
        schedule="*/15 * * * *",
        next_run_at=long_ago - 100,
        status=JobStatus.RUNNING,
        created_at=long_ago,
    )
    job.last_run_at = long_ago  # last_run_at was set 24h ago — genuinely stale
    job.config = {"job_type": "index_plan_refresh"}
    store.upsert(job)

    now_ms = long_ago + 60 * 60 * 1000  # 1 hour after last_run_at
    recovered = 0

    async def scenario() -> None:
        nonlocal recovered

        async def dispatch(j: ScheduledResearchJob) -> None:
            pass

        executor = ScheduledResearchExecutor(store, dispatch)
        recovered = executor.recover_stale_running(now_ms, startup=False)

    asyncio.run(scenario())

    assert recovered == 1, "a hung job with old last_run_at must still be recovered"
    saved = store.get("hung")
    assert saved is not None
    assert saved.status == JobStatus.PENDING
    assert saved.next_run_at > now_ms, "recovered job must be advanced to its next cron slot"


def test_recovered_running_job_with_no_last_run_at_still_recovered(tmp_path: Path) -> None:
    """Regression: a never-completed job (last_run_at=None, RUNNING) is still
    flagged stale by the watchdog via the created_at fallback.

    Without this, an orphan from a crash on a job's very first run (no
    last_run_at written) would never be recovered.
    """
    job = _job(
        "never-finished",
        schedule="*/15 * * * *",
        next_run_at=0,
        status=JobStatus.RUNNING,
        created_at=0,
    )
    job.last_run_at = None  # crashed on first run, never wrote last_run_at
    job.config = {"job_type": "hub_news_entity"}

    # Long after created_at → stale by the created_at fallback
    now_ms = 60 * 60 * 1000  # 1 hour after creation
    assert is_job_stale_running(job, now_ms) is True

def test_tz_cron_weekday_survives_spring_forward_auckland() -> None:
    # NZ springs forward on Sun 2026-09-27 (02:00 NZST -> 03:00 NZDT). A
    # weekday-23:30 cadence authored in Auckland must fire Fri then Mon at
    # 23:30 local on both sides of the transition, even though the UTC offset
    # moves from +12 to +13.
    friday_fire = _local_ms("Pacific/Auckland", 2026, 9, 25, 23, 30)
    monday_fire = _local_ms("Pacific/Auckland", 2026, 9, 28, 23, 30)
    assert next_due("30 23 * * 1-5", friday_fire, "Pacific/Auckland") == monday_fire

    fired_local = datetime.fromtimestamp(monday_fire / 1000.0, ZoneInfo("Pacific/Auckland"))
    assert (fired_local.weekday(), fired_local.hour, fired_local.minute) == (0, 23, 30)


def test_tz_cron_weekday_survives_fall_back_auckland() -> None:
    # NZ falls back on Sun 2026-04-05 (03:00 NZDT -> 02:00 NZST).
    friday_fire = _local_ms("Pacific/Auckland", 2026, 4, 3, 23, 30)
    monday_fire = _local_ms("Pacific/Auckland", 2026, 4, 6, 23, 30)
    assert next_due("30 23 * * 1-5", friday_fire, "Pacific/Auckland") == monday_fire


def test_tz_cron_weekday_is_evaluated_in_authoring_zone() -> None:
    # Monday 00:30 in Auckland is still Sunday in UTC; the weekday field must
    # follow the authoring wall clock, not the UTC calendar.
    sunday_noon = _local_ms("Pacific/Auckland", 2026, 6, 14, 12, 0)
    monday_first = _local_ms("Pacific/Auckland", 2026, 6, 15, 0, 30)
    result = next_due("30 0 * * 1", sunday_noon, "Pacific/Auckland")
    assert result == monday_first
    assert datetime.fromtimestamp(result / 1000.0, timezone.utc).weekday() == 6  # Sunday in UTC


def test_tz_cron_half_hour_offset_adelaide() -> None:
    # ACST is UTC+9:30, so a 09:00 local fire lands on a half-hour UTC boundary.
    after = _local_ms("Australia/Adelaide", 2026, 6, 10, 9, 0)
    next_fire = _local_ms("Australia/Adelaide", 2026, 6, 11, 9, 0)
    result = next_due("0 9 * * *", after, "Australia/Adelaide")
    assert result == next_fire
    assert result % 3_600_000 == 1_800_000

    # Across the Adelaide spring-forward (Sun 2026-10-04, +9:30 -> +10:30) the
    # local fire time is preserved while the UTC instant shifts by an hour.
    before_transition = _local_ms("Australia/Adelaide", 2026, 10, 3, 9, 0)
    after_transition = _local_ms("Australia/Adelaide", 2026, 10, 4, 9, 0)
    assert next_due("0 9 * * *", before_transition, "Australia/Adelaide") == after_transition


def test_tz_cron_spring_forward_gap_skips_occurrence_new_york() -> None:
    # 02:30 does not exist on Sun 2026-03-08 in America/New_York (02:00 EST
    # jumps to 03:00 EDT). The occurrence is skipped, not shifted.
    saturday_fire = _local_ms("America/New_York", 2026, 3, 7, 2, 30)
    monday_fire = _local_ms("America/New_York", 2026, 3, 9, 2, 30)
    assert next_due("30 2 * * *", saturday_fire, "America/New_York") == monday_fire


def test_tz_cron_fall_back_ambiguous_time_runs_once_at_first_occurrence_new_york() -> None:
    # 01:30 happens twice on Sun 2026-11-01 in America/New_York (EDT 05:30Z,
    # then EST 06:30Z after the clocks fall back). The job runs once, at the
    # first occurrence, and the second occurrence is not a separate firing.
    saturday_fire = _local_ms("America/New_York", 2026, 10, 31, 1, 30)
    first_occurrence = _local_ms("America/New_York", 2026, 11, 1, 1, 30, fold=0)
    assert first_occurrence == _ms(2026, 11, 1, 5, 30)
    assert next_due("30 1 * * *", saturday_fire, "America/New_York") == first_occurrence

    monday_fire = _local_ms("America/New_York", 2026, 11, 2, 1, 30)
    assert next_due("30 1 * * *", first_occurrence, "America/New_York") == monday_fire
    assert next_due("30 1 * * *", first_occurrence, "America/New_York") != _ms(2026, 11, 1, 6, 30)


def test_tz_none_keeps_utc_semantics_for_extended_grammar() -> None:
    # 2026-07-31 is a Friday; the next weekday fire after Friday 11:30 UTC is
    # Monday 11:30 UTC. Without a timezone the new range grammar still
    # evaluates on the UTC wall clock exactly as before.
    assert next_due("30 11 * * 1-5", _ms(2026, 7, 31, 11, 30)) == _ms(2026, 8, 3, 11, 30)


def test_tz_aware_next_due_defaults_match_legacy_signature() -> None:
    reference = _ms(2026, 6, 11, 0, 1)
    for schedule in ("0 */6 * * *", "0 0 13 * 5", "60000"):
        assert next_due(schedule, reference) == next_due(schedule, reference, None)


def test_interval_schedule_ignores_timezone() -> None:
    assert next_due("60000", 5_000, "Pacific/Auckland") == 65_000
    # Even an unresolvable key: interval advancement must not depend on the
    # host's timezone database.
    assert next_due("60000", 5_000, "Not/AZone") == 65_000


def test_unknown_timezone_raises_value_error() -> None:
    with pytest.raises(ValueError, match="not a recognized IANA timezone"):
        next_due("0 12 * * *", 0, "Not/AZone")


def test_tz_cron_is_strictly_after_in_local_zone() -> None:
    exact_fire = _local_ms("Pacific/Auckland", 2026, 6, 15, 12, 0)
    next_day = _local_ms("Pacific/Auckland", 2026, 6, 16, 12, 0)
    assert next_due("0 12 * * *", exact_fire, "Pacific/Auckland") == next_day


def test_executor_advances_tz_job_on_local_calendar(tmp_path: Path) -> None:
    store = _store(tmp_path)
    friday_fire = _local_ms("Pacific/Auckland", 2026, 9, 25, 23, 30)
    monday_fire = _local_ms("Pacific/Auckland", 2026, 9, 28, 23, 30)
    store.upsert(
        _job(
            schedule="30 23 * * 1-5",
            next_run_at=friday_fire,
            timezone="Pacific/Auckland",
        )
    )
    calls: list[str] = []

    async def dispatch(job: ScheduledResearchJob) -> None:
        calls.append(job.id)

    async def scenario() -> None:
        executor = ScheduledResearchExecutor(store, dispatch)
        await executor.tick(friday_fire)

    asyncio.run(scenario())

    saved = store.get("job-001")
    assert saved is not None
    assert calls == ["job-001"]
    assert saved.status == JobStatus.COMPLETED
    assert saved.timezone == "Pacific/Auckland"
    assert saved.next_run_at == monday_fire


def test_executor_marks_job_failed_when_timezone_unresolvable(tmp_path: Path) -> None:
    store = _store(tmp_path)
    # The store checks only the timezone's shape, so a key another host's tz
    # database knew persists fine; this host surfaces it per-job at
    # advancement time instead of crashing lifecycle writes.
    store.upsert(_job(schedule="0 12 * * *", next_run_at=10, timezone="Not/AZone"))
    calls: list[str] = []

    async def dispatch(job: ScheduledResearchJob) -> None:
        calls.append(job.id)

    async def scenario() -> None:
        executor = ScheduledResearchExecutor(store, dispatch)
        await executor.tick(20)

    asyncio.run(scenario())

    saved = store.get("job-001")
    assert saved is not None
    assert calls == ["job-001"]
    assert saved.status == JobStatus.FAILED
    assert saved.failure_kind == "schedule"
    assert saved.last_error is not None


def test_tick_continues_past_a_job_whose_lifecycle_write_raises(tmp_path: Path) -> None:
    class ExplodingLifecycleWriteStore(ScheduledResearchJobStore):
        def update_run_state(self, job_id: str, **state_fields):
            if job_id == "bad":
                raise RuntimeError("disk full")
            return super().update_run_state(job_id, **state_fields)

    store = ExplodingLifecycleWriteStore(path=tmp_path / "jobs.json")
    good_store = ScheduledResearchJobStore(path=tmp_path / "jobs.json")
    good_store.save({j.id: j for j in (_job("bad", next_run_at=10), _job("good", next_run_at=20))})
    calls: list[str] = []

    async def dispatch(job: ScheduledResearchJob) -> None:
        calls.append(job.id)

    async def scenario() -> None:
        executor = ScheduledResearchExecutor(store, dispatch)
        await executor.tick(100)

    asyncio.run(scenario())

    # "bad" exploded on its mark-RUNNING write; "good" must still have run.
    assert calls == ["good"]
    saved = good_store.get("good")
    assert saved is not None
    assert saved.status == JobStatus.COMPLETED


def test_job_with_invalid_persisted_schedule_fails_visibly_once(tmp_path: Path) -> None:
    # A 16-digit interval was accepted by an earlier grammar; it must surface
    # as a failed job rather than retry forever.
    store = _store(tmp_path)
    store.save({"legacy": _job("legacy", schedule="9" * 16, next_run_at=10)})
    calls: list[str] = []

    async def dispatch(job: ScheduledResearchJob) -> None:
        calls.append(job.id)

    async def scenario() -> None:
        executor = ScheduledResearchExecutor(store, dispatch)
        await executor.tick(100)
        await executor.tick(200)

    asyncio.run(scenario())

    saved = store.get("legacy")
    assert saved is not None
    assert calls == []  # never dispatched
    assert saved.status == JobStatus.FAILED
    assert saved.failure_kind == "schedule"
    assert "interval is too large" in (saved.last_error or "")
    assert saved.last_run_at == 100  # second tick left it alone


def test_lifecycle_writes_survive_a_schedule_the_grammar_now_rejects(tmp_path: Path) -> None:
    store = _store(tmp_path)
    job = _job("legacy", schedule="9" * 16, next_run_at=10)
    store.save({job.id: job})

    with pytest.raises(ValueError):
        store.upsert(job)  # creation-style write still validates

    job.status = JobStatus.FAILED
    store.upsert(job, validate=False)  # lifecycle write lands
    saved = store.get("legacy")
    assert saved is not None
    assert saved.status == JobStatus.FAILED
