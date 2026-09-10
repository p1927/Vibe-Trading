"""D11: shortest-expected-runtime-first admission into scheduler dispatch slots.

docs/DECISIONS.md D11 and
.claude/backlog/items/2026-09-10-full-ingests-starve-tight-ingests-same-type-cap.md:
two 90-minute ``-full`` ingests held both ``hub_news_ingest`` slots while a due ``*/15``
``-tight`` waited 48+ minutes, and ``tick()`` could not even see it because it awaited
its whole gathered set. These tests pin the three mechanics — (a) a tick never blocks on
a dispatch and never double-dispatches, (b) shortest expected runtime first with the last
per-type slot reserved from long jobs, (c) ageing — without naming any job variant: "long"
here is purely a runtime history.
"""

from __future__ import annotations

import asyncio
from collections import Counter, defaultdict
from pathlib import Path

import pytest
from src.scheduled_research.dispatch_admission import (
    DURATION_HISTORY_CONFIG_KEY,
    DURATION_HISTORY_LEN,
    expected_runtime_ms,
    long_runtime_threshold_ms,
    record_dispatch_duration,
)
from src.scheduled_research.executor import ScheduledResearchExecutor
from src.scheduled_research.models import JobStatus, ScheduledResearchJob
from src.scheduled_research.store import ScheduledResearchJobStore

MIN = 60 * 1000
HOUR = 60 * MIN
NOT_DUE = 10**12


@pytest.fixture(autouse=True)
def _release_tier_with_ingest_cap_2(monkeypatch: pytest.MonkeyPatch) -> None:
    # hub_news_ingest is a collection job: only the release tier dispatches it.
    monkeypatch.setenv("STACK_PROFILE", "release")
    monkeypatch.setenv("SCHEDULED_RESEARCH_DISPATCH_CONCURRENCY_HUB_NEWS_INGEST", "2")


def _store(tmp_path: Path) -> ScheduledResearchJobStore:
    return ScheduledResearchJobStore(path=tmp_path / "jobs.json")


def _ingest(
    job_id: str,
    runtime_ms: int,
    *,
    next_run_at: int = 10,
    schedule: str = str(HOUR),
    created_at: int = 0,
) -> ScheduledResearchJob:
    """A hub_news_ingest job whose measured history says it takes `runtime_ms`."""
    return ScheduledResearchJob(
        id=job_id,
        prompt=f"prompt for {job_id}",
        schedule=schedule,
        next_run_at=next_run_at,
        status=JobStatus.PENDING,
        created_at=created_at,
        config={"job_type": "hub_news_ingest", DURATION_HISTORY_CONFIG_KEY: [runtime_ms]},
    )


class _Gated:
    """Dispatch that blocks each job until its gate is opened; records start order."""

    def __init__(self) -> None:
        self.gates: dict[str, asyncio.Event] = defaultdict(asyncio.Event)
        self.started: list[str] = []
        self.calls: Counter[str] = Counter()
        self.all_open = False

    async def __call__(self, job: ScheduledResearchJob) -> None:
        self.started.append(job.id)
        self.calls[job.id] += 1
        if not self.all_open:
            await self.gates[job.id].wait()

    def release_all(self) -> None:
        """Open every gate, including those of jobs admitted after this call."""
        self.all_open = True
        for gate in self.gates.values():
            gate.set()


async def _settle() -> None:
    await asyncio.sleep(0.02)


# ---------------------------------------------------------------------------------------
# (a) + (b): the measured starvation scenario
# ---------------------------------------------------------------------------------------


def test_short_job_that_becomes_due_while_a_long_one_runs_takes_the_reserved_slot(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    store.upsert(_ingest("long-a", 90 * MIN, created_at=1))
    store.upsert(_ingest("long-b", 90 * MIN, created_at=2))
    store.upsert(_ingest("short-1", 10 * MIN, next_run_at=NOT_DUE))
    store.upsert(_ingest("short-2", 10 * MIN, next_run_at=NOT_DUE))
    store.upsert(_ingest("short-3", 10 * MIN, next_run_at=NOT_DUE))
    dispatch = _Gated()

    async def scenario() -> None:
        executor = ScheduledResearchExecutor(store, dispatch, dispatch_concurrency=4)
        pool = executor._dispatch_pool
        # (a) the background loop's tick returns while the dispatch it started still runs.
        await asyncio.wait_for(executor.tick(100, wait=False), timeout=1)
        await _settle()
        # (b) only ONE long job may run: the second would take the last ingest slot.
        assert dispatch.started == ["long-a"]
        assert pool.waiting_ids == ["long-b"]

        store.update_run_state("short-1", next_run_at=200)
        await asyncio.wait_for(executor.tick(300, wait=False), timeout=1)
        await _settle()
        # The short job got the reserved slot at once, not after long-a's 90 minutes.
        assert dispatch.started == ["long-a", "short-1"]
        assert pool.waiting_ids == ["long-b"]

        dispatch.gates["short-1"].set()
        await _settle()
        # A freed slot that is the LAST free one still does not go to a long job.
        assert "long-b" not in dispatch.started
        dispatch.gates["long-a"].set()
        await _settle()
        # With both slots free, the long job may take one.
        assert dispatch.started[-1] == "long-b"
        dispatch.release_all()
        await pool.join()

    asyncio.run(scenario())
    assert dict(dispatch.calls) == {"long-a": 1, "short-1": 1, "long-b": 1}
    for job_id in ("long-a", "short-1", "long-b"):
        assert store.get(job_id).status == JobStatus.COMPLETED


def test_short_job_waiting_behind_two_running_long_jobs_goes_before_a_waiting_long_one(
    tmp_path: Path,
) -> None:
    """Both slots held by long jobs (aged, so they were allowed both); a short job becomes
    due mid-run. The tick must still see it, and the first freed slot must go to it rather
    than to the long job that was waiting before it."""
    store = _store(tmp_path)
    # 1s cadence, due at 10, ticked at 5000: overdue by 5 cadences -> aged -> both slots.
    store.upsert(_ingest("long-a", 90 * MIN, schedule="1000", created_at=1))
    store.upsert(_ingest("long-b", 90 * MIN, schedule="1000", created_at=2))
    # Due, long, NOT aged (1h cadence, 1s overdue).
    store.upsert(_ingest("long-c", 90 * MIN, next_run_at=4000, created_at=3))
    store.upsert(_ingest("short-1", 10 * MIN, next_run_at=NOT_DUE))
    store.upsert(_ingest("short-2", 10 * MIN, next_run_at=NOT_DUE))
    store.upsert(_ingest("short-3", 10 * MIN, next_run_at=NOT_DUE))
    dispatch = _Gated()

    async def scenario() -> None:
        executor = ScheduledResearchExecutor(store, dispatch, dispatch_concurrency=4)
        pool = executor._dispatch_pool
        await asyncio.wait_for(executor.tick(5000, wait=False), timeout=1)
        await _settle()
        assert sorted(dispatch.started) == ["long-a", "long-b"]
        assert pool.waiting_ids == ["long-c"]

        store.update_run_state("short-1", next_run_at=5500)
        # Several ticks while both slots are held: none blocks, none re-queues anything.
        for now in (6000, 6500, 7000):
            await asyncio.wait_for(executor.tick(now, wait=False), timeout=1)
        await _settle()
        assert pool.waiting_ids == ["long-c", "short-1"]
        assert sorted(pool.running_ids) == ["long-a", "long-b"]

        dispatch.gates["long-a"].set()
        await _settle()
        # Shortest expected runtime first: short-1 beat long-c, which was due earlier.
        assert dispatch.started[2] == "short-1"
        assert "long-c" not in dispatch.started
        dispatch.release_all()
        await pool.join()

    asyncio.run(scenario())
    assert all(count == 1 for count in dispatch.calls.values()), dispatch.calls


# ---------------------------------------------------------------------------------------
# (b) ordering
# ---------------------------------------------------------------------------------------


def test_waiters_are_admitted_shortest_expected_runtime_first(tmp_path: Path) -> None:
    """Global cap 1, no type cap: order is by expected runtime, not by due time. A job with
    no history falls back to its `dispatch_timeout_ms` budget."""
    store = _store(tmp_path)

    def plain(job_id: str, next_run_at: int, config: dict) -> ScheduledResearchJob:
        return ScheduledResearchJob(
            id=job_id, prompt=job_id, schedule=str(HOUR), next_run_at=next_run_at,
            status=JobStatus.PENDING, created_at=0, config=config,
        )

    store.upsert(plain("slow", 1, {DURATION_HISTORY_CONFIG_KEY: [30_000]}))
    store.upsert(plain("medium", 2, {DURATION_HISTORY_CONFIG_KEY: [5_000]}))
    store.upsert(plain("budget-only", 3, {"dispatch_timeout_ms": 2_000}))
    order: list[str] = []

    async def dispatch(job: ScheduledResearchJob) -> None:
        order.append(job.id)

    async def scenario() -> None:
        executor = ScheduledResearchExecutor(store, dispatch, dispatch_concurrency=1)
        await executor.tick(100)

    asyncio.run(scenario())
    assert order == ["budget-only", "medium", "slow"]


# ---------------------------------------------------------------------------------------
# (c) ageing
# ---------------------------------------------------------------------------------------


def test_long_job_waiting_past_its_cadence_gets_the_reserved_slot_ahead_of_a_short_one(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    store.upsert(_ingest("short-1", 10 * MIN, created_at=1))
    store.upsert(_ingest("long-x", 90 * MIN, created_at=2))
    store.upsert(_ingest("short-2", 10 * MIN, next_run_at=NOT_DUE, created_at=3))
    dispatch = _Gated()

    async def scenario() -> None:
        executor = ScheduledResearchExecutor(store, dispatch, dispatch_concurrency=4)
        pool = executor._dispatch_pool
        await executor.tick(100, wait=False)
        await _settle()
        # short-1 took a slot; long-x may not take the last one while it is not aged.
        assert dispatch.started == ["short-1"]
        assert pool.waiting_ids == ["long-x"]

        # A short job becomes due too, and time passes one full cadence beyond long-x's slot.
        store.update_run_state("short-2", next_run_at=HOUR)
        await executor.tick(10 + HOUR + 1, wait=False)
        await _settle()
        # Aged: long-x takes the reserved slot, and ahead of the (non-aged) short job.
        assert dispatch.started == ["short-1", "long-x"]
        assert pool.waiting_ids == ["short-2"]
        dispatch.release_all()
        await _settle()
        await pool.join()

    asyncio.run(scenario())
    assert dispatch.calls == Counter({"short-1": 1, "long-x": 1, "short-2": 1})


# ---------------------------------------------------------------------------------------
# (a) no double dispatch
# ---------------------------------------------------------------------------------------


def test_repeated_and_overlapping_ticks_never_dispatch_a_job_twice(tmp_path: Path) -> None:
    store = _store(tmp_path)
    for i in range(3):
        store.upsert(_ingest(f"short-{i}", 10 * MIN, created_at=i))
    dispatch = _Gated()

    async def scenario() -> None:
        executor = ScheduledResearchExecutor(store, dispatch, dispatch_concurrency=4)
        pool = executor._dispatch_pool
        # Two ticks racing each other before any admitted task has even started (so the
        # store still says PENDING for everything): the pool alone must dedupe.
        await asyncio.gather(executor.tick(100, wait=False), executor.tick(100, wait=False))
        for now in (200, 300, 400, 500):
            await executor.tick(now, wait=False)
        await _settle()
        assert len(dispatch.started) == 2  # the type cap
        assert len(pool.waiting_ids) == 1
        dispatch.release_all()
        await _settle()
        await pool.join()

    asyncio.run(scenario())
    assert dispatch.calls == Counter({"short-0": 1, "short-1": 1, "short-2": 1})


def test_a_job_that_waited_for_a_slot_is_stamped_with_its_admission_time(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A long wait must not leave `last_run_at` at the offering tick's time, or the stale
    watchdog (measured from `last_run_at`) would recover the job mid-run."""
    store = _store(tmp_path)
    store.upsert(_ingest("first", 10 * MIN, created_at=1))
    store.upsert(_ingest("second", 10 * MIN, created_at=2))
    monkeypatch.setenv("SCHEDULED_RESEARCH_DISPATCH_CONCURRENCY_HUB_NEWS_INGEST", "1")
    dispatch = _Gated()

    async def scenario() -> None:
        executor = ScheduledResearchExecutor(store, dispatch, dispatch_concurrency=4)
        await executor.tick(100, wait=False)
        await _settle()
        await asyncio.sleep(0.2)  # "second" waits ~200ms for the single slot
        dispatch.gates["first"].set()
        await _settle()
        assert store.get("second").status == JobStatus.RUNNING
        assert store.get("second").last_run_at >= 100 + 150
        dispatch.release_all()
        await executor._dispatch_pool.join()

    asyncio.run(scenario())


# ---------------------------------------------------------------------------------------
# expected runtime and the "long" rule
# ---------------------------------------------------------------------------------------


def test_dispatch_duration_history_is_recorded_persisted_and_capped(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.upsert(
        ScheduledResearchJob(
            id="j", prompt="j", schedule="1000", next_run_at=10, status=JobStatus.PENDING, created_at=0
        )
    )

    async def dispatch(job: ScheduledResearchJob) -> None:
        await asyncio.sleep(0.03)

    async def scenario() -> None:
        executor = ScheduledResearchExecutor(store, dispatch, dispatch_concurrency=1)
        await executor.tick(100)

    asyncio.run(scenario())
    history = store.get("j").config[DURATION_HISTORY_CONFIG_KEY]
    assert len(history) == 1 and history[0] >= 25

    job = store.get("j")
    for ms in range(10):
        record_dispatch_duration(job, 1000 + ms)
    assert job.config[DURATION_HISTORY_CONFIG_KEY] == [1005, 1006, 1007, 1008, 1009]
    assert len(job.config[DURATION_HISTORY_CONFIG_KEY]) == DURATION_HISTORY_LEN


def test_expected_runtime_is_median_of_history_else_budget() -> None:
    job = ScheduledResearchJob(id="j", prompt="j", schedule="1000", config={"dispatch_timeout_ms": 7_000})
    assert expected_runtime_ms(job) == 7_000
    job.config[DURATION_HISTORY_CONFIG_KEY] = [100, 90_000, 200]  # one outlier timeout
    assert expected_runtime_ms(job) == 200


def test_long_threshold_is_relative_to_the_types_own_jobs_not_their_names() -> None:
    """Budget-only population shaped like today's hub_news_ingest set (one 90-min job per
    two 20-min ones): the 90-min job is long, the 20-min ones are not — decided from
    runtimes alone. A type whose jobs all cost the same has no long job at all."""

    def job(job_id: str, budget_ms: int, job_type: str = "t") -> ScheduledResearchJob:
        return ScheduledResearchJob(
            id=job_id, prompt=job_id, schedule="1000",
            config={"job_type": job_type, "dispatch_timeout_ms": budget_ms},
        )

    population = [job("a", 90 * MIN), job("b", 20 * MIN), job("c", 20 * MIN)]
    threshold = long_runtime_threshold_ms(population, "t")
    assert threshold is not None
    assert expected_runtime_ms(population[0]) > threshold
    assert expected_runtime_ms(population[1]) <= threshold

    uniform = [job(f"u{i}", 20 * MIN, "u") for i in range(4)]
    uniform_threshold = long_runtime_threshold_ms(uniform, "u")
    assert all(expected_runtime_ms(j) <= uniform_threshold for j in uniform)
    assert long_runtime_threshold_ms(population, "absent") is None
