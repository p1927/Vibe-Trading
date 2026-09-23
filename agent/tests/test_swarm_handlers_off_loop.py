"""The plain swarm REST handlers must do their swarm-store I/O off the event loop thread.

``GET /swarm/runs`` lists up to 100 runs and reconciles each one with ``write=True``;
``GET /swarm/runs/{id}`` and ``POST /swarm/runs/{id}/retry`` load and reconcile one run;
``POST /swarm/runs`` and retry call ``SwarmRuntime.start_run``, which reaps stale runs and
writes the new run's files; ``GET /swarm/presets`` reads the preset YAML files. All of it is
synchronous file I/O. Run on the loop thread, one slow request freezes every other request
on the process for its duration, ``/health`` included: the failure mode behind the
2026-09-11 release stalls. The SSE streams got the same guard in
``test_sse_job_streams_off_loop.py``. Trade backlog:
.claude/backlog/items/2026-09-16-vibe-swarm-handlers-sync-store-io-on-loop.md.

Each test makes every store call block its thread for 0.6s. A probe coroutine on the same
loop must keep ticking (max gap < 0.45s), and every store call must run on a thread other
than the loop's.
"""

from __future__ import annotations

import asyncio
import threading
import time
from types import SimpleNamespace
from typing import Any, Awaitable, Callable

import pytest

from tests.test_sse_job_streams_off_loop import _MAX_LOOP_GAP_SECONDS, _StoreCalls

_RUN_ID = "run-off-loop"


def _await_with_probe(call: Callable[[], Awaitable[Any]], calls: _StoreCalls, expected_calls: int) -> Any:
    """Await ``call()`` on a fresh loop next to a probe coroutine; assert the loop never stalled."""

    async def _run() -> tuple[float, int, Any]:
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
        await asyncio.sleep(0)  # probe is running before the handler's first store call
        try:
            result = await call()
        finally:
            done.set()
            await probe
        return max_gap, loop_thread, result

    max_gap, loop_thread, result = asyncio.run(_run())

    assert len(calls.threads) == expected_calls
    assert loop_thread not in calls.threads  # every store call ran off the loop thread
    assert max_gap < _MAX_LOOP_GAP_SECONDS, f"event loop stalled for {max_gap:.2f}s during a store call"
    return result


def _endpoint(path: str, method: str) -> Callable[..., Awaitable[Any]]:
    import api_server

    return next(
        r.endpoint
        for r in api_server.app.routes
        if getattr(r, "path", "") == path and method in getattr(r, "methods", ())
    )


def _run(status: str, run_id: str = _RUN_ID) -> SimpleNamespace:
    return SimpleNamespace(
        id=run_id,
        preset_name="probe_preset",
        status=SimpleNamespace(value=status),
        created_at="2026-09-16T00:00:00Z",
        completed_at=None,
        tasks=[],
        agents=[],
        user_vars={"goal": "probe"},
        final_report=None,
    )


def _install_runtime(monkeypatch: pytest.MonkeyPatch, **runtime_attrs: Any) -> None:
    from src.api import swarm_routes

    monkeypatch.setattr(swarm_routes, "_swarm_runtime", SimpleNamespace(**runtime_attrs))


class _FakeHttpRequest:
    """Stands in for the ``Request`` the create/retry handlers only pass to the shell-tools gate."""


def test_list_swarm_runs_reconciles_off_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _StoreCalls()
    store = SimpleNamespace(
        list_runs=calls.blocking([_run("running", "run-a"), _run("running", "run-b")]),
        reconcile_run=calls.blocking(_run("completed")),
        is_run_stale=calls.blocking(False),
    )
    _install_runtime(monkeypatch, _store=store)
    endpoint = _endpoint("/swarm/runs", "GET")

    # list_runs, then reconcile_run + is_run_stale for each of the two rows.
    items = _await_with_probe(lambda: endpoint(limit=20), calls, 5)

    assert [i["status"] for i in items] == ["completed", "completed"]
    assert all(i["is_stale"] is False for i in items)


def test_get_swarm_run_reconciles_off_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    # The handler imports this lazily; a first import inside the probe window would be
    # timed as a stall. Warm it so the probe measures only the store calls.
    import src.swarm.serialization  # noqa: F401

    calls = _StoreCalls()
    store = SimpleNamespace(
        load_run=calls.blocking(_run("running")),
        reconcile_run=calls.blocking(_run("completed")),
        is_run_stale=calls.blocking(False),
    )
    _install_runtime(monkeypatch, _store=store)
    endpoint = _endpoint("/swarm/runs/{run_id}", "GET")

    # load_run, reconcile_run, is_run_stale.
    detail = _await_with_probe(lambda: endpoint(run_id=_RUN_ID), calls, 3)

    assert detail["status"] == "completed"
    assert detail["is_stale"] is False


def test_get_swarm_run_missing_still_404s(monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi import HTTPException

    calls = _StoreCalls()
    _install_runtime(monkeypatch, _store=SimpleNamespace(load_run=calls.blocking(None)))
    endpoint = _endpoint("/swarm/runs/{run_id}", "GET")

    async def _call() -> int:
        with pytest.raises(HTTPException) as exc:
            await endpoint(run_id=_RUN_ID)
        return exc.value.status_code

    assert _await_with_probe(_call, calls, 1) == 404


def test_retry_swarm_run_reconciles_and_starts_off_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    import api_server
    from src.swarm.models import RunStatus

    calls = _StoreCalls()
    failed = _run("failed")
    failed.status = RunStatus.failed
    store = SimpleNamespace(
        load_run=calls.blocking(_run("running")),
        reconcile_run=calls.blocking(failed),
        is_run_stale=calls.blocking(False),
    )
    _install_runtime(
        monkeypatch, _store=store, start_run=calls.blocking(_run("pending", "run-new"))
    )
    monkeypatch.setattr(api_server, "_shell_tools_enabled_for_request", lambda _request: False)
    endpoint = _endpoint("/swarm/runs/{run_id}/retry", "POST")

    # load_run, reconcile_run, is_run_stale, then start_run.
    body = _await_with_probe(
        lambda: endpoint(run_id=_RUN_ID, http_request=_FakeHttpRequest(), resume=False), calls, 4
    )

    assert body == {"id": "run-new", "status": "pending", "preset_name": "probe_preset"}


def test_create_swarm_run_starts_off_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    import api_server

    calls = _StoreCalls()
    _install_runtime(monkeypatch, start_run=calls.blocking(_run("pending", "run-new")))
    monkeypatch.setattr(api_server, "_shell_tools_enabled_for_request", lambda _request: False)
    endpoint = _endpoint("/swarm/runs", "POST")

    body = _await_with_probe(
        lambda: endpoint(
            payload={"preset_name": "probe_preset", "user_vars": {}}, http_request=_FakeHttpRequest()
        ),
        calls,
        1,
    )

    assert body["id"] == "run-new"


def test_list_swarm_presets_reads_off_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.swarm import presets

    calls = _StoreCalls()
    monkeypatch.setattr(presets, "list_presets", calls.blocking([{"name": "probe_preset"}]))
    endpoint = _endpoint("/swarm/presets", "GET")

    assert _await_with_probe(endpoint, calls, 1) == [{"name": "probe_preset"}]
