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

from fastapi import APIRouter
from pydantic import BaseModel

advisory_router = APIRouter(prefix="/board/advisory", tags=["board"])


class ApproveCandidateRequest(BaseModel):
    ticker: str
    strategy_name: str | None = None


def _orders_for_strategy(widget: Dict[str, Any], strategy_name: str | None) -> list[Dict[str, Any]]:
    """Pull the execute_basket order list for `strategy_name` out of the widget's
    per-strategy `strategy_variants`, falling back to the widget's own top-level
    (recommended-strategy) implementation steps when no name matches — same variant
    shape/lookup `TradePlanWidgetCard.tsx`'s `resolveVariant` uses in the chat UI."""
    from trade_integrations.bridge.hub_context import normalize_strategy_key

    def _orders_from_steps(steps: Any) -> list[Dict[str, Any]]:
        for step in steps or []:
            if isinstance(step, dict) and step.get("action") == "execute_basket":
                orders = step.get("payload", {}).get("orders")
                if orders:
                    return orders
        return []

    variants = widget.get("strategy_variants") or {}
    if strategy_name:
        variant = variants.get(strategy_name) or variants.get(normalize_strategy_key(strategy_name))
        if variant:
            orders = _orders_from_steps(variant.get("implementation_steps"))
            if orders:
                return orders

    return _orders_from_steps(widget.get("implementation_steps"))


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
