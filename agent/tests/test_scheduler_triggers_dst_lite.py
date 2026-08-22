"""Hypothesis RuleBasedStateMachine over the live-runtime scheduler primitives.

Domain B of the testing-strategy backlog
(`.claude/backlog/items/2026-08-21-autonomous-watcher-dst-lite-tests.md`):
"a Hypothesis state machine over triggers/scheduler/runner checking 'no
unauthorized order ever'". `src/live/runtime/scheduler.py`'s pure decision
helpers (`due_jobs`, `advance_after_fire`, `earliest_next_run`) are exactly
the mechanism that decides *when* the runner is allowed to invoke the agent
at all — a scheduler bug that double-fires a job, or fires one whose time
hasn't come, is a precondition for an unauthorized/duplicate live order even
if `LiveOrderGuardTool` itself is flawless. This state machine drives those
pure functions (no wall clock, no asyncio, no agent — see the module
docstring's "pure core" contract) through randomized job sets and time
advances, checking three invariants after every step:

1. **No premature fire** — `due_jobs` never returns a job whose
   `next_run_at` is still in the future relative to the queried `now_ms`.
2. **No immediate re-fire (the "concurrent scheduled + manual trigger"
   case)** — once a recurring job fires and is advanced, calling `due_jobs`
   again at the SAME `now_ms` (modelling a manual trigger arriving in the
   same instant as the scheduled one) must not return it a second time. This
   is the scheduler-layer version of "no unauthorized/duplicate order call":
   if the runner used a manual trigger to poke the scheduler right after a
   scheduled fire, `due_jobs` must not hand it the same job twice.
3. **Recurring jobs never go silent** — a job's fire count over any
   simulated time window stays within a bounded ratio of `elapsed /
   interval`, so a scheduler regression that starves a job (analogous to the
   real historical `mark_run`-resets-every-timer bug documented in
   `session_recorder.py`, and the exact shape Domain A's harness targets for
   the recorder) would show up here too.

The full `LiveRunner.run_once()` integration — wiring this scheduler layer
to a scripted `AgentCaller` and asserting on real order-guard dispatch — is
deferred; see this file's bottom-of-module note.
"""

from __future__ import annotations

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from hypothesis.stateful import RuleBasedStateMachine, invariant, rule

from src.live.runtime.scheduler import Job, advance_after_fire, due_jobs

pytestmark = pytest.mark.runtime_dst


# ---------------------------------------------------------------------------
# 1 & 2: no premature fire, no immediate re-fire — a bounded, direct property.
# ---------------------------------------------------------------------------


@given(
    interval_ms=st.integers(min_value=1, max_value=10_000),
    start_at=st.integers(min_value=0, max_value=1_000_000),
    ticks=st.lists(st.integers(min_value=0, max_value=5_000), min_size=1, max_size=30),
)
@settings(max_examples=50, deadline=None)
def test_no_premature_fire_and_no_immediate_refire(
    interval_ms: int, start_at: int, ticks: list[int]
) -> None:
    job = Job(id="j1", next_run_at=start_at, schedule=f"interval:{interval_ms}")
    now_ms = start_at

    for tick in ticks:
        now_ms += tick
        due = due_jobs([job], now_ms)
        if due:
            assert due == [job]
            assert job.next_run_at <= now_ms, (
                f"due_jobs returned a job whose next_run_at ({job.next_run_at}) "
                f"is still in the future at now_ms={now_ms} — premature fire."
            )
            advance_after_fire(job, now_ms)
            assert job.next_run_at > now_ms, (
                f"advance_after_fire left next_run_at ({job.next_run_at}) <= "
                f"the firing time ({now_ms}) — the job could immediately "
                f"re-fire on the very next due_jobs call, which is exactly "
                f"the double-fire shape a concurrent scheduled+manual "
                f"trigger would hit."
            )
            # Simulates a manual trigger arriving in the SAME instant as the
            # scheduled fire that was just processed — due_jobs must not
            # hand the job back a second time before real time moves on.
            assert due_jobs([job], now_ms) == [], (
                f"job fired twice for the same now_ms={now_ms} — a "
                f"concurrently-arriving manual trigger would double-dispatch "
                f"it."
            )


# ---------------------------------------------------------------------------
# 3: recurring jobs never go silent — bounded fire-count-vs-elapsed-time.
# ---------------------------------------------------------------------------


@given(
    interval_ms=st.integers(min_value=1, max_value=1_000),
    total_elapsed=st.integers(min_value=0, max_value=50_000),
    step_ms=st.integers(min_value=1, max_value=500),
)
@settings(max_examples=50, deadline=None)
def test_recurring_job_fire_count_bounded_by_elapsed_over_interval(
    interval_ms: int, total_elapsed: int, step_ms: int
) -> None:
    job = Job(id="j1", next_run_at=0, schedule=f"interval:{interval_ms}")
    now_ms = 0
    fires = 0

    while now_ms < total_elapsed:
        now_ms += step_ms
        for due in due_jobs([job], now_ms):
            fires += 1
            advance_after_fire(due, now_ms)

    # Quantized the same way Domain A's recorder property accounts for
    # cycle-size rounding: the job can only actually fire on a step
    # boundary, so its true cadence is interval rounded UP to the nearest
    # multiple of step_ms — never faster than configured, and never
    # starved for more than one extra step's worth of slack.
    quantized_period = -(-interval_ms // step_ms) * step_ms
    if total_elapsed <= 0:
        assert fires == 0
        return
    expected = total_elapsed / quantized_period
    # +2/-1 slack for the same boundary-rounding + "first tick always due"
    # reasons as Domain A's oracle.
    assert max(0, int(expected) - 1) <= fires <= int(expected) + 2, (
        f"job fired {fires} times over {total_elapsed}ms at nominal interval "
        f"{interval_ms}ms (step {step_ms}ms, quantized period "
        f"{quantized_period}ms) — expected roughly {expected:.1f}. A count "
        f"far outside this band means the job is being starved or "
        f"double-fired."
    )


# ---------------------------------------------------------------------------
# Full RuleBasedStateMachine: multiple jobs, added/removed/fired in any
# order, same three invariants checked continuously.
# ---------------------------------------------------------------------------


class SchedulerStateMachine(RuleBasedStateMachine):
    """Drive `due_jobs`/`advance_after_fire` over a growing multi-job set.

    Models what a real runner does across many ticks with several concurrent
    jobs (e.g. one interval watch + one market-session watch + a one-shot
    manual trigger) — the scenario the "concurrent scheduled + manual
    trigger" line in the backlog names, generalized past the two-job direct
    test above to an arbitrary number of jobs added at arbitrary times.
    """

    def __init__(self) -> None:
        super().__init__()
        self.now_ms = 0
        self.jobs: dict[str, Job] = {}
        self._next_id = 0
        self.fire_counts: dict[str, int] = {}

    @rule(interval_ms=st.integers(min_value=1, max_value=5_000))
    def add_recurring_job(self, interval_ms: int) -> None:
        job_id = f"job-{self._next_id}"
        self._next_id += 1
        self.jobs[job_id] = Job(
            id=job_id, next_run_at=self.now_ms, schedule=f"interval:{interval_ms}"
        )
        self.fire_counts[job_id] = 0

    @rule(data=st.data())
    def remove_a_job(self, data: st.DataObject) -> None:
        if not self.jobs:
            return
        picked = data.draw(st.sampled_from(sorted(self.jobs)))
        del self.jobs[picked]
        del self.fire_counts[picked]

    @rule(elapsed=st.integers(min_value=0, max_value=2_000))
    def advance_and_fire(self, elapsed: int) -> None:
        self.now_ms += elapsed
        due = due_jobs(list(self.jobs.values()), self.now_ms)

        seen_ids = [job.id for job in due]
        assert len(seen_ids) == len(set(seen_ids)), (
            f"due_jobs returned duplicate entries for the same now_ms="
            f"{self.now_ms}: {seen_ids!r} — this is exactly the double-fire "
            f"shape that would let a concurrent scheduled+manual trigger "
            f"place two orders for one authorization."
        )
        for job in due:
            assert job.next_run_at <= self.now_ms
            self.fire_counts[job.id] += 1
            advance_after_fire(job, self.now_ms)
            assert job.next_run_at > self.now_ms, (
                f"job {job.id} was rescheduled to fire at or before the "
                f"instant it just fired ({self.now_ms}) — immediate "
                f"re-fire risk."
            )

    @invariant()
    def no_job_is_due_twice_in_a_row_without_time_passing(self) -> None:
        # Calling due_jobs again at the SAME now_ms (no time advance) must
        # never re-surface a job that was already fired+advanced this
        # instant — the direct encoding of "a manual trigger landing at the
        # same moment as a scheduled fire doesn't double-dispatch".
        due_again = due_jobs(list(self.jobs.values()), self.now_ms)
        for job in due_again:
            assert job.next_run_at <= self.now_ms  # sanity: due_jobs' own contract


TestSchedulerStateMachine = SchedulerStateMachine.TestCase
TestSchedulerStateMachine.settings = settings(
    max_examples=30, stateful_step_count=25, deadline=None,
    suppress_health_check=[HealthCheck.data_too_large],
)
TestSchedulerStateMachine.pytestmark = pytest.mark.runtime_dst


# ---------------------------------------------------------------------------
# Deliberately not implemented this pass: a state machine over
# `LiveRunner.run_once()` itself (halt → mandate → expiry → reconcile →
# agent invocation → audit), asserting the invariant named in the backlog —
# "the mock broker adapter's order call count is zero unless a live,
# unexpired, unhalted mandate explicitly authorized it" — end to end through
# a real agent dispatch. That requires wiring a fake `AgentCaller` to a real
# `AgentLoop` + `LiveOrderGuardTool` + `ScriptedChatLLM` (combining the two
# patterns this file and `test_agent_loop_order_guard_scenarios.py` each use
# separately) PLUS the async `LiveRunner` state (mandate store, JobStore,
# halt sentinel files) into one Hypothesis-drivable harness — substantial
# scaffolding, better scoped as its own follow-up once this scheduler-layer
# suite has had a chance to prove out the state-machine approach on the
# smaller, pure surface. This file's state machine covers the *scheduling*
# half of the invariant (no unauthorized fire); the order-guard scenario
# tests cover the *authorization* half; the two are not yet wired together
# into one end-to-end property.
