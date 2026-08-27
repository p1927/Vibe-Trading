"""Hypothesis + real-thread DST-lite harness over ``job_watchdog.py``.

Filed as `.claude/backlog/items/2026-08-28-dst-job-watchdog.md`. `job_watchdog.py` runs one
shared background thread that hydrates and reconciles three separate file-backed job stores
(``index_prediction_run_jobs``, ``recording_jobs``, ``external_predictions_run_jobs``) on a
wall-clock interval — its own docstring names the exact failure mode it exists to prevent: "a
crashed worker for a job nobody is polling ... would sit reporting 'running' forever."

Note on approach: unlike `session_recorder.py`, this module's loop blocks on
``threading.Event.wait(interval_seconds)``, not ``time.sleep``/``time.monotonic`` — there is no
clock call for `testing/dst/time_control.SimClock`/`patch_time()` to redirect, so this harness
does not use it (the original backlog Plan's SimClock suggestion doesn't apply once the actual
implementation is read). `reconcile_all_job_stores()` itself is a plain synchronous function
independent of the loop/thread, so the failure-isolation invariant is tested by calling it
directly; the idempotency invariant genuinely needs real threads racing on `start_job_watchdog`.

Two invariants:
1. **Failure isolation** — a scripted reconcile failure in any one job-store module never
   prevents the other two from being reconciled on the same call, regardless of which module
   (first/middle/last in iteration order) fails. Hypothesis varies which subset of the three
   modules raise and what each non-raising module returns.
2. **Start/stop idempotency under concurrency** — N concurrent `start_job_watchdog()` calls never
   leave more than one live watchdog thread running; `stop_job_watchdog()` actually terminates
   the thread (join succeeds) and a subsequent start creates a fresh one.
"""

from __future__ import annotations

import threading

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from src.trade import (
    external_predictions_run_jobs,
    index_prediction_run_jobs,
    job_watchdog,
    recording_jobs,
)

pytestmark = pytest.mark.job_watchdog_dst

_SETTINGS = settings(
    deadline=None,
    max_examples=25,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)

_MODULES = (index_prediction_run_jobs, recording_jobs, external_predictions_run_jobs)


class _Boom(RuntimeError):
    pass


def _patch_reconcile(monkeypatch, module, *, raises: bool, count: int) -> None:
    if raises:

        def fn() -> int:
            raise _Boom(f"scripted failure in {module.__name__}")

    else:

        def fn() -> int:
            return count

    monkeypatch.setattr(module, "reconcile_all_active_jobs", fn)


@given(
    raise_flags=st.lists(st.booleans(), min_size=3, max_size=3),
    counts=st.lists(st.integers(min_value=0, max_value=5), min_size=3, max_size=3),
)
@_SETTINGS
def test_reconcile_isolates_per_store_failures(monkeypatch, raise_flags, counts) -> None:
    for module, raises, count in zip(_MODULES, raise_flags, counts):
        _patch_reconcile(monkeypatch, module, raises=raises, count=count)

    total = job_watchdog.reconcile_all_job_stores()

    expected = sum(c for flag, c in zip(raise_flags, counts) if not flag)
    assert total == expected, (
        f"one store's failure affected another store's reconciliation: "
        f"raise_flags={raise_flags} counts={counts} got total={total} expected={expected}"
    )


def test_reconcile_continues_past_a_first_module_failure(monkeypatch) -> None:
    """Positive control: the *first* module in iteration order failing must not skip the rest."""
    _patch_reconcile(monkeypatch, index_prediction_run_jobs, raises=True, count=0)
    _patch_reconcile(monkeypatch, recording_jobs, raises=False, count=2)
    _patch_reconcile(monkeypatch, external_predictions_run_jobs, raises=False, count=3)

    total = job_watchdog.reconcile_all_job_stores()

    assert total == 5


def test_watchdog_start_stop_idempotent_under_concurrent_start(monkeypatch) -> None:
    monkeypatch.setattr(job_watchdog, "_hydrate_all", lambda: None)
    monkeypatch.setattr(job_watchdog, "reconcile_all_job_stores", lambda: 0)

    job_watchdog._watchdog_stop.clear()
    job_watchdog._watchdog_thread = None
    try:
        num_callers = 8
        start_barrier = threading.Barrier(num_callers)

        def caller() -> None:
            start_barrier.wait(timeout=5.0)
            job_watchdog.start_job_watchdog(interval_seconds=0.02)

        threads = [threading.Thread(target=caller) for _ in range(num_callers)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5.0)
            assert not t.is_alive()

        alive_watchdog_threads = [
            t for t in threading.enumerate() if t.name == "job-watchdog" and t.is_alive()
        ]
        assert len(alive_watchdog_threads) == 1, (
            f"concurrent start_job_watchdog calls left {len(alive_watchdog_threads)} live "
            "watchdog threads, expected exactly 1"
        )

        job_watchdog.stop_job_watchdog(timeout=5.0)
        assert job_watchdog._watchdog_thread is None
        alive_after_stop = [
            t for t in threading.enumerate() if t.name == "job-watchdog" and t.is_alive()
        ]
        assert alive_after_stop == []

        job_watchdog.start_job_watchdog(interval_seconds=0.02)
        fresh_threads = [
            t for t in threading.enumerate() if t.name == "job-watchdog" and t.is_alive()
        ]
        assert len(fresh_threads) == 1
    finally:
        job_watchdog.stop_job_watchdog(timeout=5.0)
