"""Bounded per-job in-process log buffer feeding the Scheduler tab's live-log-tail.

Each of the 7 dispatch modules (index_jobs.py, options_jobs.py, trade_data_jobs.py,
hub_calibration_jobs.py, capture_jobs.py, autonomous_agent_jobs.py,
recording_wake_jobs.py) calls :func:`append_log` around its dispatch, and
``GET /scheduled-runs/{job_id}/stream`` (scheduled_routes.py) polls
:func:`get_logs_since` to emit SSE ``log`` events — the same replay-then-poll
shape as ``_index_prediction_run_event_stream`` (trade_routes.py), just backed
by an in-memory buffer instead of a persisted job-record field, since these
runs are typically short-lived and don't need durable log history.

Entries carry a monotonically increasing ``seq`` (not a deque index) so a
caller polling with ``since_seq`` sees the right tail even after the deque's
``maxlen`` has evicted older entries — an index would silently shift under
eviction, a seq comparison won't.
"""

from __future__ import annotations

import asyncio
import threading
import time
from collections import deque
from typing import Any, Awaitable, Callable, Deque, Dict, List

_MAX_LOGS_PER_JOB = 500

_LOCK = threading.Lock()
_BUFFERS: Dict[str, Deque[Dict[str, Any]]] = {}
_SEQ_COUNTERS: Dict[str, int] = {}


def append_log(job_id: str, message: str) -> None:
    """Append one log line for ``job_id``, evicting the oldest once past 500."""
    with _LOCK:
        seq = _SEQ_COUNTERS.get(job_id, 0) + 1
        _SEQ_COUNTERS[job_id] = seq
        buf = _BUFFERS.setdefault(job_id, deque(maxlen=_MAX_LOGS_PER_JOB))
        buf.append({"seq": seq, "at": int(time.time() * 1000), "message": message})


def get_logs_since(job_id: str, since_seq: int = 0) -> List[Dict[str, Any]]:
    """Return log entries for ``job_id`` with ``seq > since_seq``, oldest first."""
    with _LOCK:
        buf = _BUFFERS.get(job_id)
        if buf is None:
            return []
        return [entry for entry in buf if entry["seq"] > since_seq]


def clear_logs(job_id: str) -> None:
    """Drop ``job_id``'s buffer and reset its sequence counter.

    Called when a new run starts so a fresh SSE connection never replays a
    previous run's stale tail.
    """
    with _LOCK:
        _BUFFERS.pop(job_id, None)
        _SEQ_COUNTERS.pop(job_id, None)


async def run_logged(
    job: Any,
    dispatch: Callable[[Any], Any] | Callable[[Any], Awaitable[Any]],
    *,
    run_in_thread: bool = True,
) -> None:
    """Wrap one job dispatch with start/completed/failed log lines.

    Each of the 7 scheduled-research dispatch modules calls this from its
    async entry point instead of duplicating start/complete/fail bookkeeping
    at every one of its internal ``if job_type == ...`` branches — coarser
    than per-stage logging, but the same lines the Scheduler tab's
    live-log-tail needs, without ~50 near-identical edits across 7 files.

    ``dispatch`` is called with ``job``: off the event loop via
    ``asyncio.to_thread`` when ``run_in_thread`` (the sync-dispatch-plus-thin-
    async-shell shape most modules use — pass the module's
    ``dispatch_*_job_sync``), awaited directly otherwise (for a module like
    ``autonomous_agent_jobs.py`` whose dispatch logic is already async).
    Re-raises whatever ``dispatch`` raises, after logging it, so callers'
    existing failure handling is unaffected.
    """
    clear_logs(job.id)
    job_type = str((getattr(job, "config", None) or {}).get("job_type") or "")
    append_log(job.id, f"starting{f' ({job_type})' if job_type else ''}")
    try:
        if run_in_thread:
            await asyncio.to_thread(dispatch, job)
        else:
            await dispatch(job)
    except Exception as exc:
        append_log(job.id, f"failed: {exc}")
        raise
    else:
        append_log(job.id, "completed")
