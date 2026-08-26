"""HTTP routes for the dual-board advisory/agent UI
(2026-08-25-advisory-board-live-prediction-approve-reject-ui) — Board 1 (Advisory): live
market-prediction state and which option strategies would currently be profitable, for a
human to approve/reject on the go. No agent involved.

`candidates` is a read-only live-scoring layer over `strategy_rank.score_ranked_strategies`
(local cached-research-JSON read + payoff arithmetic, already invoked synchronously per agent
turn in `autonomous_agents.turns`) and `prediction_ledger_bridge.get_live_confidence` (single
parquet read) — both cheap enough to poll. `approve` only builds/persists the same
`trade_plan.widget` shape the chat UI already produces; it does not place any order itself —
the frontend calls the existing `POST /trade/execute-basket` route with the returned
`widget_id`, which (for a widget with no `agent_id`) already writes the `outcome_ledger` ENTER
row via its `intent_source="manual_ui"` branch. Same pattern as `board_routes.py`: deferred
imports of `trade_integrations.*` inside each handler body, plain dict returns.
"""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

advisory_router = APIRouter(prefix="/board/advisory", tags=["board"])


class ApproveCandidateRequest(BaseModel):
    ticker: str
    strategy_name: str | None = None


def _orders_for_strategy(widget: Dict[str, Any], strategy_name: str | None) -> list[Dict[str, Any]]:
    """Pull the execute_basket order list for `strategy_name` out of the widget's
    per-strategy `strategy_variants`. Falls back to the widget's own top-level
    (recommended-strategy) implementation steps only when the caller didn't name a
    strategy at all. When a `strategy_name` IS given but doesn't resolve in
    `strategy_variants`, fails loudly instead of silently substituting whatever
    strategy the doc happens to recommend — see
    2026-08-27-advisory-approve-executes-wrong-strategy-orders: that silent fallback
    let approving one strategy submit a completely different strategy's real orders."""
    from trade_integrations.bridge.hub_context import normalize_strategy_key

    def _orders_from_steps(steps: Any) -> list[Dict[str, Any]]:
        for step in steps or []:
            if isinstance(step, dict) and step.get("action") == "execute_basket":
                orders = step.get("payload", {}).get("orders")
                if orders:
                    return orders
        return []

    if not strategy_name:
        return _orders_from_steps(widget.get("implementation_steps"))

    variants = widget.get("strategy_variants") or {}
    variant = variants.get(strategy_name) or variants.get(normalize_strategy_key(strategy_name))
    if not variant:
        raise HTTPException(
            status_code=400,
            detail=(
                f"strategy_name {strategy_name!r} not found among this widget's "
                f"strategy_variants ({sorted(variants.keys())}); refusing to fall back "
                "to a different strategy's orders"
            ),
        )
    orders = _orders_from_steps(variant.get("implementation_steps"))
    if not orders:
        raise HTTPException(
            status_code=400,
            detail=f"strategy_name {strategy_name!r} resolved but has no execute_basket orders",
        )
    return orders


def _watchlist() -> list[str]:
    from trade_integrations.autonomous_agents.trading_config import get_agent_trading_config

    watchlist = list(get_agent_trading_config().watchlist)
    return watchlist or ["NIFTY"]


@advisory_router.get("/candidates")
def get_advisory_candidates() -> Dict[str, Any]:
    """Per watchlist ticker: live index-direction confidence plus ranked, currently-
    profitable option strategy candidates."""
    from trade_integrations.autonomous_agents.strategy_rank import score_ranked_strategies
    from trade_integrations.dataflows.prediction_ledger_bridge import get_live_confidence

    result: Dict[str, Any] = {}
    for ticker in _watchlist():
        result[ticker] = {
            "confidence": get_live_confidence(ticker),
            "candidates": score_ranked_strategies(ticker),
        }
    return result


@advisory_router.get("/candidate-detail")
def get_advisory_candidate_detail(ticker: str, strategy_name: str) -> Dict[str, Any]:
    """Pre-approval detail payload for one candidate: the doc-level prediction
    explanation (view/iv_regime/expected_move_pct/confidence/signals) plus that
    candidate's legs/rationale, pulled out of the same `strategy_variants` widget shape
    `POST /board/advisory/approve` already builds (`_orders_for_strategy` reuses the
    identical lookup) -- no new pricing/scoring here. The richer per-candidate numbers
    (payoff curve, risk/reward, predicted trajectory) already reach the frontend via
    `GET /board/advisory/candidates`'s `score_ranked_strategies` rows and are not
    recomputed on this route.
    (2026-08-27-advisory-candidate-detail-view)"""
    from trade_integrations.bridge.hub_context import normalize_strategy_key
    from trade_integrations.dataflows.options_research.widget_payload import build_options_trade_widget

    widget = build_options_trade_widget(ticker, refresh=False)
    variants = widget.get("strategy_variants") or {}
    variant = variants.get(strategy_name) or variants.get(normalize_strategy_key(strategy_name))
    recommended = (variant or {}).get("recommended") or {}
    return {
        "ok": True,
        "ticker": ticker,
        "strategy_name": strategy_name,
        "found": variant is not None,
        "spot": widget.get("spot"),
        "expiry": widget.get("expiry"),
        "as_of": widget.get("as_of"),
        "prediction": widget.get("prediction") or {},
        "legs": recommended.get("legs") or [],
        "rationale": recommended.get("rationale"),
    }


@advisory_router.get("/forecast")
def get_advisory_forecast() -> Dict[str, Any]:
    """Most recently pushed quantile-conformal Nifty range forecast (per-horizon
    quantiles/coverage from `quantile_forecast`), read back from the prediction ledger
    rather than recomputed live — `forecast_nifty_range` trains real quantile models on
    every call, too expensive for a page-load read; the scheduled
    `nifty-quantile-forecast-ledger-push` job is what keeps this fresh. Returns
    `{"status": "none"}` if nothing has been pushed yet."""
    from trade_integrations.quantile_forecast.ledger_bridge import load_latest_pushed_forecast

    forecast = load_latest_pushed_forecast()
    if forecast is None:
        return {"status": "none", "forecast": None}
    return {"status": "ok", "forecast": forecast}


@advisory_router.post("/approve")
def prepare_advisory_widget(body: ApproveCandidateRequest) -> Dict[str, Any]:
    """Build and persist a trade-plan widget for `ticker`, resolving `strategy_name`'s own
    order legs (falling back to the widget's recommended strategy) so the frontend can hand
    `widget_id`+`orders` to the existing `/trade/execute-basket` route. Does not place any
    order itself."""
    from trade_integrations.dataflows.options_research.widget_payload import (
        build_options_trade_widget,
    )
    from trade_integrations.trade_widgets.store import persist_trade_widget

    widget = build_options_trade_widget(body.ticker, refresh=False)
    persist_trade_widget(widget)
    orders = _orders_for_strategy(widget, body.strategy_name)
    return {"widget_id": widget.get("widget_id"), "orders": orders, "widget": widget}
