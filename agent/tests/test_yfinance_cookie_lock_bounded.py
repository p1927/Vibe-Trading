"""Tests for the bounded-acquire patch on yfinance's shared YfData._cookie_lock.

See 2026-08-26-yfinance-singleton-cookie-lock-unbounded-process-wide-starvation.
"""

from __future__ import annotations

import threading
import time

import pytest

from backtest.loaders import yfinance_loader as loader


def test_patch_installs_bounded_lock_on_singleton() -> None:
    import yfinance.data as yf_data

    instance = yf_data.YfData()
    assert isinstance(instance._cookie_lock, loader._BoundedLock)


def test_bounded_lock_supports_context_manager_when_uncontended() -> None:
    lock = loader._BoundedLock(default_timeout=1)
    with lock:
        assert lock.locked()
    assert not lock.locked()


def test_bounded_lock_raises_timeout_when_another_thread_holds_it() -> None:
    lock = loader._BoundedLock(default_timeout=0.2)
    lock.acquire()
    try:
        errors = []

        def _contend() -> None:
            try:
                lock.acquire()
            except TimeoutError as exc:
                errors.append(exc)

        thread = threading.Thread(target=_contend, daemon=True)
        thread.start()
        thread.join(timeout=2)
        assert not thread.is_alive()
        assert len(errors) == 1
        assert "not acquired within" in str(errors[0])
    finally:
        lock.release()


def test_bounded_lock_does_not_block_a_second_wedged_caller_forever() -> None:
    """A holder that never releases must not wedge every other acquirer forever."""
    lock = loader._BoundedLock(default_timeout=0.2)
    lock.acquire()  # simulates a thread stuck forever inside the bootstrap

    start = time.monotonic()
    with pytest.raises(TimeoutError):
        lock.acquire()
    elapsed = time.monotonic() - start

    assert elapsed < 2  # bounded, not an indefinite hang


def test_explicit_timeout_argument_still_respected() -> None:
    lock = loader._BoundedLock(default_timeout=5)
    lock.acquire()
    try:
        start = time.monotonic()
        acquired = lock.acquire(blocking=True, timeout=0.1)
        elapsed = time.monotonic() - start
        assert acquired is False
        assert elapsed < 1
    finally:
        lock.release()
