"""Every job-polling SSE stream must do its store I/O off the event loop thread.

Each stream below is an ``async def`` generator that, every poll tick for every connected
client, calls a synchronous job-store read: a job-file read and parse, a ``_JOBS_LOCK`` a
writer thread may hold, a worker-PID probe, or a swarm ``run.json``/``events.jsonl`` read
plus a reconcile write. Run on the loop thread, a slow one freezes every request on the
process for its duration, ``/health`` included. That is how the Command Center stream froze
release on 2026-09-11. Fork 19f04199 fixed that stream (see
``test_command_center_stream.py::test_blocking_legs_do_not_stall_the_event_loop``); this file
holds the same guard for the rest. Trade backlog:
.claude/backlog/items/2026-09-16-vibe-sse-job-streams-sync-reads-on-loop.md.

Each test makes every store call block its thread for 0.3s. A probe coroutine on the same
loop must keep ticking (max gap < 0.2s), and every store call must run on a thread other
than the loop's.
"""

from __future__ import annotations

import asyncio
import threading
import time
from types import SimpleNamespace
from typing import Any, AsyncIterator, Callable

import pytest

from src.api import trade_routes as routes

_BLOCK_SECONDS = 0.3
_MAX_LOOP_GAP_SECONDS = 0.2


class _FakeRequest:
    """Never disconnects: each stream here ends on its own terminal job status."""

    async def is_disconnected(self) -> bool:
        return False


class _StoreCalls:
    """Builds blocking fakes for store functions and records which thread ran each call."""

    def __init__(self) -> None:
        self.threads: list[int] = []

    def blocking(self, result: Any) -> Callable[..., Any]:
        def _call(*_a: Any, **_kw: Any) -> Any:
            self.threads.append(threading.get_ident())
            time.sleep(_BLOCK_SECONDS)
            return result

        return _call


def _drain_with_probe(
    stream: Callable[[], AsyncIterator[Any]], calls: _StoreCalls, expected_calls: int
) -> list[str]:
    """Drain ``stream()`` on a fresh loop next to a probe coroutine; assert the loop never stalled."""

    async def _run() -> tuple[float, int, list[str]]:
        loop_thread = threading.get_ident()
        done = asyncio.Event()
        max_gap = 0.0

        async def _probe() -> None:
            nonlocal max_gap
            last = time.monotonic()
            while not done.is_set():
                await asyncio.sleep(0.01)
                now = time.monotonic()
                max_gap = max(max_gap, now - last)
                last = now

        probe = asyncio.create_task(_probe())
        await asyncio.sleep(0)  # probe is running before the stream's first store call
        frames: list[str] = []
        try:
            async for frame in stream():
                frames.append(frame if isinstance(frame, str) else frame.decode())
        finally:
            done.set()
            await probe
        return max_gap, loop_thread, frames

    max_gap, loop_thread, frames = asyncio.run(_run())

    assert len(calls.threads) == expected_calls
    assert loop_thread not in calls.threads  # every store call ran off the loop thread
    assert max_gap < _MAX_LOOP_GAP_SECONDS, f"event loop stalled for {max_gap:.2f}s during a store call"
    return frames


def test_external_predictions_refresh_stream_polls_job_store_off_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.trade import external_predictions_run_jobs as jobs

    calls = _StoreCalls()
    monkeypatch.setattr(jobs, "reconcile_zombie_job", calls.blocking(False))
    monkeypatch.setattr(
        jobs,
        "_get_job_record",
        calls.blocking({"status": "done", "ticker": "NIFTY", "logs": [], "snapshot": {"sources": []}}),
    )

    frames = _drain_with_probe(
        lambda: routes._external_predictions_refresh_event_stream("a" * 32, _FakeRequest()), calls, 2
    )

    assert frames[-1].startswith("event: done")


def test_index_prediction_run_stream_polls_job_store_off_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.trade import index_prediction_run_jobs as jobs

    calls = _StoreCalls()
    monkeypatch.setattr(jobs, "reconcile_job", calls.blocking(False))
    monkeypatch.setattr(
        jobs,
        "_get_job_record",
        calls.blocking({"status": "done", "ticker": "NIFTY", "logs": [], "artifact": {"spot": 24000.0}}),
    )

    frames = _drain_with_probe(
        lambda: routes._index_prediction_run_event_stream("b" * 32, _FakeRequest()), calls, 2
    )

    assert frames[-1].startswith("event: done")


def test_recording_stream_polls_job_store_off_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.trade import recording_jobs as jobs

    calls = _StoreCalls()
    monkeypatch.setattr(jobs, "reconcile_job", calls.blocking(False))
    monkeypatch.setattr(jobs, "_get_job_record", calls.blocking({"status": "done", "logs": [], "result": {}}))

    frames = _drain_with_probe(lambda: routes._recording_event_stream("c" * 32, _FakeRequest()), calls, 2)

    assert frames[-1].startswith("event: done")


def test_scheduled_run_log_stream_reads_job_store_off_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.api import scheduled_routes
    from src.scheduled_research.models import JobStatus

    calls = _StoreCalls()
    store = SimpleNamespace(get=calls.blocking(SimpleNamespace(status=JobStatus.COMPLETED)))
    monkeypatch.setattr(scheduled_routes, "_get_scheduled_research_store", lambda: store)

    frames = _drain_with_probe(
        lambda: scheduled_routes._scheduled_run_log_stream("off-loop-probe-job", _FakeRequest()), calls, 1
    )

    assert frames[-1].startswith("event: status")


def test_swarm_run_events_stream_reads_store_off_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    """Covers the pre-stream 404 check too: it is the first ``load_run`` on each connection."""
    import api_server
    from src.api import swarm_routes

    calls = _StoreCalls()
    store = SimpleNamespace(
        load_run=calls.blocking(SimpleNamespace(id="run-off-loop")),
        read_events=calls.blocking([]),
        reconcile_run=calls.blocking(SimpleNamespace(status=SimpleNamespace(value="completed"))),
    )
    monkeypatch.setattr(swarm_routes, "_swarm_runtime", SimpleNamespace(_store=store))
    route = next(r for r in api_server.app.routes if getattr(r, "path", "") == "/swarm/runs/{run_id}/events")

    async def _stream() -> AsyncIterator[Any]:
        response = await route.endpoint(
            run_id="run-off-loop", request=_FakeRequest(), last_index=0, last_event_id=None
        )
        async for chunk in response.body_iterator:
            yield chunk

    # load_run (404 check), then one poll tick: read_events, load_run, reconcile_run.
    frames = _drain_with_probe(_stream, calls, 4)

    assert frames[-1].startswith("event: done")
