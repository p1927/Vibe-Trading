"""External-predictions routes: read, refresh (job start/active/stream), source add/remove/discover/approve, thumbnail. Split out of trade_routes.py; the routes register on its trade_router."""

from __future__ import annotations

import asyncio
from fastapi import Depends, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from src.api.helpers import bad_gateway_on_error, redacted_error_detail
from src.api.security import require_local_or_auth
from typing import Any
from .trade_routes import ExternalPredictionSourceRequest, ExternalPredictionSourcesResponse, ExternalPredictionsRefreshActiveResponse, ExternalPredictionsRefreshJobResponse, ExternalPredictionsRefreshRequest, ExternalPredictionsRefreshStartResponse, ExternalPredictionsResponse, _INDEX_PREDICTION_RUN_HEARTBEAT_SECONDS, _INDEX_PREDICTION_RUN_POLL_SECONDS, _external_prediction_source_health, _external_predictions_status, _index_prediction_run_sse_frame, _sse_event_stream, _ticker_key, logger, trade_router


@trade_router.get(
    "/index-prediction/external-predictions",
    response_model=ExternalPredictionsResponse,
)
def get_external_predictions(
    ticker: str = "NIFTY",
    horizon_days: int = 14,
    _auth: None = Depends(require_local_or_auth),
) -> ExternalPredictionsResponse:
    """Return cached third-party NIFTY forecasts (no live fetch)."""
    key = _ticker_key(ticker)
    with bad_gateway_on_error(logger, "get external predictions failed for %s", key):
        from src.trade.hub_bridge import ensure_trade_stack_path
        from trade_integrations.dataflows.index_research.external_predictions.store import (
            load_snapshot,
        )

        ensure_trade_stack_path()
        snapshot = load_snapshot(symbol=key, horizon_days=horizon_days)
        snapshot_payload = snapshot.to_dict()
        snapshot_payload["source_health"] = _external_prediction_source_health(snapshot)
        return ExternalPredictionsResponse(
            status=_external_predictions_status(snapshot),
            ticker=key,
            snapshot=snapshot_payload,
        )


@trade_router.post(
    "/index-prediction/external-predictions/refresh",
    response_model=ExternalPredictionsResponse,
)
def refresh_external_predictions(
    body: ExternalPredictionsRefreshRequest,
    _auth: None = Depends(require_local_or_auth),
) -> ExternalPredictionsResponse:
    """Fetch latest third-party forecasts from watchlisted sources."""
    key = _ticker_key(body.ticker)
    with bad_gateway_on_error(logger, "refresh external predictions failed for %s", key):
        from src.trade.hub_bridge import ensure_trade_stack_path
        from trade_integrations.dataflows.index_research.external_predictions.refresh import (
            refresh_all_external_predictions,
        )

        ensure_trade_stack_path()
        snapshot = refresh_all_external_predictions(
            symbol=key,
            horizon_days=body.horizon_days,
        )
        snapshot_payload = snapshot.to_dict()
        snapshot_payload["source_health"] = _external_prediction_source_health(snapshot)
        return ExternalPredictionsResponse(
            status=_external_predictions_status(snapshot),
            ticker=key,
            snapshot=snapshot_payload,
        )


def _kick_external_predictions_refresh(body: ExternalPredictionsRefreshRequest) -> tuple[str, str, bool]:
    from src.trade.external_predictions_run_jobs import kick_external_predictions_refresh

    return kick_external_predictions_refresh(
        ticker=_ticker_key(body.ticker),
        horizon_days=body.horizon_days,
    )


def _external_predictions_log_to_events(entry: dict[str, Any]) -> "list[tuple[str, dict[str, Any]]]":
    if str(entry.get("stage") or "") == "source_complete":
        return [
            (
                "source_complete",
                {
                    "source_id": entry.get("source_id"),
                    "record": entry.get("record"),
                    "partial_snapshot": entry.get("partial_snapshot"),
                },
            )
        ]
    return [("log", {"entry": entry})]


def _external_predictions_terminal(job: dict[str, Any]) -> "tuple[str, dict[str, Any]] | None":
    status = str(job.get("status") or "")
    if status == "done":
        snapshot = job.get("snapshot")
        ticker = str(job.get("ticker") or "")
        if snapshot is not None:
            return ("done", {"ticker": ticker, "snapshot": snapshot})
        return ("error", {"message": "job completed without snapshot"})
    if status == "error":
        return ("error", {"message": job.get("error") or "unknown error"})
    return None


def _external_predictions_heartbeat(job_id: str, status: str) -> str:
    return _index_prediction_run_sse_frame("heartbeat", {"job_id": job_id, "status": status})


async def _external_predictions_refresh_event_stream(job_id: str, request: Request):
    """Replay stored logs then poll job store until done/error."""
    from src.trade.external_predictions_run_jobs import _get_job_record, reconcile_zombie_job

    async for chunk in _sse_event_stream(
        job_id=job_id,
        request=request,
        reconcile=reconcile_zombie_job,
        get_record=_get_job_record,
        frame=_index_prediction_run_sse_frame,
        log_to_events=_external_predictions_log_to_events,
        terminal=_external_predictions_terminal,
        heartbeat=_external_predictions_heartbeat,
        heartbeat_seconds=_INDEX_PREDICTION_RUN_HEARTBEAT_SECONDS,
        poll_seconds=_INDEX_PREDICTION_RUN_POLL_SECONDS,
    ):
        yield chunk


def _external_predictions_refresh_stream_response(job_id: str, request: Request) -> StreamingResponse:
    return StreamingResponse(
        _external_predictions_refresh_event_stream(job_id, request),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@trade_router.post(
    "/index-prediction/external-predictions/refresh/start",
    response_model=ExternalPredictionsRefreshStartResponse,
    status_code=202,
)
def start_external_predictions_refresh(
    body: ExternalPredictionsRefreshRequest,
    _auth: None = Depends(require_local_or_auth),
) -> ExternalPredictionsRefreshStartResponse:
    """Queue external-predictions refresh and return a trackable job_id."""
    from src.trade.hub_bridge import ensure_trade_stack_path

    ensure_trade_stack_path()
    job_id, job_status, reused = _kick_external_predictions_refresh(body)
    return ExternalPredictionsRefreshStartResponse(
        job_id=job_id,
        job_status=job_status,
        reused=reused,
    )


@trade_router.get(
    "/index-prediction/external-predictions/refresh/active",
    response_model=ExternalPredictionsRefreshActiveResponse,
)
def get_active_external_predictions_refresh(
    ticker: str = "NIFTY",
    horizon_days: int = 14,
    _auth: None = Depends(require_local_or_auth),
) -> ExternalPredictionsRefreshActiveResponse:
    from src.trade.external_predictions_run_jobs import get_active_job

    snap = get_active_job(ticker, horizon_days=horizon_days)
    if snap is None:
        return ExternalPredictionsRefreshActiveResponse(status="ok", job=None)
    return ExternalPredictionsRefreshActiveResponse(status="ok", job=snap)


@trade_router.get(
    "/index-prediction/external-predictions/refresh/{job_id}",
    response_model=ExternalPredictionsRefreshJobResponse,
)
def get_external_predictions_refresh_job(
    job_id: str,
    _auth: None = Depends(require_local_or_auth),
) -> ExternalPredictionsRefreshJobResponse:
    from src.trade.external_predictions_run_jobs import get_job, job_id_valid

    if not job_id_valid(job_id):
        raise HTTPException(status_code=400, detail="invalid job_id")
    snap = get_job(job_id)
    if snap is None:
        raise HTTPException(status_code=404, detail=f"job {job_id} not found")
    return ExternalPredictionsRefreshJobResponse(status="ok", job=snap)


@trade_router.get("/index-prediction/external-predictions/refresh/{job_id}/stream")
async def stream_external_predictions_refresh_job(
    job_id: str,
    request: Request,
    _auth: None = Depends(require_local_or_auth),
) -> StreamingResponse:
    """SSE: replay pipeline logs and stream until the refresh terminates."""
    from src.trade.external_predictions_run_jobs import _get_job_record, job_id_valid

    if not job_id_valid(job_id):
        raise HTTPException(status_code=400, detail="invalid job_id")
    if await asyncio.to_thread(_get_job_record, job_id) is None:
        raise HTTPException(status_code=404, detail=f"job {job_id} not found")
    return _external_predictions_refresh_stream_response(job_id, request)


@trade_router.get(
    "/index-prediction/external-predictions/sources",
    response_model=ExternalPredictionSourcesResponse,
)
def list_external_prediction_sources(
    ticker: str = "NIFTY",
    watchlisted_only: bool = False,
    _auth: None = Depends(require_local_or_auth),
) -> ExternalPredictionSourcesResponse:
    key = _ticker_key(ticker)
    with bad_gateway_on_error(logger, "list external prediction sources failed"):
        from src.trade.hub_bridge import ensure_trade_stack_path
        from trade_integrations.dataflows.index_research.external_predictions.source_registry import (
            load_registry,
        )

        ensure_trade_stack_path()
        registry = load_registry()
        if watchlisted_only:
            registry = [s for s in registry if s.watchlisted]
        return ExternalPredictionSourcesResponse(
            status="ok",
            ticker=key,
            sources=[s.to_dict() for s in registry],
        )


@trade_router.post(
    "/index-prediction/external-predictions/sources",
    response_model=ExternalPredictionSourcesResponse,
)
def add_external_prediction_source(
    body: ExternalPredictionSourceRequest,
    ticker: str = "NIFTY",
    _auth: None = Depends(require_local_or_auth),
) -> ExternalPredictionSourcesResponse:
    key = _ticker_key(ticker)
    try:
        from src.trade.hub_bridge import ensure_trade_stack_path
        from trade_integrations.dataflows.index_research.external_predictions.source_registry import (
            add_source_to_watchlist,
            load_registry,
        )

        ensure_trade_stack_path()
        from trade_integrations.dataflows.index_research.external_predictions.source_validation import (
            validate_user_source_request,
        )

        domains, entry_urls, validation_error = validate_user_source_request(
            display_name=body.display_name,
            domains=body.domains,
            entry_urls=body.entry_urls,
            require_entry_urls=not bool(body.id and str(body.id).strip()),
        )
        if validation_error:
            raise HTTPException(status_code=400, detail=validation_error)
        add_source_to_watchlist(
            source_id=body.id,
            display_name=body.display_name,
            domains=domains,
            entry_urls=entry_urls,
            search_queries=body.search_queries,
            kind=body.kind,
            added_by="user",
        )
        registry = load_registry()
        return ExternalPredictionSourcesResponse(
            status="ok",
            ticker=key,
            sources=[s.to_dict() for s in registry],
            message=f"Added {body.display_name} to watchlist",
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("add external prediction source failed")
        raise HTTPException(status_code=502, detail=redacted_error_detail(exc)) from exc


@trade_router.delete(
    "/index-prediction/external-predictions/sources/{source_id}",
    response_model=ExternalPredictionSourcesResponse,
)
def remove_external_prediction_source(
    source_id: str,
    ticker: str = "NIFTY",
    _auth: None = Depends(require_local_or_auth),
) -> ExternalPredictionSourcesResponse:
    key = _ticker_key(ticker)
    with bad_gateway_on_error(logger, "remove external prediction source failed"):
        from src.trade.hub_bridge import ensure_trade_stack_path
        from trade_integrations.dataflows.index_research.external_predictions.source_registry import (
            load_registry,
            remove_source_from_watchlist,
        )

        ensure_trade_stack_path()
        removed = remove_source_from_watchlist(source_id)
        registry = load_registry()
        if not removed:
            return ExternalPredictionSourcesResponse(
                status="forbidden",
                ticker=key,
                sources=[s.to_dict() for s in registry],
                message=f"Cannot remove source {source_id}",
            )
        return ExternalPredictionSourcesResponse(
            status="ok",
            ticker=key,
            sources=[s.to_dict() for s in registry],
            message=f"Removed {source_id} from watchlist",
        )


@trade_router.get(
    "/index-prediction/external-predictions/discover",
    response_model=ExternalPredictionSourcesResponse,
)
def discover_external_prediction_sources(
    ticker: str = "NIFTY",
    limit: int = 12,
    _auth: None = Depends(require_local_or_auth),
) -> ExternalPredictionSourcesResponse:
    key = _ticker_key(ticker)
    with bad_gateway_on_error(logger, "discover external prediction sources failed"):
        from src.trade.hub_bridge import ensure_trade_stack_path
        from trade_integrations.dataflows.index_research.external_predictions.discover import (
            discover_external_sources,
        )
        from trade_integrations.dataflows.index_research.external_predictions.source_registry import (
            load_registry,
        )

        ensure_trade_stack_path()
        candidates = discover_external_sources(limit=limit, persist=True)
        registry = load_registry()
        return ExternalPredictionSourcesResponse(
            status="ok",
            ticker=key,
            sources=[s.to_dict() for s in registry if not s.watchlisted],
            candidates=candidates,
        )


@trade_router.post(
    "/index-prediction/external-predictions/sources/{source_id}/approve-path",
    response_model=ExternalPredictionSourcesResponse,
)
def approve_external_prediction_path(
    source_id: str,
    ticker: str = "NIFTY",
    horizon_days: int = 14,
    _auth: None = Depends(require_local_or_auth),
) -> ExternalPredictionSourcesResponse:
    """Promote auto-saved navigation path to user-approved for fast-path replay."""
    key = _ticker_key(ticker)
    sid = (source_id or "").strip().lower()
    if not sid:
        raise HTTPException(status_code=400, detail="source_id required")
    try:
        from src.trade.hub_bridge import ensure_trade_stack_path
        from trade_integrations.dataflows.index_research.external_predictions.path_store import (
            approve_path,
        )
        from trade_integrations.dataflows.index_research.external_predictions.source_registry import (
            load_registry,
        )

        ensure_trade_stack_path()
        promoted = approve_path(sid, horizon_days=horizon_days)
        registry = load_registry()
        if promoted is None:
            return ExternalPredictionSourcesResponse(
                status="not_found",
                ticker=key,
                sources=[s.to_dict() for s in registry],
                message=f"No saved path to approve for {sid} ({horizon_days}d)",
            )
        return ExternalPredictionSourcesResponse(
            status="ok",
            ticker=key,
            sources=[s.to_dict() for s in registry],
            message=f"Approved navigation path for {sid} ({horizon_days}d)",
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("approve external prediction path failed for %s", sid)
        raise HTTPException(status_code=502, detail=redacted_error_detail(exc)) from exc


@trade_router.get("/index-prediction/external-predictions/sources/{source_id}/thumbnail")
def get_external_prediction_thumbnail(
    source_id: str,
    ticker: str = "NIFTY",
    run_id: str | None = None,
    _auth: None = Depends(require_local_or_auth),
):
    key = _ticker_key(ticker)
    sid = (source_id or "").strip().lower()
    if not sid:
        raise HTTPException(status_code=400, detail="source_id required")
    try:
        from src.trade.hub_bridge import ensure_trade_stack_path
        from trade_integrations.dataflows.index_research.external_predictions.screenshot_utils import (
            resolve_thumbnail_path,
        )

        ensure_trade_stack_path()
        path = resolve_thumbnail_path(symbol=key, source_id=sid, run_id=run_id)
        if path is None or not path.is_file():
            raise HTTPException(status_code=404, detail="thumbnail not found")
        return FileResponse(path, media_type="image/jpeg")
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("external prediction thumbnail failed for %s", sid)
        raise HTTPException(status_code=502, detail=redacted_error_detail(exc)) from exc
