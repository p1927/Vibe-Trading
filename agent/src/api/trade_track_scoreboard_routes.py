"""Track-scoreboard read and refresh routes. Split out of trade_routes.py; the routes register on its trade_router."""

from __future__ import annotations

from fastapi import Depends, HTTPException
from src.api.helpers import redacted_error_detail
from src.api.security import require_local_or_auth
from src.trade import scoreboard_refresh
from typing import Any, Dict
from .trade_routes import IndexTrackScoreboardResponse, _ticker_key, logger, trade_router


@trade_router.get("/index-prediction/track-scoreboard", response_model=IndexTrackScoreboardResponse)
def get_index_track_scoreboard(
    ticker: str = "NIFTY",
    days: int = 365,
    horizon_days: int | None = None,
    _auth: None = Depends(require_local_or_auth),
) -> IndexTrackScoreboardResponse:
    """The cached per-track scoreboard, with `as_of`, `stale`/`needs_refresh` and the latest recompute job.

    A GET never recomputes (the walk-forward takes minutes; an inline recompute never returned in 170 s,
    D198): refresh is `POST /index-prediction/track-scoreboard/refresh`."""
    key = _ticker_key(ticker)
    try:
        from trade_integrations.dataflows.index_research.prediction_algorithms.evaluator.scoreboard import (
            load_scoreboard,
            normalize_scoreboard_report,
            scoreboard_needs_refresh,
        )
        from trade_integrations.dataflows.index_research.prediction_algorithms.promotion import (
            enrich_scoreboard_with_live,
        )
        from src.trade.hub_bridge import ensure_trade_stack_path

        ensure_trade_stack_path()
        history_days = scoreboard_refresh.history_days(key, days)
        report = normalize_scoreboard_report(load_scoreboard(key) or {"status": "empty", "ticker": key})
        report["needs_refresh"] = scoreboard_needs_refresh(report, horizon_days=horizon_days, history_days=history_days)
        report["stale"] = report["needs_refresh"]
        report = enrich_scoreboard_with_live(report, ticker=key)
        report["refresh_job"] = scoreboard_refresh.current_job()
        return IndexTrackScoreboardResponse(status=str(report.get("status") or "ok"), ticker=key, report=report)
    except Exception as exc:
        logger.exception("index-prediction track-scoreboard failed for %s", key)
        raise HTTPException(status_code=502, detail=redacted_error_detail(exc)) from exc


@trade_router.post("/index-prediction/track-scoreboard/refresh")
def refresh_index_track_scoreboard(
    ticker: str = "NIFTY",
    days: int = 365,
    horizon_days: int | None = None,
    eval_step: int = 5,
    _auth: None = Depends(require_local_or_auth),
) -> Dict[str, Any]:
    """Start the walk-forward recompute in the background (single-flight) and return its job id.

    A run already in flight is returned as-is (`started: false`); poll GET track-scoreboard's
    `report.refresh_job` until `state` is `done`/`failed`, then read the new report."""
    started, job = scoreboard_refresh.start_refresh(_ticker_key(ticker), days, horizon_days, eval_step)
    return {"status": "ok", "started": started, "job": job}
