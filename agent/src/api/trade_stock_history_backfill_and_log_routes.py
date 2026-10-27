"""Stock-history backfill run routes and the stock-simulator / OpenAlgo log-stream relays. Split out of trade_routes.py; the routes register on its trade_router."""

from __future__ import annotations

import json
import re
from fastapi import Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from src.api.helpers import redacted_error_detail
from src.api.security import require_event_stream_auth, require_local_or_auth
from typing import Any
from .trade_routes import trade_router


class HubStockHistoryBackfillRunRequest(BaseModel):
    day: str
    buckets: list[str] = Field(min_length=1)
    symbol: str = "NIFTY"
    include_optional: bool = True


def _backfill_run_call(fn_name: str, *args: Any, **kwargs: Any) -> dict[str, Any]:
    """Proxy one call to stock_simulator's `/backfill_runs` (D195). The run's live log is read
    through `/trade/stock-simulator/log-stream/{run.stream_key}` below."""
    from src.trade.stock_simulator_facade import sim_client
    from trade_integrations.stock_simulator.client import StockSimulatorClientError

    client = sim_client()
    try:
        payload = getattr(client, fn_name)(*args, **kwargs)
    except StockSimulatorClientError as exc:
        status = exc.status_code if exc.status_code and exc.status_code >= 400 else 502
        raise HTTPException(status_code=status, detail=redacted_error_detail(exc)) from exc
    return {"status": "ok", "run": payload.get("run")}


_SIM_LOG_KEY_RE = re.compile(r"^[A-Za-z0-9_-]{1,80}$")
# openalgo's `_LIVE_LOG_SOURCES` (services/scheduler_registry_service.py); openalgo re-checks it.
_OPENALGO_LOG_SOURCES = frozenset({"flow", "historify", "strategy", "chartink", "python_strategy"})
# APScheduler job ids: `flow_workflow_5`, `historify_schedule_3`, `start_<secure_filename stem>_<ts>`.
# No leading dot, so `.`/`..` can never become a path segment upstream.
_OPENALGO_JOB_ID_RE = re.compile(r"^[A-Za-z0-9_-][A-Za-z0-9_.-]{0,199}$")


def _relay_log_stream(upstream: str, request: Request) -> StreamingResponse:
    """Relay one upstream live-log SSE stream to the browser, frames unchanged.

    Every cross-service live log (stock_simulator, openalgo) goes through here: the browser can't
    read those streams itself (no CORS for this app's origin) and their URLs carry the service's
    secret (simulator control token, openalgo apikey), which must stay on the server."""
    import httpx

    async def relay():
        # No read timeout: the upstreams send a keepalive every 15s, and a recorder stream never ends.
        async with httpx.AsyncClient(timeout=httpx.Timeout(10.0, read=None)) as client:
            async with client.stream("GET", upstream) as resp:
                if resp.status_code != 200:
                    yield f"event: error\ndata: {json.dumps({'status': resp.status_code})}\n\n"
                    return
                async for chunk in resp.aiter_raw():
                    if await request.is_disconnected():
                        return
                    yield chunk

    return StreamingResponse(
        relay(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


@trade_router.get("/stock-simulator/log-stream/{key}", dependencies=[Depends(require_event_stream_auth)])
async def stock_simulator_log_stream(key: str, request: Request) -> StreamingResponse:
    """SSE: relay one stock_simulator live-log stream (`/scheduler-runs/{key}/stream`): a
    recorder's (the Scheduler tab) or a coverage backfill run's (D195). Ticket-authed like this
    app's other SSE routes. The frames pass through unchanged (`log` lines, then a terminal
    `status` for a finite run)."""
    from src.trade.stock_simulator_facade import sim_client

    if not _SIM_LOG_KEY_RE.match(key):
        raise HTTPException(status_code=400, detail=f"invalid log-stream key {key!r}")
    return _relay_log_stream(sim_client().log_stream_url(key), request)


@trade_router.get(
    "/openalgo/log-stream/{source}/{job_id}", dependencies=[Depends(require_event_stream_auth)]
)
async def openalgo_log_stream(source: str, job_id: str, request: Request) -> StreamingResponse:
    """SSE: relay one openalgo scheduler job's live log (`/api/v1/scheduler/registry/{source}/
    {job_id}/stream`) for the Scheduler tab. The upstream URL carries openalgo's apikey, which
    authorizes its broker/order APIs, so it never leaves this server. Ticket-authed."""
    from trade_integrations.execution.openalgo_client import OpenAlgoClient

    if source not in _OPENALGO_LOG_SOURCES:
        raise HTTPException(status_code=400, detail=f"invalid openalgo log source {source!r}")
    if not _OPENALGO_JOB_ID_RE.match(job_id):
        raise HTTPException(status_code=400, detail=f"invalid openalgo job id {job_id!r}")
    try:
        client = OpenAlgoClient()
    except RuntimeError as exc:  # OPENALGO_API_KEY not configured
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return _relay_log_stream(client.log_stream_url(source, job_id), request)


@trade_router.post("/hub/stock-history/backfill-runs", status_code=202)
def hub_stock_history_backfill_run_start(
    req: HubStockHistoryBackfillRunRequest,
    _auth: None = Depends(require_local_or_auth),
) -> dict[str, Any]:
    """Start a background backfill of `req.buckets` for the one day `req.day` (D195): the
    coverage panel's per-day "Backfill all" and per-cell button. 409 while another run is active."""
    return _backfill_run_call(
        "start_backfill_run", day=req.day, buckets=req.buckets, symbol=req.symbol,
        include_optional=req.include_optional,
    )


@trade_router.get("/hub/stock-history/backfill-runs/active")
def hub_stock_history_backfill_run_active(_auth: None = Depends(require_local_or_auth)) -> dict[str, Any]:
    return _backfill_run_call("get_active_backfill_run")


@trade_router.get("/hub/stock-history/backfill-runs/{run_id}")
def hub_stock_history_backfill_run_get(run_id: str, _auth: None = Depends(require_local_or_auth)) -> dict[str, Any]:
    return _backfill_run_call("get_backfill_run", run_id)
