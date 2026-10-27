"""Index-prediction light-refresh routes (refresh gate and refresh) with their in-flight lock. Split out of trade_routes.py; the routes register on its trade_router."""

from __future__ import annotations

import threading
from fastapi import Depends, HTTPException
from src.api.helpers import redacted_error_detail
from src.api.security import require_local_or_auth
from trade_integrations.market_registry import market_for_ticker
from typing import Any, Dict
from .trade_routes import IndexPredictionRefreshResponse, RefreshIndexPredictionRequest, _ticker_key, logger, trade_router


_LIGHT_REFRESH_LOCK = threading.Lock()


def _light_refresh_skip_reason(key: str) -> str | None:
    """Why a non-forced poll must not run now: `replay_armed`, `market_closed` or `simulator_unavailable`."""
    from src.trade.hub_bridge import ensure_trade_stack_path

    ensure_trade_stack_path()
    from nautilus_openalgo_bridge.market_hours import is_market_open_for_market
    from trade_integrations.dataflows.news_hub_bridge.replay_gate import is_replaying
    from trade_integrations.market_registry import market_for_ticker

    from trade_integrations.stock_simulator.integration import simulator_status

    market = market_for_ticker(key)
    market_open = is_market_open_for_market(market)
    try:
        # The simulator also replays by itself while the market is shut (`market_closed_fallback`): that is
        # a closed market, not an armed replay, and the page must say so.
        if is_replaying(market) and (market_open or simulator_status().get("reason") == "explicit"):
            return "replay_armed"
    except Exception:  # simulator cannot answer: polling blind during a possible replay is worse than skipping
        logger.warning("light refresh skipped: replay state unknown", exc_info=True)
        return "simulator_unavailable"
    return None if market_open else "market_closed"


@trade_router.get("/index-prediction/refresh-gate")
def get_index_prediction_refresh_gate(
    ticker: str = "NIFTY",
    _auth: None = Depends(require_local_or_auth),
) -> Dict[str, Any]:
    """The same gate the refresh POST applies, read-only: lets the page skip the POST when it would no-op."""
    key = _ticker_key(ticker)
    return {"ticker": key, "skip_reason": _light_refresh_skip_reason(key)}


@trade_router.post("/index-prediction/refresh", response_model=IndexPredictionRefreshResponse)
def refresh_index_prediction(
    body: RefreshIndexPredictionRequest,
    _auth: None = Depends(require_local_or_auth),
) -> IndexPredictionRefreshResponse:
    """Lightweight macro + cached-constituent refresh for live polling.

    A non-forced poll runs only during the market's session, never while a replay is armed, and one at a
    time; otherwise it answers `status: skipped` with the reason and writes nothing (merely opening the
    prediction page must not mutate the artifact)."""
    key = _ticker_key(body.ticker)
    if not body.force:
        skip = _light_refresh_skip_reason(key)
        if skip:
            return IndexPredictionRefreshResponse(status="skipped", ticker=key, reason=skip)
    if not _LIGHT_REFRESH_LOCK.acquire(blocking=False):
        return IndexPredictionRefreshResponse(status="skipped", ticker=key, reason="refresh_in_flight")
    try:
        from trade_integrations.dataflows.index_research.light_refresh import run_index_light_refresh
        from src.trade.hub_bridge import _index_doc_to_panel, ensure_trade_stack_path

        ensure_trade_stack_path()
        doc, reason = run_index_light_refresh(
            key,
            horizon_days=body.horizon_days,
            force=body.force,
            poll_mode=True,
        )
        artifact = _index_doc_to_panel(doc)
        artifact["asset_type"] = "index"
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("index-prediction refresh failed for %s", key)
        raise HTTPException(status_code=502, detail=redacted_error_detail(exc)) from exc
    finally:
        _LIGHT_REFRESH_LOCK.release()

    return IndexPredictionRefreshResponse(
        status="ok",
        ticker=key,
        reason=reason,
        artifact=artifact,
    )
