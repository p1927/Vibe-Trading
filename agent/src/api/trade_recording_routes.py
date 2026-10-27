"""Recording and replay routes: recording start/active/sessions/constituents/job/stream, replay control and calendar, auto-record. Split out of trade_routes.py; the routes register on its trade_router."""

from __future__ import annotations

import asyncio
import json
from fastapi import Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from src.api.helpers import redacted_error_detail, safe_path_component
from src.api.security import require_local_or_auth
from typing import Any, Dict
from .trade_routes import AutoRecordRequest, AutoRecordStatusResponse, ConstituentInfo, RecordingActiveResponse, RecordingConstituentsResponse, RecordingJobResponse, RecordingJobSnapshot, RecordingRunStartResponse, RecordingSessionsResponse, ReplayStatusResponse, SeekReplayRequest, SetReplaySpeedRequest, StartRecordingRequest, StartReplayRequest, _sse_event_stream, logger, trade_router


_RECORDING_POLL_SECONDS = 0.5
_RECORDING_HEARTBEAT_SECONDS = 15.0


def _recording_sse_frame(event: str, data: dict[str, Any]) -> str:
    payload = json.dumps(data, ensure_ascii=False, default=str)
    return f"event: {event}\ndata: {payload}\n\n"


def _recording_log_to_events(entry: dict[str, Any]) -> "list[tuple[str, dict[str, Any]]]":
    return [("log", {"entry": entry})]


def _recording_terminal(job: dict[str, Any]) -> "tuple[str, dict[str, Any]] | None":
    status = str(job.get("status") or "")
    if status == "done":
        return ("done", {"result": job.get("result")})
    if status == "error":
        return ("error", {"message": job.get("error") or "unknown error"})
    return None


def _recording_heartbeat(job_id: str, status: str) -> str:
    return ": keepalive\n\n"


async def _recording_event_stream(job_id: str, request: Request):
    """Replay stored logs then poll the job store until done/error."""
    from src.trade.recording_jobs import _get_job_record, reconcile_job

    async for chunk in _sse_event_stream(
        job_id=job_id,
        request=request,
        reconcile=reconcile_job,
        get_record=_get_job_record,
        frame=_recording_sse_frame,
        log_to_events=_recording_log_to_events,
        terminal=_recording_terminal,
        heartbeat=_recording_heartbeat,
        heartbeat_seconds=_RECORDING_HEARTBEAT_SECONDS,
        poll_seconds=_RECORDING_POLL_SECONDS,
    ):
        yield chunk


def _recording_stream_response(job_id: str, request: Request) -> StreamingResponse:
    return StreamingResponse(
        _recording_event_stream(job_id, request),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# Known REST categories the day-recorder polls. The set is the union of
# the keys passed to ``CategoryScheduler.intervals`` in
# ``session_recorder.run_recording_session`` — anything else here would
# be stored on disk but never read by the recorder, so we reject it at
# the API boundary.
_KNOWN_RECORDING_CATEGORIES = (
    "option_chain", "market_depth", "full_quote",
    "equity_option_chain", "equity_market_depth", "equity_full_quote",
)

# Indmoney-native interval tokens for the historical-candle pull
# (matches session_recorder._HISTORICAL_INTERVAL_API).
_VALID_HISTORICAL_INTERVALS_API = (
    "1minute", "5minute", "15minute", "30minute",
    "60minute", "120minute", "240minute",
    "1day", "1week", "1month",
)


def _validate_recording_payload(body: StartRecordingRequest) -> None:
    """Reject malformed category_intervals / ws_throttle_hz / equity
    / historical payloads at the API boundary so the worker never
    sees a config it can't honour."""
    intervals_payloads = [
        ("category_intervals", body.category_intervals),
        ("equity_intervals", body.equity_intervals),
    ]
    for field_name, payload in intervals_payloads:
        if payload is None:
            continue
        unknown = set(payload) - set(_KNOWN_RECORDING_CATEGORIES)
        if unknown:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"{field_name} contains unknown key(s) {sorted(unknown)}; "
                    f"valid keys are {list(_KNOWN_RECORDING_CATEGORIES)}"
                ),
            )
        for cat, seconds in payload.items():
            if seconds < 0:
                raise HTTPException(
                    status_code=400,
                    detail=f"{field_name}[{cat!r}] must be >= 0 (0 = off)",
                )
    if body.ws_throttle_hz is not None and body.ws_throttle_hz < 0:
        raise HTTPException(
            status_code=400,
            detail="ws_throttle_hz must be >= 0 (0 / null = unlimited)",
        )
    if body.historical_config is not None:
        interval = body.historical_config.get("interval")
        lookback = body.historical_config.get("lookback_days")
        if interval is not None and interval not in _VALID_HISTORICAL_INTERVALS_API:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"historical_config.interval={interval!r} not supported; "
                    f"valid intervals are {list(_VALID_HISTORICAL_INTERVALS_API)}"
                ),
            )
        if lookback is not None and (not isinstance(lookback, int) or lookback < 1):
            raise HTTPException(
                status_code=400,
                detail="historical_config.lookback_days must be a positive integer",
            )


def _normalize_recording_request(
    body: StartRecordingRequest,
) -> tuple[list[str], list[str]]:
    """Validate + normalise underlyings/equities shared by start + auto-record."""
    _validate_recording_payload(body)
    underlyings = [u.strip().upper() for u in body.underlyings if u.strip()] or [
        "NIFTY",
        "BANKNIFTY",
        "SENSEX",
    ]
    equities = [e.strip().upper() for e in body.equities if e.strip()]
    for symbol in (*underlyings, *equities):
        safe_path_component(symbol, "symbol")
    if body.include_nifty50_constituents:
        from trade_integrations.dataflows.index_research.constituents import (
            load_nifty50_constituents,
        )

        constituent_symbols = {
            r.symbol.strip().upper() for r in load_nifty50_constituents() if r.symbol.strip()
        }
        equities = sorted(set(equities) | constituent_symbols)
    return underlyings, equities


def _kick_recording(body: StartRecordingRequest) -> tuple[str, str, bool]:
    underlyings, equities = _normalize_recording_request(body)
    from src.trade.recording_jobs import kick_recording

    return kick_recording(
        underlyings=underlyings,
        equities=equities,
        poll_interval_s=body.poll_interval_s,
        category_intervals=body.category_intervals,
        equity_intervals=body.equity_intervals,
        ws_throttle_hz=body.ws_throttle_hz,
        historical_config=body.historical_config,
        wait_for_open=body.wait_for_open,
    )


@trade_router.post(
    "/recording/start",
    response_model=RecordingRunStartResponse,
    status_code=202,
)
def start_recording(
    body: StartRecordingRequest,
    _auth: None = Depends(require_local_or_auth),
) -> RecordingRunStartResponse:
    """Start (or resume) the stock-simulator day recorder."""
    job_id, job_status, reused = _kick_recording(body)
    return RecordingRunStartResponse(job_id=job_id, job_status=job_status, reused=reused)


@trade_router.get("/recording/active", response_model=RecordingActiveResponse)
def get_active_recording(
    _auth: None = Depends(require_local_or_auth),
) -> RecordingActiveResponse:
    from src.trade.recording_jobs import get_active_job

    snap = get_active_job()
    if snap is None:
        return RecordingActiveResponse(job=None)
    return RecordingActiveResponse(job=RecordingJobSnapshot(**snap))


@trade_router.get("/recording/sessions", response_model=RecordingSessionsResponse)
def list_recording_sessions(
    _auth: None = Depends(require_local_or_auth),
) -> RecordingSessionsResponse:
    """Days available to replay — scans the exported index + equity parquet files."""
    from trade_integrations.stock_simulator.client import StockSimulatorClient, StockSimulatorClientError

    client = StockSimulatorClient()
    days: set[str] = set()
    try:
        for symbol, exchange in (
            ("NIFTY", "NSE_INDEX"),
            ("BANKNIFTY", "NSE_INDEX"),
            ("SENSEX", "BSE_INDEX"),
        ):
            days.update(client.get_recorded_index_days(symbol=symbol, exchange=exchange)["data"])

        for symbol in client.get_recorded_equities()["data"]:
            days.update(client.get_recorded_index_days(symbol=symbol, exchange="NSE")["data"])
    except StockSimulatorClientError as exc:
        # An outage must not read as "no recorded days": the replay picker would show an empty
        # list with nothing to retry.
        logger.exception("stock_simulator unavailable while listing recording sessions")
        raise HTTPException(status_code=502, detail=redacted_error_detail(exc)) from exc

    return RecordingSessionsResponse(sessions=sorted(days, reverse=True))


@trade_router.get("/recording/constituents", response_model=RecordingConstituentsResponse)
def get_recording_constituents(
    _auth: None = Depends(require_local_or_auth),
) -> RecordingConstituentsResponse:
    """Current NIFTY50 constituents, for the equity-recording picker."""
    from trade_integrations.dataflows.index_research.constituents import (
        load_nifty50_constituents,
    )

    rows = load_nifty50_constituents()
    return RecordingConstituentsResponse(
        constituents=[
            ConstituentInfo(
                symbol=row.symbol,
                name=row.name or row.symbol,
                sector=row.sector,
                weight=row.weight,
                weight_missing=row.weight_missing,
            )
            for row in rows
        ]
    )


def _simulator_client() -> "StockSimulatorClient":
    from trade_integrations.stock_simulator.client import StockSimulatorClient

    return StockSimulatorClient()


def _simulator_not_configured() -> HTTPException:
    return HTTPException(
        status_code=503,
        detail="SIMULATOR_CONTROL_TOKEN is not configured — set it to match the "
        "stock_simulator service's token to enable replay control.",
    )


def _run_control(fn):
    """Call a `StockSimulatorClient` control method, mapping its errors to HTTPException.

    The client talks directly to the standalone `stock_simulator` service now —
    OpenAlgo is no longer in this path at all, so there's no clock to mirror
    into this process's env afterward (see the backlog item's Phase 1-3 plan:
    `.claude/backlog/items/2026-08-21-stock-simulator-single-clock-source-of-truth.md`).
    """
    from trade_integrations.stock_simulator.client import StockSimulatorClientError

    client = _simulator_client()
    if not client.is_configured:
        raise _simulator_not_configured()
    try:
        return fn(client)
    except StockSimulatorClientError as exc:
        status = exc.status_code if exc.status_code and exc.status_code >= 400 else 502
        raise HTTPException(status_code=status, detail=redacted_error_detail(exc)) from exc


@trade_router.post("/recording/{day}/replay", response_model=ReplayStatusResponse)
def start_replay(
    day: str,
    body: StartReplayRequest,
    _auth: None = Depends(require_local_or_auth),
) -> ReplayStatusResponse:
    """Arm the stock_simulator service to replay a previously recorded day."""
    payload = _run_control(
        lambda c: c.start_replay(day, end_date=body.end_date, speed=body.speed, loop=body.loop)
    )
    return ReplayStatusResponse(status="ok", replay=payload)


@trade_router.get("/recording/replay/status", response_model=ReplayStatusResponse)
def get_replay_status(
    _auth: None = Depends(require_local_or_auth),
) -> ReplayStatusResponse:
    """Current simulator replay clock state, without arming a new day."""
    payload = _run_control(lambda c: c.status())
    return ReplayStatusResponse(status="ok", replay=payload)


@trade_router.post("/recording/replay/pause", response_model=ReplayStatusResponse)
def pause_replay(
    _auth: None = Depends(require_local_or_auth),
) -> ReplayStatusResponse:
    """Pause the simulator clock (does not unload it)."""
    payload = _run_control(lambda c: c.pause())
    return ReplayStatusResponse(status="ok", replay=payload)


@trade_router.post("/recording/replay/resume", response_model=ReplayStatusResponse)
def resume_replay(
    _auth: None = Depends(require_local_or_auth),
) -> ReplayStatusResponse:
    """Resume a paused simulator clock."""
    payload = _run_control(lambda c: c.resume())
    return ReplayStatusResponse(status="ok", replay=payload)


@trade_router.post("/recording/replay/seek", response_model=ReplayStatusResponse)
def seek_replay(
    body: SeekReplayRequest,
    _auth: None = Depends(require_local_or_auth),
) -> ReplayStatusResponse:
    """Scrub the simulator clock to an arbitrary point in the armed day.

    ``body.time`` is either ``HH:MM[:SS]`` (applied to the currently armed
    replay date) or a full ISO datetime.
    """
    payload = _run_control(lambda c: c.seek(body.time))
    return ReplayStatusResponse(status="ok", replay=payload)


@trade_router.post("/recording/replay/speed", response_model=ReplayStatusResponse)
def set_replay_speed(
    body: SetReplaySpeedRequest,
    _auth: None = Depends(require_local_or_auth),
) -> ReplayStatusResponse:
    """Change the simulator clock's replay rate live, without re-arming it."""
    payload = _run_control(lambda c: c.set_speed(body.speed))
    return ReplayStatusResponse(status="ok", replay=payload)


@trade_router.post("/recording/replay/stop", response_model=ReplayStatusResponse)
def stop_replay(
    _auth: None = Depends(require_local_or_auth),
) -> ReplayStatusResponse:
    """Tear down the simulator so consumers fall back to live."""
    payload = _run_control(lambda c: c.stop())
    return ReplayStatusResponse(status="ok", replay=payload)


@trade_router.get("/recording/replay/calendar")
def get_replay_calendar(
    _auth: None = Depends(require_local_or_auth),
):
    """Per-day parquet coverage for the calendar heatmap.

    Missing configuration is a distinct, expected state (a fresh install
    that hasn't wired up replay control yet) — it returns 200 with
    `configured=false` and an empty calendar rather than a 503, so the UI
    can render "replay not available, configure the token" instead of a
    generic "calendar failed to load" error with a Retry button that can
    never succeed.
    """
    client = _simulator_client()
    if not client.is_configured:
        return {
            "status": "ok",
            "configured": False,
            "days": [],
            "underlyings": [],
            "message": "SIMULATOR_CONTROL_TOKEN is not configured — set it to match the "
            "stock_simulator service's token to enable replay control.",
        }
    from trade_integrations.stock_simulator.client import StockSimulatorClientError

    try:
        payload = client.calendar()
    except StockSimulatorClientError as exc:
        status = exc.status_code if exc.status_code and exc.status_code >= 400 else 502
        raise HTTPException(status_code=status, detail=redacted_error_detail(exc)) from exc
    return {"configured": True, **payload}

# Routes below live in src/api/trade_markets_routes.py. The import sits here so they register on trade_router
# in their original order; the names are re-exported because callers and tests patch/import them from trade_routes.
from .trade_markets_routes import (  # noqa: E402,F401
    ArmMultiMarketRequest,
    SeekMultiMarketRequest,
    SetMultiMarketSpeedRequest,
    StartMarketTickRecordingRequest,
    arm_multi_market,
    get_market_bundle,
    get_market_economy_factor,
    get_market_factor_coverage,
    get_market_flow_of_funds,
    get_market_global_macro,
    get_market_global_macro_live_spot,
    get_market_global_macro_ui_cards,
    get_market_index_history,
    get_market_live_spot,
    get_market_policy_factors,
    get_market_replay_calendar,
    get_market_sector_indices,
    get_market_top_constituents,
    get_markets_registry,
    get_multi_market_quote,
    get_multi_market_status,
    list_market_global_macro_refreshable_series,
    list_market_tick_recordings,
    pause_multi_market,
    refresh_market_global_macro,
    resume_multi_market,
    seek_multi_market,
    set_multi_market_speed,
    start_market_tick_recording,
    stop_market_tick_recording,
    stop_multi_market,
)


@trade_router.post("/recording/auto-record", response_model=AutoRecordStatusResponse)
def set_auto_record(
    body: AutoRecordRequest,
    _auth: None = Depends(require_local_or_auth),
) -> AutoRecordStatusResponse:
    """Enable/disable Auto Record: re-arm ``wait_for_open`` recording every
    trading day (start at open, the recorder already stops itself at
    market close — see ``session_recorder.py``'s in-loop market-hours
    gate). Enabling captures the given config as the daily template and,
    if nothing is currently recording, immediately arms a session (starts
    right away if the market's open now, otherwise schedules today/next
    session's wake — same as pressing Record with "wait for market open").
    Disabling only stops future re-arms; it does not touch an
    already-running or already-waiting session — stop that separately via
    ``POST /recording/{job_id}/stop`` if desired.

    Registered before the ``/recording/{job_id}`` GET route below so
    ``auto-record`` isn't swallowed as a ``job_id`` path parameter.
    """
    from src.trade.recording_auto import save_auto_record

    config: Dict[str, Any] | None = None
    if body.enabled:
        underlyings, equities = _normalize_recording_request(
            StartRecordingRequest(
                underlyings=body.underlyings,
                equities=body.equities,
                include_nifty50_constituents=body.include_nifty50_constituents,
                poll_interval_s=body.poll_interval_s,
                category_intervals=body.category_intervals,
                equity_intervals=body.equity_intervals,
                ws_throttle_hz=body.ws_throttle_hz,
                historical_config=body.historical_config,
            )
        )
        config = {
            "underlyings": underlyings,
            "equities": equities,
            "poll_interval_s": body.poll_interval_s,
            "category_intervals": body.category_intervals,
            "equity_intervals": body.equity_intervals,
            "ws_throttle_hz": body.ws_throttle_hz,
            "historical_config": body.historical_config,
        }
    state = save_auto_record(enabled=body.enabled, config=config)

    from src.trade.recording_jobs import get_active_job

    if body.enabled and get_active_job() is None:
        from src.trade.recording_jobs import kick_recording

        kick_recording(
            underlyings=config["underlyings"],
            equities=config["equities"],
            poll_interval_s=config["poll_interval_s"],
            category_intervals=config["category_intervals"],
            equity_intervals=config["equity_intervals"],
            ws_throttle_hz=config["ws_throttle_hz"],
            historical_config=config["historical_config"],
            wait_for_open=True,
        )
    active = get_active_job()
    active_job_id = active.get("job_id") if active else None
    active_job_status = active.get("status") if active else None
    return AutoRecordStatusResponse(
        enabled=state["enabled"],
        config=state["config"],
        updated_at=state["updated_at"],
        active_job_id=active_job_id,
        active_job_status=active_job_status,
    )


@trade_router.get("/recording/auto-record", response_model=AutoRecordStatusResponse)
def get_auto_record(
    _auth: None = Depends(require_local_or_auth),
) -> AutoRecordStatusResponse:
    from src.trade.recording_auto import load_auto_record
    from src.trade.recording_jobs import get_active_job

    state = load_auto_record()
    active = get_active_job()
    return AutoRecordStatusResponse(
        enabled=state["enabled"],
        config=state["config"],
        updated_at=state["updated_at"],
        active_job_id=active.get("job_id") if active else None,
        active_job_status=active.get("status") if active else None,
    )


@trade_router.get("/recording/{job_id}", response_model=RecordingJobResponse)
def get_recording_job(
    job_id: str,
    _auth: None = Depends(require_local_or_auth),
) -> RecordingJobResponse:
    from src.trade.recording_jobs import get_job, job_id_valid

    if not job_id_valid(job_id):
        raise HTTPException(status_code=400, detail="invalid job_id")
    snap = get_job(job_id)
    if snap is None:
        raise HTTPException(status_code=404, detail=f"job {job_id} not found")
    return RecordingJobResponse(job=RecordingJobSnapshot(**snap))


@trade_router.post("/recording/{job_id}/stop")
def stop_recording(
    job_id: str,
    _auth: None = Depends(require_local_or_auth),
) -> dict[str, str]:
    """Request cooperative stop for an in-flight recording session."""
    from src.trade.recording_jobs import _ACTIVE_STATUSES, _get_job_record, job_id_valid, stop_job

    if not job_id_valid(job_id):
        raise HTTPException(status_code=400, detail="invalid job_id")
    job = _get_job_record(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"job {job_id} not found")
    status = str(job.get("status") or "")
    if status not in _ACTIVE_STATUSES:
        raise HTTPException(status_code=409, detail=f"job is not active (status={status})")
    return {"status": "ok", "message": stop_job(job_id)}


@trade_router.get("/recording/{job_id}/stream")
async def stream_recording_job(
    job_id: str,
    request: Request,
    _auth: None = Depends(require_local_or_auth),
) -> StreamingResponse:
    """SSE: replay recorder logs and stream until the session terminates."""
    from src.trade.recording_jobs import _get_job_record, job_id_valid

    if not job_id_valid(job_id):
        raise HTTPException(status_code=400, detail="invalid job_id")
    if await asyncio.to_thread(_get_job_record, job_id) is None:
        raise HTTPException(status_code=404, detail=f"job {job_id} not found")
    return _recording_stream_response(job_id, request)
