"""Event-loop stall watchdog: log the loop thread's stack when it stops answering.

Fork-only sidecar, started/stopped from ``src/api/lifecycle.py``.

Why: the Vibe API has twice stopped answering every request -- including ``/health``, which is
``async def`` and returns a constant -- for minutes at a time under sustained job-dispatch load,
at near-zero CPU, while ``llm_call_complete`` lines kept appearing in the log (see backlog item
2026-09-07-vibe-api-thread-pool-starvation-during-agent-turn). Those LLM calls run inside
``asyncio.to_thread`` dispatch workers, so their progress says nothing about the event loop
itself; an ``async def`` route that never answers means the loop thread is blocked on something
synchronous. Attaching ``py-spy`` needs root on macOS and someone watching at the right moment,
so this makes the process report its own blocked frame instead.

How: a daemon thread posts a no-op probe to the loop with ``call_soon_threadsafe`` every
``probe_interval_s``. If a probe is still unanswered after ``stall_threshold_s``, it logs one
WARNING carrying the loop thread's current stack, then one more when the loop recovers, with the
stall's length. It never touches the loop's work, so it cannot make a stall worse.

Liveness signal: while stalled the same thread also rewrites ``<runtime root>/loop_stall.json``
every probe interval (``stalled_since`` set, ``updated_at`` fresh) and clears ``stalled_since`` on
recovery. A wedged loop cannot answer ``/health``, so Trade's ``check_dev_ports.py`` reads this file
instead of asking the loop (backlog 2026-09-21-loop-stall-watchdog-no-health-signal).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import threading
import time
import traceback
from pathlib import Path
from typing import Any

from src.config.paths import get_runtime_root

logger = logging.getLogger(__name__)

_DEFAULT_PROBE_INTERVAL_S = 1.0
_DEFAULT_STALL_THRESHOLD_S = 10.0

_lock = threading.Lock()
_thread: threading.Thread | None = None
_stop = threading.Event()
_stats: dict[str, Any] = {"stall_count": 0, "max_stall_seconds": 0.0, "last_stall_at": None}


def loop_stall_stats() -> dict[str, Any]:
    """Counters for stalls seen since start (a copy; safe to read from any thread)."""
    with _lock:
        return dict(_stats)


STATUS_FILE = "loop_stall.json"


def _publish(stalled_since: float | None) -> None:
    """Write the stall state where an out-of-process checker can read it. Loud on failure."""
    path = Path(get_runtime_root()) / STATUS_FILE
    payload = {**loop_stall_stats(), "pid": os.getpid(), "stalled_since": stalled_since, "updated_at": time.time()}
    try:
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload))
        os.replace(tmp, path)
    except OSError:
        logger.warning("could not write %s", path, exc_info=True)


def _format_thread_stack(thread_id: int) -> str:
    frame = sys._current_frames().get(thread_id)
    if frame is None:
        return "  <loop thread has no live frame>"
    return "".join(traceback.format_stack(frame))


def _watch(
    loop: asyncio.AbstractEventLoop,
    loop_thread_id: int,
    probe_interval_s: float,
    stall_threshold_s: float,
) -> None:
    answered = threading.Event()
    while not _stop.is_set():
        if loop.is_closed():
            return
        answered.clear()
        sent = time.monotonic()
        try:
            loop.call_soon_threadsafe(answered.set)
        except RuntimeError:  # loop closed between the check and the post
            return
        if answered.wait(stall_threshold_s):
            if _stop.wait(probe_interval_s):
                return
            continue
        # Stalled: report once, with the frame the loop thread is stuck in.
        logger.warning(
            "event loop stalled: no response to a probe for %.1fs; loop thread stack:\n%s",
            time.monotonic() - sent,
            _format_thread_stack(loop_thread_id),
        )
        stalled_at = time.time() - (time.monotonic() - sent)
        _publish(stalled_at)
        while not answered.wait(probe_interval_s):
            if _stop.is_set() or loop.is_closed():
                return
            _publish(stalled_at)
        stalled_for = time.monotonic() - sent
        with _lock:
            _stats["stall_count"] += 1
            _stats["max_stall_seconds"] = max(_stats["max_stall_seconds"], round(stalled_for, 1))
            _stats["last_stall_at"] = time.time()
        _publish(None)
        logger.warning("event loop recovered after a %.1fs stall", stalled_for)


def start_loop_stall_watchdog(
    *,
    probe_interval_s: float = _DEFAULT_PROBE_INTERVAL_S,
    stall_threshold_s: float = _DEFAULT_STALL_THRESHOLD_S,
) -> None:
    """Start watching the running loop. Must be called from inside that loop. Idempotent."""
    global _thread
    loop = asyncio.get_running_loop()
    loop_thread_id = threading.get_ident()
    with _lock:
        if _thread is not None and _thread.is_alive():
            return
        _stop.clear()
        _thread = threading.Thread(
            target=_watch,
            args=(loop, loop_thread_id, probe_interval_s, stall_threshold_s),
            name="loop-stall-watchdog",
            daemon=True,
        )
        _thread.start()


def stop_loop_stall_watchdog(timeout: float = 2.0) -> None:
    """Stop the watchdog thread, if running."""
    global _thread
    _stop.set()
    with _lock:
        thread, _thread = _thread, None
    if thread is not None:
        thread.join(timeout=timeout)
