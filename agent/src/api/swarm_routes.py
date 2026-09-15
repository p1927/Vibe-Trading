"""Swarm HTTP routes.

Mounted by ``agent/api_server.py`` via ``register_swarm_routes(app, ...)``.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Awaitable, Callable

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

# ---------------------------------------------------------------------------
# Module-level state
# ---------------------------------------------------------------------------

_swarm_runtime = None


def _get_swarm_runtime():
    """Lazy-init SwarmRuntime singleton."""
    global _swarm_runtime
    if _swarm_runtime is not None:
        return _swarm_runtime
    from src.config import load_swarm_agent_config
    from src.swarm.store import SwarmStore, swarm_runs_root
    from src.swarm.runtime import SwarmRuntime

    store = SwarmStore(base_dir=swarm_runs_root())
    # Boot-time / operator-trusted: REST API callers cannot influence the
    # config path. See docs/2026-05-25_swarm_mcp_tools_roadmap.md.
    agent_config = load_swarm_agent_config()
    _swarm_runtime = SwarmRuntime(store=store, agent_config=agent_config)
    return _swarm_runtime


# Fork: the swarm store is file I/O (run.json, tasks/*.json, events.jsonl), and
# reconcile_run(write=True) may rewrite those files. The handlers below run these
# helpers with asyncio.to_thread, one thread call per request, so a slow disk never
# freezes the event loop (and /health with it). Trade backlog:
# .claude/backlog/items/2026-09-16-vibe-swarm-handlers-sync-store-io-on-loop.md


def _reconciled_run_rows(store, limit: int) -> list[tuple[Any, bool]]:
    """``list_runs`` + reconcile + staleness for each row: ``[(run, is_stale), ...]``."""
    rows = []
    for r in store.list_runs(limit=limit):
        # Reconcile each row: a zombie running run will be auto-finalized so
        # the dashboard never shows a "running" stuck row.
        reconciled = store.reconcile_run(r, write=True)
        rows.append((reconciled, store.is_run_stale(reconciled)))
    return rows


def _load_reconciled_run(store, run_id: str) -> tuple[Any, bool] | None:
    """``load_run`` + reconcile + staleness: ``(run, is_stale)``, or None if not found."""
    loaded = store.load_run(run_id)
    if not loaded:
        return None
    run = store.reconcile_run(loaded, write=True)
    return run, store.is_run_stale(run)


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

AuthDep = Callable[..., Awaitable[Any] | Any]


def register_swarm_routes(
    app: FastAPI,
    require_auth: AuthDep | None = None,
    require_event_stream_auth: AuthDep | None = None,
) -> None:
    """Mount the swarm routes onto ``app``.

    Resolves ``require_auth`` and ``require_event_stream_auth`` from the host
    ``api_server`` module via ``sys.modules`` when not passed explicitly.
    """
    import sys as _sys

    host = _sys.modules.get("api_server") or _sys.modules.get("agent.api_server")
    if host is None:
        raise RuntimeError(
            "register_swarm_routes: api_server module not in sys.modules; "
            "ensure api_server is imported before calling this function"
        )

    if require_auth is None:
        require_auth = host.require_auth
    if require_event_stream_auth is None:
        require_event_stream_auth = host.require_event_stream_auth

    def _host_validate_path_param(value: str, kind: str) -> None:
        h = _sys.modules.get("api_server") or _sys.modules.get("agent.api_server")
        h._validate_path_param(value, kind)

    def _host_shell_tools_enabled_for_request(request: Request) -> bool:
        h = _sys.modules.get("api_server") or _sys.modules.get("agent.api_server")
        return h._shell_tools_enabled_for_request(request)

    # --- Routes ---

    @app.get("/swarm/presets", dependencies=[Depends(require_auth)])
    async def list_swarm_presets():
        """List Swarm YAML presets.

        Authenticated for the same reason as ``/skills``: the preset inventory
        describes configured agent capabilities and should not be readable by a
        peer that cannot start a swarm run.
        """
        from src.swarm.presets import list_presets

        return await asyncio.to_thread(list_presets)

    @app.post("/swarm/runs", dependencies=[Depends(require_auth)])
    async def create_swarm_run(payload: dict, http_request: Request):
        """Start a swarm run: body must include preset_name and user_vars."""
        runtime = _get_swarm_runtime()
        preset_name = payload.get("preset_name", "")
        user_vars = payload.get("user_vars", {})
        try:
            run = await asyncio.to_thread(
                runtime.start_run,
                preset_name,
                user_vars,
                include_shell_tools=_host_shell_tools_enabled_for_request(http_request),
            )
            return {"id": run.id, "status": run.status.value, "preset_name": run.preset_name}
        except FileNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e))
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

    @app.get("/swarm/runs", dependencies=[Depends(require_auth)])
    async def list_swarm_runs(limit: int = Query(20, ge=1, le=100)):
        """List swarm runs (newest first), reconciled."""
        runtime = _get_swarm_runtime()
        rows = await asyncio.to_thread(_reconciled_run_rows, runtime._store, limit)
        items = []
        for reconciled, is_stale in rows:
            items.append(
                {
                    "id": reconciled.id,
                    "preset_name": reconciled.preset_name,
                    "status": reconciled.status.value,
                    "is_stale": is_stale,
                    "created_at": reconciled.created_at,
                    "completed_at": reconciled.completed_at,
                    "task_count": len(reconciled.tasks),
                    "completed_count": sum(
                        1 for t in reconciled.tasks if t.status.value == "completed"
                    ),
                }
            )
        return items

    @app.get("/swarm/runs/{run_id}", dependencies=[Depends(require_auth)])
    async def get_swarm_run(run_id: str):
        """Swarm run detail including task statuses (reconciled)."""
        _host_validate_path_param(run_id, "run_id")
        runtime = _get_swarm_runtime()
        found = await asyncio.to_thread(_load_reconciled_run, runtime._store, run_id)
        if not found:
            raise HTTPException(status_code=404, detail=f"Run {run_id} not found")

        run, is_stale = found

        from src.swarm.serialization import serialize_task

        return {
            "id": run.id,
            "preset_name": run.preset_name,
            "status": run.status.value,
            "is_stale": is_stale,
            "user_vars": run.user_vars,
            "agents": [a.model_dump() for a in run.agents],
            "tasks": [
                {
                    **serialize_task(t),
                    # Keep the existing REST field while sharing the public
                    # serializer used by the other swarm read paths.
                    "worker_iterations": getattr(t, "worker_iterations", 0),
                }
                for t in run.tasks
            ],
            "created_at": run.created_at,
            "completed_at": run.completed_at,
            "final_report": run.final_report,
        }

    @app.get(
        "/swarm/runs/{run_id}/events",
        dependencies=[Depends(require_event_stream_auth)],
    )
    async def swarm_run_events(
        run_id: str,
        request: Request,
        last_index: int = Query(0, ge=0),
        last_event_id: int | None = Header(None, alias="Last-Event-ID", ge=0),
    ):
        """SSE stream for a swarm run."""
        import asyncio

        _host_validate_path_param(run_id, "run_id")
        runtime = _get_swarm_runtime()
        # Store reads/writes are file I/O: run them off the event loop thread.
        if not await asyncio.to_thread(runtime._store.load_run, run_id):
            raise HTTPException(status_code=404, detail=f"Run {run_id} not found")

        async def event_stream():
            # Browser EventSource reconnects with Last-Event-ID. Keep the
            # existing query parameter for non-browser and older clients.
            idx = last_event_id if last_event_id is not None else last_index
            while True:
                if await request.is_disconnected():
                    break
                events = await asyncio.to_thread(runtime._store.read_events, run_id, after_index=idx)
                for evt in events:
                    idx += 1
                    yield f"id: {idx}\nevent: {evt.type}\ndata: {json.dumps(evt.model_dump(), ensure_ascii=False)}\n\n"
                run = await asyncio.to_thread(runtime._store.load_run, run_id)
                if not run:
                    yield 'event: done\ndata: {"status": "missing"}\n\n'
                    break
                # Reconcile so a zombie running run can still close this SSE
                # stream cleanly — without it, a dead host would keep the
                # stream open forever and block the dashboard's "done" state.
                reconciled = await asyncio.to_thread(runtime._store.reconcile_run, run, write=True)
                if reconciled.status.value in ("completed", "failed", "cancelled"):
                    yield f'event: done\ndata: {{"status": "{reconciled.status.value}"}}\n\n'
                    break
                await asyncio.sleep(2)

        return StreamingResponse(event_stream(), media_type="text/event-stream")

    @app.post("/swarm/runs/{run_id}/cancel", dependencies=[Depends(require_auth)])
    async def cancel_swarm_run(run_id: str):
        """Cancel an active swarm run."""
        _host_validate_path_param(run_id, "run_id")
        runtime = _get_swarm_runtime()
        ok = runtime.cancel_run(run_id)
        if not ok:
            raise HTTPException(status_code=404, detail=f"No active run {run_id}")
        return {"status": "cancelled"}

    @app.post("/swarm/runs/{run_id}/retry", dependencies=[Depends(require_auth)])
    async def retry_swarm_run(run_id: str, http_request: Request, resume: bool = Query(False)):
        """Retry a failed, stale, or cancelled swarm run.

        Creates a new run with the same preset and user_vars as the original.
        ``resume=true`` replays: completed upstream tasks and their artifacts
        are carried into the new run and only the failed/cancelled subgraph
        re-executes.
        """
        _host_validate_path_param(run_id, "run_id")
        runtime = _get_swarm_runtime()
        # Reconcile first so a stale "running" run whose host died gets demoted
        # before we gate on status; only a genuinely active run blocks retry.
        found = await asyncio.to_thread(_load_reconciled_run, runtime._store, run_id)
        if not found:
            raise HTTPException(status_code=404, detail=f"Run {run_id} not found")

        from src.swarm.models import RunStatus

        reconciled, _is_stale = found
        if reconciled.status == RunStatus.running:
            raise HTTPException(
                status_code=409, detail="Cannot retry a running run. Cancel it first."
            )
        if resume and reconciled.status not in (RunStatus.failed, RunStatus.cancelled):
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Cannot resume a run in status '{reconciled.status.value}'; "
                    "resume only applies to failed or cancelled runs."
                ),
            )

        try:
            new_run = await asyncio.to_thread(
                runtime.start_run,
                reconciled.preset_name,
                reconciled.user_vars or {},
                include_shell_tools=_host_shell_tools_enabled_for_request(http_request),
                resume_from=reconciled if resume else None,
            )
            return {
                "id": new_run.id,
                "status": new_run.status.value,
                "preset_name": new_run.preset_name,
            }
        except FileNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e))
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
