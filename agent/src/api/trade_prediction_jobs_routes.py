"""Scheduled index-prediction job controls (list, pause, resume, trigger, cancel), snapshots and the historical upcoming-events read. Split out of trade_routes.py; the routes register on its trade_router."""

from __future__ import annotations

from fastapi import Depends, HTTPException
from src.api.helpers import bad_gateway_on_error, redacted_error_detail
from src.api.security import require_local_or_auth
from typing import List, Optional
from .trade_routes import HistoricalUpcomingEvent, HistoricalUpcomingEventsResponse, IndexPredictionJobsResponse, IndexPredictionSnapshotsResponse, IndexUpcomingEventOutcome, _ticker_key, logger, trade_router


@trade_router.get("/index-prediction/jobs", response_model=IndexPredictionJobsResponse)
def get_index_prediction_jobs(
    _auth: None = Depends(require_local_or_auth),
) -> IndexPredictionJobsResponse:
    """List scheduled cron jobs that feed the prediction pipeline."""
    with bad_gateway_on_error(logger, "index-prediction jobs list failed"):
        from src.trade.index_prediction_jobs import list_index_prediction_jobs
        from src.trade.hub_bridge import ensure_trade_stack_path

        ensure_trade_stack_path()
        payload = list_index_prediction_jobs()
    return IndexPredictionJobsResponse(
        status=payload.get("status", "ok"),
        env=payload.get("env") or {},
        master_scheduler_env_enabled=bool(payload.get("master_scheduler_env_enabled")),
        master_scheduler_running=bool(payload.get("master_scheduler_running")),
        executor_is_running=bool(payload.get("executor_is_running")),
        news_pipeline=payload.get("news_pipeline") or {},
        index_quote=payload.get("index_quote") or {},
        jobs=payload.get("jobs") or [],
    )


@trade_router.post("/index-prediction/jobs/{job_id}/pause", response_model=IndexPredictionJobsResponse)
def pause_index_prediction_job(
    job_id: str,
    _auth: None = Depends(require_local_or_auth),
) -> IndexPredictionJobsResponse:
    """Pause one index prediction cron job's recurring schedule."""
    with bad_gateway_on_error(logger, "pause index job %s failed", job_id):
        from src.trade.index_prediction_jobs import pause_index_prediction_job
        from src.trade.hub_bridge import ensure_trade_stack_path

        ensure_trade_stack_path()
        payload = pause_index_prediction_job(job_id)
    if payload.get("status") == "error":
        return IndexPredictionJobsResponse(status="error", message=str(payload.get("message") or "not found"))
    return IndexPredictionJobsResponse(status="ok", job=payload.get("job"))


@trade_router.post("/index-prediction/jobs/{job_id}/resume", response_model=IndexPredictionJobsResponse)
def resume_index_prediction_job_route(
    job_id: str,
    _auth: None = Depends(require_local_or_auth),
) -> IndexPredictionJobsResponse:
    """Resume a paused index prediction cron job."""
    with bad_gateway_on_error(logger, "resume index job %s failed", job_id):
        from src.trade.index_prediction_jobs import resume_index_prediction_job
        from src.trade.hub_bridge import ensure_trade_stack_path

        ensure_trade_stack_path()
        payload = resume_index_prediction_job(job_id)
    if payload.get("status") == "error":
        return IndexPredictionJobsResponse(status="error", message=str(payload.get("message") or "not found"))
    return IndexPredictionJobsResponse(status="ok", job=payload.get("job"))


@trade_router.post("/index-prediction/jobs/{job_id}/trigger", response_model=IndexPredictionJobsResponse)
def trigger_index_prediction_job_route(
    job_id: str,
    _auth: None = Depends(require_local_or_auth),
) -> IndexPredictionJobsResponse:
    """Make an index prediction cron job due now (the Prediction tab's "run now").

    Does not enable a paused job — full schedule control lives on the
    Scheduler tab. This does not fire the job immediately or bypass D11
    admission — see ``trigger_index_prediction_job``'s docstring and
    .claude/backlog/items/2026-09-16-trigger-docstring-promises-immediate-dispatch.md.
    The returned ``job.due_jobs_ahead`` says how many other jobs were due
    at trigger time.
    """
    with bad_gateway_on_error(logger, "trigger index job %s failed", job_id):
        from src.trade.index_prediction_jobs import trigger_index_prediction_job
        from src.trade.hub_bridge import ensure_trade_stack_path

        ensure_trade_stack_path()
        payload = trigger_index_prediction_job(job_id)
    if payload.get("status") == "error":
        return IndexPredictionJobsResponse(status="error", message=str(payload.get("message") or "not found"))
    return IndexPredictionJobsResponse(status="ok", job=payload.get("job"))


@trade_router.post("/index-prediction/jobs/{job_id}/cancel", response_model=IndexPredictionJobsResponse)
def cancel_index_prediction_job_route(
    job_id: str,
    _auth: None = Depends(require_local_or_auth),
) -> IndexPredictionJobsResponse:
    """Cancel an index prediction cron job's currently running execution.

    Distinct from pause: only aborts the in-flight run, leaves the
    recurring schedule untouched.
    """
    with bad_gateway_on_error(logger, "cancel index job %s failed", job_id):
        from src.trade.index_prediction_jobs import cancel_index_prediction_job
        from src.trade.hub_bridge import ensure_trade_stack_path

        ensure_trade_stack_path()
        payload = cancel_index_prediction_job(job_id)
    if payload.get("status") == "error":
        return IndexPredictionJobsResponse(status="error", message=str(payload.get("message") or "not found"))
    return IndexPredictionJobsResponse(status="ok", job=payload.get("job"))


@trade_router.get("/index-prediction/snapshots", response_model=IndexPredictionSnapshotsResponse)
def get_index_prediction_snapshots(
    ticker: str = "NIFTY",
    limit: int = 10,
    _auth: None = Depends(require_local_or_auth),
) -> IndexPredictionSnapshotsResponse:
    """Return versioned index research snapshots."""
    key = _ticker_key(ticker)
    try:
        from trade_integrations.dataflows.index_research.snapshots import (
            list_index_research_snapshots,
        )

        snapshots = list_index_research_snapshots(key, limit=max(1, min(limit, 30)))
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("index-prediction snapshots failed for %s", key)
        raise HTTPException(status_code=502, detail=redacted_error_detail(exc)) from exc

    return IndexPredictionSnapshotsResponse(status="ok", ticker=key, snapshots=snapshots)


_HISTORICAL_UPCOMING_EVENT_FIELDS = {
    "date", "days_from_now", "event_type", "label", "symbol", "weight", "sector", "impact", "category",
}


@trade_router.get("/index-prediction/upcoming-events/history", response_model=HistoricalUpcomingEventsResponse)
def get_index_prediction_upcoming_events_history(
    start: Optional[str] = None,
    end: Optional[str] = None,
    ticker: str = "NIFTY",
    _auth: None = Depends(require_local_or_auth),
) -> HistoricalUpcomingEventsResponse:
    """Structural/calendar events (RBI/FOMC/earnings/macro) reconstructed from the
    `index_research/history` snapshot archive, each with a realized market outcome attached
    for dates that have already matured. See
    .claude/backlog/items/2026-09-06-scheduled-calendar-event-outcome-tracking-gap.md."""
    key = _ticker_key(ticker)
    with bad_gateway_on_error(logger, "index-prediction upcoming-events history failed for %s", key):
        from datetime import date as _date

        from trade_integrations.dataflows.index_research.event_outcomes import compute_event_market_outcome
        from trade_integrations.dataflows.index_research.snapshots import list_historical_upcoming_events

        today = _date.today().isoformat()
        rows = list_historical_upcoming_events(key, start=start, end=end)

        events: List[HistoricalUpcomingEvent] = []
        for row in rows:
            outcome = None
            event_date = row.get("date")
            if event_date and event_date < today:
                result = compute_event_market_outcome(event_date, symbol=key)
                if result:
                    outcome = IndexUpcomingEventOutcome(**result)
            filtered = {k: v for k, v in row.items() if k in _HISTORICAL_UPCOMING_EVENT_FIELDS}
            events.append(HistoricalUpcomingEvent(**filtered, market_outcome=outcome))

        return HistoricalUpcomingEventsResponse(status="ok", events=events)
