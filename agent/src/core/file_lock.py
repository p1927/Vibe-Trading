"""THE exclusive lock for a load, mutate, save sequence on one stored file (D306): a thread lock plus a ``flock`` on a
sidecar ``<name>.lock`` file, re-entrant per thread and per path, shared across store instances (a tool builds a fresh store
on every call) and across processes (the API, the CLI and a child all take the same ``flock``). Without it two
writers each load the same snapshot and the second ``save`` silently erases the first one's change.

Stdlib only. Lifted unchanged from ``ScheduledResearchJobStore.transaction``, which now calls it.
"""
from __future__ import annotations

import functools
import os
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator, TypeVar

try:
    import fcntl
except ImportError:  # Windows: the thread lock still holds within a process
    fcntl = None  # type: ignore[assignment]

_HELD = threading.local()  # per thread: path -> re-entry depth
_THREAD_LOCKS: dict[str, threading.Lock] = {}
_THREAD_LOCKS_GUARD = threading.Lock()


@contextmanager
def file_lock(path: Path) -> Iterator[None]:
    """Hold ``path`` exclusively. Re-entrant per thread and per path."""
    key = str(path)
    depths: dict[str, int] = _HELD.__dict__.setdefault("depths", {})
    if depths.get(key):
        depths[key] += 1
        try:
            yield
        finally:
            depths[key] -= 1
        return
    with _THREAD_LOCKS_GUARD:
        thread_lock = _THREAD_LOCKS.setdefault(key, threading.Lock())
    with thread_lock:
        fd = -1
        if fcntl is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(path.with_name(path.name + ".lock"), os.O_RDWR | os.O_CREAT, 0o600)
            fcntl.flock(fd, fcntl.LOCK_EX)
        depths[key] = 1
        try:
            yield
        finally:
            depths[key] = 0
            if fd >= 0:
                os.close(fd)  # closing releases the flock


F = TypeVar("F", bound=Callable[..., Any])


def locked(method: F) -> F:
    """Run a store method (one that loads, mutates and saves ``self.path``) under ``file_lock(self.path)``."""

    @functools.wraps(method)
    def wrapper(self: Any, *args: Any, **kwargs: Any) -> Any:
        with file_lock(self.path):
            return method(self, *args, **kwargs)

    return wrapper  # type: ignore[return-value]
