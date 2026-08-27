"""Hypothesis + real-thread DST-lite harness over ``prediction_heavy_pool.py``.

Filed as `.claude/backlog/items/2026-08-28-dst-prediction-heavy-pool.md`.
``run_single_flight`` is a single-flight coalescing lock (``threading.Lock`` +
a shared ``ThreadPoolExecutor``) gating slow prediction-analytics calls: N
concurrent callers on the *same* key must share one in-flight computation;
callers on *different* keys must not block each other. Unlike the recorder/
scheduler DST-lite domains, this module drives real OS threads rather than
logical time — there is no clock to simulate, the thing under test *is* the
real concurrency — so this harness uses ``threading.Barrier``/``Event`` to
force adversarial interleavings deterministically instead of
``testing/dst/time_control.SimClock``.

Four invariants, one per test:
1. **Coalescing** — N truly-concurrent callers on the same key produce
   exactly one execution of the underlying function, and every caller
   receives that one result (the positive control: coalescing must actually
   happen, not just "never mis-coalesce").
2. **Isolation** — callers on distinct keys run independently; a call on key
   B completes without waiting for an in-flight call on key A to finish.
3. **No permanent caching** — once a key's in-flight future completes, the
   key is freed; a later call with the same key re-invokes the function
   rather than replaying a stale cached result.
4. **Exception fan-out and recovery** — if the shared function raises, every
   coalesced caller (not just the one that happened to submit the work)
   receives the exception, and the key is left in a clean state so the next
   call for that key runs fresh rather than being permanently wedged.
"""

from __future__ import annotations

import threading
import uuid
from typing import Any

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from src.trade import prediction_heavy_pool
from src.trade.prediction_heavy_pool import run_single_flight

pytestmark = pytest.mark.prediction_pool_dst

_SETTINGS = settings(
    deadline=None,
    max_examples=25,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)


def _unique_key() -> str:
    return f"dst-key-{uuid.uuid4()}"


def _run_threads(targets: list[threading.Thread], timeout: float = 10.0) -> None:
    for t in targets:
        t.start()
    for t in targets:
        t.join(timeout=timeout)
        assert not t.is_alive(), "thread failed to complete within timeout — likely a deadlock"


# ---------------------------------------------------------------------------
# 1. Coalescing: N concurrent same-key callers -> exactly one execution,
#    every caller gets that one result.
# ---------------------------------------------------------------------------


@given(num_callers=st.integers(min_value=2, max_value=8))
@_SETTINGS
def test_concurrent_same_key_callers_coalesce_to_one_execution(num_callers: int) -> None:
    key = _unique_key()
    call_count = 0
    call_count_lock = threading.Lock()
    gate = threading.Event()
    start_barrier = threading.Barrier(num_callers)
    results: list[Any] = [None] * num_callers
    errors: list[BaseException | None] = [None] * num_callers

    sentinel = object()

    def fn() -> object:
        nonlocal call_count
        with call_count_lock:
            call_count += 1
        # Block until every caller has had a chance to reach run_single_flight,
        # maximizing the odds a buggy implementation would double-submit.
        gate.wait(timeout=5.0)
        return sentinel

    def caller(idx: int) -> None:
        start_barrier.wait(timeout=5.0)
        try:
            results[idx] = run_single_flight(key, fn)
        except BaseException as exc:  # noqa: BLE001 - captured for assertion, not swallowed
            errors[idx] = exc

    threads = [threading.Thread(target=caller, args=(i,)) for i in range(num_callers)]
    for t in threads:
        t.start()

    # Give every thread a moment to queue up at (or past) the barrier before
    # releasing the shared function, then let it complete.
    threading.Event().wait(0.05)
    gate.set()
    for t in threads:
        t.join(timeout=10.0)
        assert not t.is_alive()

    assert all(e is None for e in errors), f"unexpected errors: {errors}"
    assert call_count == 1, "coalescing failed: fn ran more than once for one key"
    assert all(r is sentinel for r in results), "not every coalesced caller got the shared result"
    assert key not in prediction_heavy_pool._inflight, "key not cleaned up after completion"


# ---------------------------------------------------------------------------
# 2. Isolation: distinct keys never block each other.
# ---------------------------------------------------------------------------


def test_distinct_keys_do_not_block_each_other() -> None:
    key_a = _unique_key()
    key_b = _unique_key()

    a_started = threading.Event()
    a_release = threading.Event()
    b_done = threading.Event()

    def fn_a() -> str:
        a_started.set()
        a_release.wait(timeout=5.0)
        return "a"

    def fn_b() -> str:
        return "b"

    result_a: list[Any] = [None]

    def caller_a() -> None:
        result_a[0] = run_single_flight(key_a, fn_a)

    t_a = threading.Thread(target=caller_a)
    t_a.start()
    assert a_started.wait(timeout=5.0), "key A's call never started"

    # Key A is now blocked mid-flight. A call on a different key must not
    # wait on it.
    result_b = run_single_flight(key_b, fn_b)
    b_done.set()
    assert result_b == "b"
    assert not a_release.is_set()  # sanity: A really was still blocked while B ran

    a_release.set()
    t_a.join(timeout=5.0)
    assert not t_a.is_alive()
    assert result_a[0] == "a"


# ---------------------------------------------------------------------------
# 3. No permanent caching: a key is re-run fresh once its in-flight future
#    completes.
# ---------------------------------------------------------------------------


def test_key_reruns_fn_after_prior_call_completes() -> None:
    key = _unique_key()
    calls: list[int] = []

    def make_fn(value: int):
        def fn() -> int:
            calls.append(value)
            return value

        return fn

    first = run_single_flight(key, make_fn(1))
    second = run_single_flight(key, make_fn(2))

    assert first == 1
    assert second == 2
    assert calls == [1, 2], "second call reused a stale cached result instead of re-running fn"
    assert key not in prediction_heavy_pool._inflight


# ---------------------------------------------------------------------------
# 4. Exceptions fan out to every coalesced caller, and the key recovers
#    cleanly for the next call.
# ---------------------------------------------------------------------------


class _BoomError(RuntimeError):
    pass


@given(num_callers=st.integers(min_value=2, max_value=6))
@_SETTINGS
def test_exception_propagates_to_all_coalesced_callers_and_key_recovers(
    num_callers: int,
) -> None:
    key = _unique_key()
    start_barrier = threading.Barrier(num_callers)
    gate = threading.Event()
    errors: list[BaseException | None] = [None] * num_callers
    results: list[Any] = [None] * num_callers

    def failing_fn() -> None:
        gate.wait(timeout=5.0)
        raise _BoomError("simulated analytics failure")

    def caller(idx: int) -> None:
        start_barrier.wait(timeout=5.0)
        try:
            results[idx] = run_single_flight(key, failing_fn)
        except BaseException as exc:  # noqa: BLE001
            errors[idx] = exc

    threads = [threading.Thread(target=caller, args=(i,)) for i in range(num_callers)]
    for t in threads:
        t.start()
    threading.Event().wait(0.05)
    gate.set()
    for t in threads:
        t.join(timeout=10.0)
        assert not t.is_alive()

    assert all(r is None for r in results)
    assert all(isinstance(e, _BoomError) for e in errors), (
        f"not every coalesced caller received the exception: {errors}"
    )
    assert key not in prediction_heavy_pool._inflight, "failed key left the pool wedged"

    # The key must be usable again, not permanently poisoned by the earlier failure.
    recovered = run_single_flight(key, lambda: "recovered")
    assert recovered == "recovered"
