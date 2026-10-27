"""Execution routes: execution mode, charges, and the basket order route with its risk gate (the only order-placing route). Split out of trade_routes.py; the routes register on its trade_router."""

from __future__ import annotations

from fastapi import Depends, HTTPException
from pydantic import BaseModel, Field
from src.api.helpers import redacted_error_detail
from src.api.security import require_local_or_auth
from src.config.accessor import get_env_config
from trade_integrations.trade_widgets.store import load_trade_widget
from trade_integrations.ui_links import trade_ui_deep_link
from typing import Any, Dict, List
from .trade_routes import logger, trade_router


class ExecuteBasketRequest(BaseModel):
    widget_id: str | None = None
    orders: List[Dict[str, Any]] = Field(default_factory=list)
    strategy: str = "vibe_trade_plan"


class ExecuteBasketResponse(BaseModel):
    status: str
    results: List[Dict[str, Any]] = Field(default_factory=list)
    message: str = ""
    execution_mode: str = "live"


class TradeChargesRequest(BaseModel):
    legs: List[Dict[str, Any]] = Field(default_factory=list)
    spot: float | None = None
    broker_preset: str = "zerodha"
    include_exit: bool = True


class TradeChargesResponse(BaseModel):
    status: str
    charges: Dict[str, Any] = Field(default_factory=dict)
    message: str = ""


class ExecutionModeResponse(BaseModel):
    mode: str
    analyze_mode: bool
    paper_env: bool
    live_allowed: bool
    switch_url: str = ""


def _openalgo_switch_url() -> str:
    return trade_ui_deep_link(tab="openalgo")


def _resolve_execution_mode(analyze: bool, paper_env: bool) -> ExecutionModeResponse:
    """OpenAlgo UI is authoritative; paper_env only blocks live from Vibe."""
    mode = "paper" if analyze else "live"
    live_allowed = not paper_env
    try:
        _openalgo_config()
        switch_url = _openalgo_switch_url()
    except HTTPException:
        switch_url = ""
    return ExecutionModeResponse(
        mode=mode,
        analyze_mode=analyze,
        paper_env=paper_env,
        live_allowed=live_allowed,
        switch_url=switch_url,
    )


def _openalgo_config() -> tuple[str, str]:
    host = get_env_config().trade.openalgo_host.rstrip("/")
    api_key = get_env_config().trade.openalgo_api_key.strip()
    if not api_key:
        raise HTTPException(
            status_code=503,
            detail="OPENALGO_API_KEY not configured for execution",
        )
    return host, api_key


def _paper_mode_env_enabled() -> bool:
    # The one parser (fail-safe: only an explicit off unlocks live execution); "on" or a typo used
    # to read as unlocked here while the Trade bridge read the same variable as locked.
    from trade_integrations.execution.default_profile import paper_mode_value_enabled

    return paper_mode_value_enabled(get_env_config().trade.openalgo_paper_mode)


def _openalgo_analyzer_status(host: str, api_key: str) -> bool:
    try:
        from trade_integrations.openalgo.rest_client import get_rest_client

        body = get_rest_client(host=host, api_key=api_key).post(
            "analyzer",
            {"apikey": api_key},
            timeout=15,
        )
    except RuntimeError as exc:
        logger.warning("OpenAlgo analyzer status failed: %s", exc)
        return False
    data = body.get("data") if isinstance(body.get("data"), dict) else body
    return bool(data.get("analyze_mode"))


def _assert_execution_allowed(analyze: bool) -> None:
    """Block live basket execution from Vibe when OPENALGO_PAPER_MODE safety lock is on."""
    if not analyze and _paper_mode_env_enabled():
        raise HTTPException(
            status_code=403,
            detail=(
                "Live execution is disabled (OPENALGO_PAPER_MODE=true). "
                "Switch OpenAlgo to Analyze mode, or set OPENALGO_PAPER_MODE=false in .env "
                "after you are ready for real orders."
            ),
        )


@trade_router.get("/execution-mode", response_model=ExecutionModeResponse)
def execution_mode(
    _auth: None = Depends(require_local_or_auth),
) -> ExecutionModeResponse:
    """Return OpenAlgo paper/live mode (toggle lives in OpenAlgo UI only)."""
    paper_env = _paper_mode_env_enabled()
    try:
        host, api_key = _openalgo_config()
        analyze = _openalgo_analyzer_status(host, api_key)
    except HTTPException:
        analyze = paper_env
    return _resolve_execution_mode(analyze, paper_env)


@trade_router.post("/charges", response_model=TradeChargesResponse)
def trade_charges(
    body: TradeChargesRequest,
    _auth: None = Depends(require_local_or_auth),
) -> TradeChargesResponse:
    """Compute per-leg and round-trip charges for adjusted strategy legs."""
    legs = list(body.legs or [])
    if not legs:
        raise HTTPException(status_code=400, detail="legs array required")
    try:
        from src.trade.hub_bridge import ensure_trade_stack_path

        ensure_trade_stack_path()
        from trade_integrations.dataflows.options_research.payoff_charges import (
            calculate_charges,
            calculate_charges_with_exit,
        )

        spot = float(body.spot or 0.0)
        if body.include_exit and spot > 0:
            charges = calculate_charges_with_exit(
                legs,
                spot=spot,
                broker_preset=body.broker_preset,
            )
        else:
            charges = calculate_charges(legs, broker_preset=body.broker_preset)
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("trade charges failed: %s", exc)
        raise HTTPException(status_code=500, detail=redacted_error_detail(exc)) from exc
    return TradeChargesResponse(status="success", charges=charges)


def _assert_basket_passes_risk_gate(
    host: str, api_key: str, orders: list[dict], widget: dict, widget_id: str, agent_id: str
) -> str:
    """Refuse (HTTP 403/503) unless the one risk gate permits ``orders`` (D453); returns the principal
    id the ledger books them under. Fail closed: margin or lot size that cannot be read is a refusal."""
    from nautilus_openalgo_bridge.openalgo_client import BridgeOpenAlgoClient
    from nautilus_openalgo_bridge.preflight import _margin_positions_from_orders
    from trade_integrations.autonomous_agents.mandate_enforcer import MandateViolation
    from trade_integrations.autonomous_agents.store import get_agent
    from trade_integrations.risk.manual_operator import assert_manual_order_permitted

    for order in orders:
        try:
            quantity = int(float(order.get("quantity")))
        except (TypeError, ValueError, OverflowError):
            quantity = 0
        if quantity <= 0 or not str(order.get("symbol") or "").strip():
            # The gate's lot count skips a leg it cannot read; a leg that is not a positive-size order on a named symbol is no order to place.
            raise HTTPException(status_code=400, detail=f"Order has no symbol or no positive quantity: {order!r}"[:300])
    agent = None
    if agent_id:
        agent = get_agent(agent_id)
        if agent is None:
            raise HTTPException(status_code=403, detail=f"Widget's agent {agent_id} has no record: no mandate to check")
    client = BridgeOpenAlgoClient(host=host, api_key=api_key)
    try:
        margin = client.calculate_margin(_margin_positions_from_orders(orders))
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=f"Margin check failed: {redacted_error_detail(exc)}") from exc
    if margin is None:
        raise HTTPException(status_code=503, detail="Margin unavailable: the order cannot be sized against the risk limits")
    try:
        return assert_manual_order_permitted(
            orders, margin_inr=float(margin), widget_id=widget_id, lot_size_lookup=client.lot_size, agent=agent
        )
    except MandateViolation as exc:
        raise HTTPException(status_code=403, detail=f"Order refused by the risk gate ({exc.code}): {exc}") from exc
    except Exception as exc:
        logger.warning("risk gate check failed for widget %s: %s", widget_id, exc)
        raise HTTPException(status_code=503, detail=f"Risk gate unavailable: {redacted_error_detail(exc)}") from exc


@trade_router.post("/execute-basket", response_model=ExecuteBasketResponse)
def execute_basket(
    body: ExecuteBasketRequest,
    _auth: None = Depends(require_local_or_auth),
) -> ExecuteBasketResponse:
    """Place a multi-leg basket order via OpenAlgo REST (after the human confirms in the widget).

    The human's one manual order path (D432). It names a widget and passes the SAME risk gate every
    agent order passes (D453): the manual operator's own conservative limits, or a non-bridge
    agent widget's agent's mandate. No widget, no order.
    """
    orders = list(body.orders or [])
    if not body.widget_id:
        raise HTTPException(status_code=400, detail="widget_id required: a basket is placed from a trade-plan widget")
    widget = load_trade_widget(body.widget_id)
    if widget is None:
        raise HTTPException(status_code=404, detail="Widget not found")
    # Checked whether the orders come from the widget or from the request body: a bridge agent's
    # plan only executes through its approval flow, so naming its widget must not unlock a
    # free-form order list.
    agent_id = str(widget.get("autonomous_agent_id") or widget.get("agent_id") or "").strip()
    if agent_id:
        from trade_integrations.execution.enforce import is_bridge_autonomous_agent

        if is_bridge_autonomous_agent(agent_id):
            raise HTTPException(
                status_code=403,
                detail="Direct execute-basket blocked for autonomous agents — use plan approval flow",
            )
    if not orders:
        for step in widget.get("implementation_steps") or []:
            if step.get("action") == "execute_basket" and step.get("payload"):
                orders = (step["payload"] or {}).get("orders") or []
                break
    if not orders:
        raise HTTPException(status_code=400, detail="No orders to execute")

    host, api_key = _openalgo_config()
    paper_env = _paper_mode_env_enabled()
    analyze = False
    if paper_env:
        from trade_integrations.execution.context_verify import ensure_paper_execution_ready
        from trade_integrations.execution.openalgo_client import OpenAlgoClient

        client = OpenAlgoClient(host=host, api_key=api_key)
        ctx = ensure_paper_execution_ready(client, env_paper_lock=True)
        analyze = ctx.analyze_mode
    else:
        from trade_integrations.openalgo.market_context import fetch_market_context

        try:
            ctx = fetch_market_context(host=host, api_key=api_key)
            analyze = ctx.analyze_mode
        except Exception:
            analyze = _openalgo_analyzer_status(host, api_key)
    _assert_execution_allowed(analyze)
    execution_mode = "paper" if analyze else "live"
    manual_principal_id = _assert_basket_passes_risk_gate(host, api_key, orders, widget, body.widget_id, agent_id)

    payload = {"apikey": api_key, "strategy": body.strategy, "orders": orders}
    try:
        from trade_integrations.openalgo.rest_client import get_rest_client

        body_json = get_rest_client(host=host, api_key=api_key).post(
            "basketorder",
            payload,
            timeout=45,
        )
    except RuntimeError as exc:
        logger.warning("OpenAlgo basket order failed: %s", exc)
        raise HTTPException(status_code=502, detail=f"OpenAlgo request failed: {redacted_error_detail(exc)}") from exc

    if body_json.get("status") == "error":
        raise HTTPException(
            status_code=502,
            detail=body_json.get("message") or str(body_json),
        )

    results = body_json.get("results") or body_json.get("data") or []
    if isinstance(results, dict):
        results = [results]
    if not isinstance(results, list):
        results = []

    if body.widget_id:
        widget = load_trade_widget(body.widget_id)
        if widget:
            just_recorded_execution_id: str | None = None
            try:
                from src.trade.hub_bridge import ensure_trade_stack_path

                ensure_trade_stack_path()
                from trade_integrations.monitor.execution_ledger import record_execution_from_widget

                recorded_entry = record_execution_from_widget(
                    widget,
                    results,
                    execution_mode=execution_mode,
                    agent_id=manual_principal_id,
                )
                just_recorded_execution_id = str(recorded_entry.get("execution_id") or "") or None
            except Exception:
                logger.warning(
                    "Failed to record execution ledger for widget %s",
                    body.widget_id,
                    exc_info=True,
                )

            # Tag every order placed through this route in outcome_ledger.py so
            # Board 1 (Advisory) has *any* downstream analytics at all — see
            # 2026-08-25-manual-recommendation-to-order-path-audit: before this,
            # a manual order placed from the selector/chat-widget UI never wrote
            # an ENTER row here, so its eventual CLOSE (if ever detected via
            # `execution_ledger.py`'s stale-position reconciliation) had no
            # matching ENTER to pair with. `intent_source="manual_ui"` is a new,
            # distinct tag — deliberately not `"execution_ledger"` (that tag
            # means "closed via reconciliation," not "opened by a human," and
            # is a mixed bag that also fires for some agent-opened positions;
            # see that audit item for the evidence). A widget carrying a
            # non-bridge agent's `agent_id` (any bridge agent's widget was
            # already 403'd above) is tagged `"vibe_basket"` instead — the
            # same tag the dedicated MCP execution path
            # (`autonomous_agents/execution_actions.py`) uses for agent-placed
            # ENTERs — rather than being left completely untagged, see
            # 2026-08-25-non-bridge-agent-widget-execute-basket-untagged.
            agent_id_on_widget = str(widget.get("autonomous_agent_id") or widget.get("agent_id") or "").strip()
            underlying = str(widget.get("underlying") or "").strip()

            # Persist this strategy's risk/profit-at-entry to OpenAlgo's portfolio ledger
            # (StrategyRiskProfile) now that its basket order has actually gone in — see
            # .claude/backlog/items/2026-08-26-selector-not-writing-strategy-risk-profile.md.
            # `set_strategy_risk_profile`'s own `/riskprofile` REST endpoint had zero
            # production callers before this: module 5's selector computes max_loss/max_profit
            # per candidate already (carried on the widget's `recommended` block by
            # `/options/india/selector/prepare-widget`), but nothing downstream ever wrote it
            # to the ledger — so module 9's capital-at-risk rollup only ever reflected manually
            # entered sandbox trades. `max_loss` is a PnL figure (negative), `max_risk` the
            # ledger's own convention is a non-negative magnitude — same `abs()` the payoff
            # engine's own callers already apply. Best-effort: a failure here must not undo an
            # order that has already been placed, so it's caught and logged like every other
            # post-execution side effect in this block.
            recommended = widget.get("recommended") or {}
            max_loss = recommended.get("max_loss")
            if body.strategy and max_loss is not None:
                try:
                    from trade_integrations.openalgo.rest_client import get_rest_client

                    get_rest_client(host=host, api_key=api_key).post(
                        "riskprofile",
                        {
                            "apikey": api_key,
                            "strategy": body.strategy,
                            "max_risk": abs(float(max_loss)),
                            "max_profit": recommended.get("max_profit"),
                        },
                        timeout=15,
                    )
                except Exception:
                    logger.warning(
                        "Failed to record strategy risk profile for widget %s",
                        body.widget_id,
                        exc_info=True,
                    )

            if underlying:
                try:
                    from trade_integrations.autonomous_agents.outcome_ledger import append_outcome

                    append_outcome(
                        symbol=underlying,
                        strategy=(widget.get("recommended") or {}).get("name"),
                        action="ENTER",
                        intent_source="vibe_basket" if agent_id_on_widget else "manual_ui",
                        widget_id=body.widget_id,
                        agent_id=agent_id_on_widget or None,
                    )
                except Exception:
                    logger.warning(
                        "Failed to record manual outcome_ledger entry for widget %s",
                        body.widget_id,
                        exc_info=True,
                    )
                try:
                    # Did the human's choice agree with the prediction ledger
                    # ([[2026-08-25-manual-trades-no-shadow-alignment-check]]); this route is the one
                    # manual order path (the agent tool `execute_basket` refuses agent-less widgets),
                    # so the check lives here. An agent-less comparison row carries agent_id "".
                    from trade_integrations.autonomous_agents.shadow_strategy import record_shadow_alignment

                    record_shadow_alignment(
                        agent_id=agent_id_on_widget,
                        symbol=underlying,
                        decision="ENTER",
                        agent_strategy=(widget.get("recommended") or {}).get("name"),
                        agent_confidence=None,
                    )
                except Exception:
                    logger.warning(
                        "Failed to record shadow alignment for widget %s", body.widget_id, exc_info=True
                    )

            # Reconcile this underlying's open ledger entries right now, agent
            # or not — see
            # 2026-08-25-manual-widget-close-reconciliation-depends-on-agent-tick:
            # `close_ledger_entry` (and the outcome_ledger EXIT row it writes)
            # was previously only ever reached from an autonomous agent's own
            # status check or review tick, so with zero agents running a
            # manually-executed widget's position was never marked closed —
            # this call makes reconciliation happen off the human's own
            # action instead of depending on an agent tick that may never
            # fire. Narrowly scoped to this underlying (not a repo-wide
            # sweep) so it's cheap enough to run synchronously here.
            if underlying:
                try:
                    from trade_integrations.monitor.execution_ledger import reconcile_underlying

                    reconcile_underlying(underlying, exclude_execution_id=just_recorded_execution_id)
                except Exception:
                    logger.warning(
                        "Failed to reconcile execution ledger for %s",
                        underlying,
                        exc_info=True,
                    )

    mode_label = "Paper" if execution_mode == "paper" else "Live"
    return ExecuteBasketResponse(
        status=str(body_json.get("status") or "success"),
        results=results,
        message=str(body_json.get("message") or f"Basket submitted ({mode_label})"),
        execution_mode=execution_mode,
    )
