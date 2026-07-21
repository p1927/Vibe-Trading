"""Dedicated thread pool + single-flight coalescing for slow prediction analytics."""

from __future__ import annotations

import threading
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Callable, TypeVar

T = TypeVar("T")

_POOL = ThreadPoolExecutor(max_workers=2, thread_name_prefix="pred-heavy")
_lock = threading.Lock()
_inflight: dict[str, Future] = {}


def run_single_flight(key: str, fn: Callable[[], T]) -> T:
    """Run *fn* once per *key*; concurrent callers wait on the same result."""
    with _lock:
        existing = _inflight.get(key)
        if existing is not None and not existing.done():
            future = existing
        else:
            future = _POOL.submit(fn)
            _inflight[key] = future
    try:
        return future.result()
    finally:
        with _lock:
            if _inflight.get(key) is future and future.done():
                _inflight.pop(key, None)
