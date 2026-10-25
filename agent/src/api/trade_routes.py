"""Trade-stack widget persistence and OpenAlgo execution proxy for Vibe chat."""

from __future__ import annotations

import json
import logging
import re
import threading
import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field, field_validator

from src.api.helpers import is_unsafe_path_component, redacted_error_detail, safe_path_component
from src.api.security import require_event_stream_auth, require_local_or_auth
from src.config.accessor import get_env_config
from src.trade import scoreboard_refresh
from trade_integrations.market_registry import market_for_ticker
from trade_integrations.trade_widgets.store import load_trade_widget
from trade_integrations.ui_links import trade_ui_deep_link

logger = logging.getLogger(__name__)

# Query keys these routes join onto hub/artifact paths (``hub_dir / ticker / ...``,
# ``runs / recipe / f"{run_id}.json"``). Path params are checked for every route as well.
_PATHISH_QUERY_KEYS = ("ticker", "entity_id", "symbol", "exchange", "recipe", "run_id", "agent_id", "source_id")


async def _reject_unsafe_path_inputs(request: Request) -> None:
    """Router-wide: refuse a path param or path-joined query value that could leave its directory.

    ``../`` in a ticker/recipe/run_id reached ``mkdir``/``read_text`` in the hub and
    forecast-engine stores, and ``..`` as ``country``/``job_id`` was interpolated, unquoted,
    into the URL the stock_simulator client calls with its control token.
    """
    values = list(request.path_params.values())
    for key in _PATHISH_QUERY_KEYS:
        values.extend(request.query_params.getlist(key))
    if any(is_unsafe_path_component(str(v)) for v in values):
        raise HTTPException(status_code=400, detail="invalid path component")


trade_router = APIRouter(prefix="/trade", tags=["trade"], dependencies=[Depends(_reject_unsafe_path_inputs)])


def _ticker_key(raw: str | None, default: str = "NIFTY") -> str:
    """Canonical (upper-case, stripped) ticker/entity id from a request; 400 when path-unsafe.

    The default applies only when the parameter is absent: a present-but-blank `ticker=` is a 422, not a silent NIFTY.
    """
    if raw is not None and not raw.strip() and default:
        raise HTTPException(status_code=422, detail="ticker must not be empty (omit it for the default)")
    return safe_path_component((default if raw is None else raw).strip().upper(), "ticker")


def _query_day(raw: str | None, name: str) -> str | None:
    """A `YYYY-MM-DD` query day (an ISO datetime keeps its date part); 422 on anything else, None when absent."""
    if raw is None:
        return None
    try:
        return datetime.fromisoformat(raw.strip()).date().isoformat()
    except ValueError:
        raise HTTPException(status_code=422, detail=f"{name} must be a valid YYYY-MM-DD date, got {raw!r}") from None


def _query_window(since: str | None, until: str | None, day: str | None) -> tuple[str | None, str | None, str | None]:
    """Validated (since, until, day); 422 when `until` is before `since`."""
    s, u, d = _query_day(since, "since"), _query_day(until, "until"), _query_day(day, "day")
    if s and u and u < s:
        raise HTTPException(status_code=422, detail=f"until ({u}) is before since ({s})")
    return (since if s else None), (until if u else None), d


def _query_limit(limit: int, *, maximum: int) -> int:
    """`limit` must be >= 1 (422); above `maximum` it is clamped (the response count shows what was returned)."""
    if limit < 1:
        raise HTTPException(status_code=422, detail=f"limit must be >= 1, got {limit}")
    return min(limit, maximum)

_WIDGET_ID_RE = re.compile(r"(?:tp|ts|ti|ns)_[A-Z][A-Z0-9]*_[0-9a-f]{12}")
_WIDGET_ID_INLINE_RE = re.compile(r"((?:tp|ts|ti|ns)_[A-Z][A-Z0-9]*_[0-9a-f]{12})")
_WIDGET_TOOL_NAMES = frozenset(
    {
        "get_options_trade_widget",
        "mcp_openalgo_get_options_trade_widget",
        "get_stock_trade_widget",
        "mcp_openalgo_get_stock_trade_widget",
        "get_index_trade_widget",
        "mcp_openalgo_get_index_trade_widget",
        "get_news_scenario_widget",
        "mcp_openalgo_get_news_scenario_widget",
    }
)


def _widget_id_from_preview(preview: str) -> Optional[str]:
    """Extract widget id from tool_result preview (often escaped/truncated JSON)."""
    text = preview or ""
    inline = _WIDGET_ID_INLINE_RE.search(text)
    if inline:
        return inline.group(1)
    match = re.search(r'"widget_id"\s*:\s*"((?:tp|ts|ti|ns)_[^"]+)"', text)
    if match and _WIDGET_ID_RE.fullmatch(match.group(1)):
        return match.group(1)
    return None


def trade_plan_widget_frame_from_tool_result(event: Any) -> Optional[str]:
    """Build trade_plan.widget SSE frame from MCP tool_result."""
    data = getattr(event, "data", None)
    if getattr(event, "event_type", None) != "tool_result" or not isinstance(data, dict):
        return None
    tool = str(data.get("tool") or "")
    if tool not in _WIDGET_TOOL_NAMES or data.get("status") != "ok":
        return None
    preview = str(data.get("preview") or "")
    widget_id = _widget_id_from_preview(preview)
    if not widget_id:
        return None
    widget = load_trade_widget(widget_id)
    if widget is None:
        return None
    from src.session.events import SSEEvent

    frame = SSEEvent(
        event_type="trade_plan.widget",
        data=widget,
        session_id=getattr(event, "session_id", "") or "",
    )
    return frame.to_sse()


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


@trade_router.get("/widget/{widget_id}")
def get_widget(
    widget_id: str,
    _auth: None = Depends(require_local_or_auth),
) -> Dict[str, Any]:
    """Reload a persisted trade-plan widget."""
    widget = load_trade_widget(widget_id)
    if widget is None:
        raise HTTPException(status_code=404, detail="Widget not found")
    return widget


def _widget_staleness_from_report(report: Any) -> dict[str, Any]:
    return {
        "status": report.status,
        "reasons": list(report.reasons or []),
        "spot_drift_pct": report.spot_drift_pct,
    }


def _live_context_from_report(report: Any) -> dict[str, Any]:
    return {
        "spot": report.live_spot,
        "plan_spot": report.plan_spot,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }


def _count_verified_headlines_since(ticker: str, since: datetime) -> int:
    """Canonical verified hub stories ingested or published since analysis as_of."""
    from trade_integrations.dataflows.news_hub_bridge import now_instant, query_verified_news

    since_day = since.astimezone(timezone.utc).strftime("%Y-%m-%d")
    records = query_verified_news(as_of=now_instant(), ticker=ticker, since=since_day, limit=200)
    if not records:
        return 0

    since_iso = since.astimezone(timezone.utc).isoformat()
    seen_ids: set[str] = set()
    count = 0
    for rec in records:
        story_id = str(rec.get("canonical_story_id") or "").strip()
        if story_id and story_id in seen_ids:
            continue

        first_seen = str(rec.get("first_seen_at") or "")
        if first_seen and first_seen >= since_iso:
            if story_id:
                seen_ids.add(story_id)
            count += 1
            continue

        tags = rec.get("tags") if isinstance(rec.get("tags"), dict) else {}
        pub_day = str(tags.get("publish_day") or rec.get("published_at") or "")[:10]
        if pub_day and pub_day >= since_day:
            if story_id:
                seen_ids.add(story_id)
            count += 1
    return count


def _material_news_count(ticker: str) -> int:
    try:
        from trade_integrations.dataflows.entity_registry import india_index_tickers
        from trade_integrations.monitor.news_watcher import count_material_headlines_since, news_since

        key = ticker.strip().upper()
        is_index = key in india_index_tickers()
        since = news_since(key, index=is_index)
        if is_index:
            hub_count = _count_verified_headlines_since(key, since)
            if hub_count:
                return hub_count
        return count_material_headlines_since(key, since)
    except Exception:
        logger.exception("Material news count failed for %s", ticker)
        return 0


def _has_open_plan_position(ticker: str) -> bool:
    try:
        from trade_integrations.monitor.execution_ledger import has_open_position_for_underlying

        return bool(has_open_position_for_underlying(ticker))
    except ImportError:
        return False
    except Exception:
        logger.exception("Execution ledger lookup failed for %s", ticker)
        return False


@trade_router.get("/plan-context/{ticker}")
def get_plan_context(
    ticker: str,
    _auth: None = Depends(require_local_or_auth),
) -> Dict[str, Any]:
    """Live staleness, spot drift, and news context for mounted trade widgets."""
    key = _ticker_key(ticker, default="")
    if not key:
        raise HTTPException(status_code=400, detail="ticker required")

    try:
        from trade_integrations.monitor.service import MonitorService
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    if not MonitorService.is_enabled():
        return {"monitor_enabled": False}

    try:
        report = MonitorService().evaluate_ticker(key)
    except Exception as exc:
        logger.exception("plan-context failed for %s", key)
        raise HTTPException(status_code=502, detail=redacted_error_detail(exc)) from exc

    if report is None:
        return {
            "ticker": key,
            "monitor_enabled": True,
            "staleness": {"status": "broken", "reasons": ["monitor_unavailable"], "spot_drift_pct": None},
            "live_context": {"spot": None, "plan_spot": None, "fetched_at": datetime.now(timezone.utc).isoformat()},
            "material_news_count": 0,
            "open_position": False,
        }

    return {
        "ticker": key,
        "monitor_enabled": True,
        "staleness": _widget_staleness_from_report(report),
        "live_context": _live_context_from_report(report),
        "material_news_count": _material_news_count(key),
        "open_position": _has_open_plan_position(key),
    }


class HubPlanResponse(BaseModel):
    status: str
    ticker: str = ""
    asset_type: str = "options"
    artifact: Dict[str, Any] | None = None
    message: str = ""


class AgentDebateResponse(BaseModel):
    status: str
    ticker: str = ""
    running: bool = False
    debate: Dict[str, Any] | None = None
    message: str = ""


class RunDebateRequest(BaseModel):
    ticker: str
    asset_type: str = "options"
    session_id: str | None = None
    refresh: bool = False


@trade_router.get("/hub-plan", response_model=HubPlanResponse)
def get_hub_plan(
    ticker: str,
    asset: str = "options",
    refresh: bool = False,
    _auth: None = Depends(require_local_or_auth),
) -> HubPlanResponse:
    """Load structured trade plan from the shared hub for the research side panel."""
    key = _ticker_key(ticker, default="")
    if not key:
        raise HTTPException(status_code=400, detail="ticker required")
    asset_type = (asset or "options").strip().lower()
    try:
        from src.trade.hub_bridge import load_hub_plan_artifact, prefetch_hub_plan

        if refresh:
            artifact = prefetch_hub_plan(key, asset_type)
        else:
            artifact = load_hub_plan_artifact(key, asset_type)
            if artifact is None:
                artifact = prefetch_hub_plan(key, asset_type)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("hub-plan failed for %s", key)
        raise HTTPException(status_code=502, detail=redacted_error_detail(exc)) from exc

    if artifact is None:
        return HubPlanResponse(status="not_found", ticker=key, asset_type=asset_type, message="No hub plan")
    return HubPlanResponse(status="ok", ticker=key, asset_type=artifact.get("asset_type", asset_type), artifact=artifact)


class IndexPredictionResponse(BaseModel):
    status: str
    ticker: str = ""
    artifact: Dict[str, Any] | None = None
    message: str = ""
    degraded_reason: str | None = None


class RunIndexPredictionRequest(BaseModel):
    ticker: str = "NIFTY"
    horizon_days: int | None = None
    refresh_constituents: bool = False
    run_forecast_lab: bool = True


class IndexPredictionRunStartResponse(BaseModel):
    status: str = "ok"
    job_id: str
    job_status: str
    reused: bool = False


class IndexPredictionRunJobSnapshot(BaseModel):
    job_id: str
    status: str
    ticker: str = ""
    horizon_days: int | None = None
    refresh_constituents: bool = False
    run_forecast_lab: bool = False
    created_at: str | None = None
    error: str | None = None
    warnings: List[str] = Field(default_factory=list)
    logs: List[Dict[str, Any]] = Field(default_factory=list)
    artifact: Dict[str, Any] | None = None
    current_stage: str | None = None
    last_log_at: str | None = None
    last_log_message: str | None = None
    stage_elapsed_ms: float | None = None
    current_track_id: str | None = None


class IndexPredictionRunActiveResponse(BaseModel):
    status: str = "ok"
    job: IndexPredictionRunJobSnapshot | None = None


class IndexPredictionRunJobResponse(BaseModel):
    status: str = "ok"
    job: IndexPredictionRunJobSnapshot | None = None


class StartRecordingRequest(BaseModel):
    underlyings: List[str] = Field(default_factory=lambda: ["NIFTY", "BANKNIFTY", "SENSEX"])
    equities: List[str] = Field(default_factory=list)
    # Opt-in convenience: merges the live NIFTY50 constituent list (via
    # constituents.load_nifty50_constituents()) into `equities` at job
    # start, instead of requiring the caller to paste all 50 symbols.
    # Default False so existing/automated callers are unaffected.
    include_nifty50_constituents: bool = False
    poll_interval_s: int = 10                       # legacy; used only when category_intervals is None
    category_intervals: Dict[str, int] | None = None
    equity_intervals: Dict[str, int] | None = None
    ws_throttle_hz: float | None = None
    historical_config: Dict[str, Any] | None = None
    wait_for_open: bool = False


class RecordingRunStartResponse(BaseModel):
    status: str = "ok"
    job_id: str
    job_status: str
    reused: bool = False


class AutoRecordRequest(BaseModel):
    """Toggle Auto Record. Same recording-config shape as ``StartRecordingRequest``
    (minus ``wait_for_open``, which is implied) — the config is captured as the
    daily template when ``enabled=True`` and ignored when ``enabled=False``."""

    enabled: bool
    underlyings: List[str] = Field(default_factory=lambda: ["NIFTY", "BANKNIFTY", "SENSEX"])
    equities: List[str] = Field(default_factory=list)
    include_nifty50_constituents: bool = False
    poll_interval_s: int = 10                       # legacy; used only when category_intervals is None
    category_intervals: Dict[str, int] | None = None
    equity_intervals: Dict[str, int] | None = None
    ws_throttle_hz: float | None = None
    historical_config: Dict[str, Any] | None = None


class AutoRecordStatusResponse(BaseModel):
    status: str = "ok"
    enabled: bool
    config: Dict[str, Any] | None = None
    updated_at: str | None = None
    active_job_id: str | None = None
    active_job_status: str | None = None


class RecordingJobSnapshot(BaseModel):
    job_id: str
    status: str
    underlyings: List[str] = Field(default_factory=list)
    equities: List[str] = Field(default_factory=list)
    poll_interval_s: int = 10                       # legacy; reflects whichever request created the job, ignored when category_intervals is set
    category_intervals: Dict[str, int] | None = None
    equity_intervals: Dict[str, int] | None = None
    ws_throttle_hz: float | None = None
    historical_config: Dict[str, Any] | None = None
    wait_for_open: bool = False
    # Phase C: ISO timestamp of the scheduled wake deadline when the
    # recorder is in ``waiting_for_open`` state. The frontend renders
    # the "Next open at HH:MM IST" subtitle from this directly (with a
    # log-entry fallback for jobs persisted before Phase C).
    next_open_at: str | None = None
    created_at: str | None = None
    session_date: str | None = None
    error: str | None = None
    logs: List[Dict[str, Any]] = Field(default_factory=list)
    result: Dict[str, Any] | None = None
    last_log_at: str | None = None
    last_log_message: str | None = None
    session_pct_complete: float | None = None


class RecordingActiveResponse(BaseModel):
    status: str = "ok"
    job: RecordingJobSnapshot | None = None


class RecordingJobResponse(BaseModel):
    status: str = "ok"
    job: RecordingJobSnapshot | None = None


class RecordingSessionsResponse(BaseModel):
    status: str = "ok"
    sessions: List[str] = Field(default_factory=list)


class ConstituentInfo(BaseModel):
    symbol: str
    name: str = ""
    sector: str = ""
    weight: float | None = None
    # No published weight for this member (D410): `weight` is then a 0.0 placeholder, not a real 0% share.
    weight_missing: bool = False


class RecordingConstituentsResponse(BaseModel):
    status: str = "ok"
    constituents: List[ConstituentInfo] = Field(default_factory=list)


class StartReplayRequest(BaseModel):
    speed: float | None = None
    loop: bool | None = None
    end_date: str | None = None


class ReplayStatusResponse(BaseModel):
    status: str = "ok"
    message: str | None = None
    replay: Dict[str, Any] | None = None


class SeekReplayRequest(BaseModel):
    time: str


class SetReplaySpeedRequest(BaseModel):
    speed: float


class RefreshIndexPredictionRequest(BaseModel):
    ticker: str = "NIFTY"
    horizon_days: int | None = None
    force: bool = False


class IndexPredictionRefreshResponse(BaseModel):
    status: str
    ticker: str = ""
    reason: str = ""
    artifact: Dict[str, Any] | None = None
    message: str = ""


class IndexPredictionHistoryResponse(BaseModel):
    status: str
    ticker: str = ""
    daily: List[Dict[str, Any]] = Field(default_factory=list)
    intraday: List[Dict[str, Any]] = Field(default_factory=list)
    meta: Dict[str, Any] = Field(default_factory=dict)
    message: str = ""


class ForecastReplayPoint(BaseModel):
    horizon_days: int
    target_date: str
    p50_level: float
    p10_level: float | None = None
    p90_level: float | None = None
    matured: bool
    actual_level: float | None = None
    error_pts: float | None = None
    error_pct: float | None = None
    in_band: bool | None = None
    direction_hit: bool | None = None
    projected: bool = False  # target_date is a weekday projection past the last realised close


class ForecastReplayRecord(BaseModel):
    source_id: str
    family: str
    issued_at: str
    issue_day: str
    provenance: Literal["live", "replay", "backtest"]
    spot_at_issue: float
    points: List[ForecastReplayPoint] = Field(default_factory=list)
    approximate_path: bool
    meta: Dict[str, Any] = Field(default_factory=dict)


class ForecastReplaySource(BaseModel):
    source_id: str
    family: str
    label: str
    provenance: Literal["live", "replay", "backtest"]
    status: Literal["ok", "empty", "error"]
    reason: str | None = None
    first_day: str | None = None
    last_day: str | None = None
    n_stored: int = 0  # records the store holds for the source (whole history, last run per day): an inventory
    n_returned: int | None = None  # records of THIS call for the source; null when the call did not read it


class ForecastReplayActual(BaseModel):
    date: str
    close: float


class ForecastReplayResponse(BaseModel):
    status: str
    ticker: str = ""
    actuals: List[ForecastReplayActual] = Field(default_factory=list)
    sources: List[ForecastReplaySource] = Field(default_factory=list)
    records: List[ForecastReplayRecord] = Field(default_factory=list)
    stats: Dict[str, Dict[str, Any]] =Field(default_factory=dict)
    message: str = ""


class IndexFactorHistoryResponse(BaseModel):
    status: str
    ticker: str = ""
    series: List[Dict[str, Any]] = Field(default_factory=list)
    factors: List[str] = Field(default_factory=list)
    coverage: Dict[str, int] = Field(default_factory=dict)
    coverage_notes: List[str] = Field(default_factory=list)
    message: str = ""


class ForecastEngineArtifactResponse(BaseModel):
    status: str
    available: bool = False
    ticker: str = ""
    recipe_name: str = ""
    recipe_version: str = ""
    engine_checkpoint: str = ""
    horizon_days: int = 0
    as_of: str = ""
    generated_at: str = ""
    spot: float = 0.0
    expected_return_pct: float = 0.0
    quantiles_pct: Dict[str, float] = Field(default_factory=dict)
    dataset: List[Dict[str, Any]] = Field(default_factory=list)
    covariate_keys: List[str] = Field(default_factory=list)
    message: str = ""


class ForecastEngineRecipeInfo(BaseModel):
    name: str
    version: str
    covariate_keys: List[str] = Field(default_factory=list)
    target: str = ""
    description: str = ""


class ForecastEngineRecipesResponse(BaseModel):
    status: str
    recipes: List[ForecastEngineRecipeInfo] = Field(default_factory=list)
    default_recipe: str = ""
    message: str = ""


class ForecastEngineEvaluationResponse(BaseModel):
    status: str
    available: bool = False
    ticker: str = ""
    recipe_name: str = ""
    recipe_version: str = ""
    engine_checkpoint: str = ""
    horizon_days: int = 0
    generated_at: str = ""
    n_observations: int = 0
    n_skipped_gaps: int = 0
    mean_pinball_loss: float = 0.0
    mean_naive_pinball_loss: float = 0.0
    passes_gate: bool = False
    gate_lower_bound: float = 0.0
    observations: List[Dict[str, Any]] = Field(default_factory=list)
    message: str = ""


class ForecastEngineRunStartRequest(BaseModel):
    # ``start``/``end`` become the run_id (an artifact file name) and ``recipe``/``ticker`` path
    # segments, so a free string here was a write outside the artifact directory.
    ticker: str = "NIFTY"
    recipe: str
    start: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    end: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    horizon: int = 5

    @field_validator("ticker", "recipe")
    @classmethod
    def _path_safe(cls, value: str) -> str:
        if is_unsafe_path_component(value):
            raise ValueError("must be a plain name, not a path")
        return value


class ForecastEngineRunStartResponse(BaseModel):
    status: str = "ok"
    job_id: str
    run_id: str
    job_status: str
    reused: bool = False


class ForecastEngineRunJobSnapshot(BaseModel):
    job_id: str
    status: str
    created_at: str | None = None
    error: str | None = None
    logs: List[Dict[str, Any]] = Field(default_factory=list)
    extra: Dict[str, Any] = Field(default_factory=dict)
    result: Dict[str, Any] | None = None


class ForecastEngineRunJobResponse(BaseModel):
    status: str = "ok"
    job: ForecastEngineRunJobSnapshot


class ForecastEngineRunSummary(BaseModel):
    run_id: str
    run_start: str | None = None
    run_end: str | None = None
    generated_at: str | None = None
    n_observations: int | None = None
    passes_gate: bool | None = None
    mean_pinball_loss: float | None = None


class ForecastEngineRunListResponse(BaseModel):
    status: str = "ok"
    runs: List[ForecastEngineRunSummary] = Field(default_factory=list)


class ConstituentHistoryResponse(BaseModel):
    status: str
    symbol: str = ""
    days: int = 90
    snapshot_count: int = 0
    has_research_archive: bool = False
    points: List[Dict[str, Any]] = Field(default_factory=list)
    message: str = ""


class IndexPredictionSnapshotsResponse(BaseModel):
    status: str
    ticker: str = ""
    snapshots: List[Dict[str, Any]] = Field(default_factory=list)
    message: str = ""


class IndexUpcomingEventOutcome(BaseModel):
    reference_date: str | None = None
    reference_close: float | None = None
    outcome_date: str | None = None
    outcome_close: float | None = None
    return_pct: float | None = None
    session_lag: int | None = None


class HistoricalUpcomingEvent(BaseModel):
    date: str | None = None
    days_from_now: int | None = None
    event_type: str | None = None
    label: str | None = None
    symbol: str | None = None
    weight: float | None = None
    sector: str | None = None
    impact: str | None = None
    category: str | None = None
    market_outcome: IndexUpcomingEventOutcome | None = None


class HistoricalUpcomingEventsResponse(BaseModel):
    status: str = "ok"
    events: List[HistoricalUpcomingEvent] = Field(default_factory=list)
    message: str = ""


class IndexBacktestResponse(BaseModel):
    status: str
    ticker: str = ""
    report: Dict[str, Any] | None = None
    message: str = ""


class IndexForecastLabResponse(BaseModel):
    status: str
    ticker: str = ""
    result: Dict[str, Any] | None = None
    message: str = ""
    artifact: Dict[str, Any] | None = None


class IndexTrackScoreboardResponse(BaseModel):
    status: str
    ticker: str = ""
    report: Dict[str, Any] | None = None
    message: str = ""


class IndexExecutionBacktestResponse(BaseModel):
    status: str
    ticker: str = ""
    report: Dict[str, Any] | None = None
    message: str = ""


class IndexMissAnalysisResponse(BaseModel):
    status: str
    ticker: str = ""
    report: Dict[str, Any] | None = None
    message: str = ""


class IndexDataAuditResponse(BaseModel):
    status: str
    ticker: str = ""
    report: Dict[str, Any] | None = None
    message: str = ""


class IndexCounterfactualResponse(BaseModel):
    status: str
    ticker: str = ""
    report: Dict[str, Any] | None = None
    message: str = ""


class IndexPredictionJobsResponse(BaseModel):
    status: str
    env: Dict[str, Any] = Field(default_factory=dict)
    master_scheduler_env_enabled: bool = False
    master_scheduler_running: bool = False
    executor_is_running: bool = False
    news_pipeline: Dict[str, Any] = Field(default_factory=dict)
    index_quote: Dict[str, Any] = Field(default_factory=dict)
    jobs: List[Dict[str, Any]] = Field(default_factory=list)
    job: Dict[str, Any] | None = None
    message: str = ""


class DayAttributionResponse(BaseModel):
    status: str
    date: str = ""
    attribution: Dict[str, Any] = Field(default_factory=dict)
    message: str = ""


class IndexFactorCatalogResponse(BaseModel):
    status: str
    macro_and_technical: List[Dict[str, Any]] = Field(default_factory=list)
    bottom_up: List[Dict[str, Any]] = Field(default_factory=list)
    constituent_research: List[Dict[str, Any]] = Field(default_factory=list)
    constituent_market_data: List[Dict[str, Any]] = Field(default_factory=list)
    news_and_sentiment: List[Dict[str, Any]] = Field(default_factory=list)
    derivatives: List[Dict[str, Any]] = Field(default_factory=list)
    pipeline_modules: List[Dict[str, Any]] = Field(default_factory=list)
    model_layers: List[Dict[str, Any]] = Field(default_factory=list)
    total_macro_keys: int = 0
    message: str = ""


class CaptureRegistryEntityPatch(BaseModel):
    """The capture set is declared in the factor registry (D367); the only editable setting is the switch."""

    capture_enabled: bool


class CaptureRegistryUpdateRequest(BaseModel):
    entity_id: str = "NIFTY"
    patch: CaptureRegistryEntityPatch


class CaptureRegistryBackfillRequest(BaseModel):
    entity_id: str = "NIFTY"
    days: int = 365


class CaptureRegistryResponse(BaseModel):
    status: str
    registry: Dict[str, Any] = Field(default_factory=dict)
    factor_tree: List[Dict[str, Any]] = Field(default_factory=list)
    stats: Dict[str, Any] = Field(default_factory=dict)
    coverage: Dict[str, Any] = Field(default_factory=dict)
    message: str = ""


class HubNewsPage(BaseModel):
    total_count: int = 0
    page: int = 1
    page_size: int = 20
    has_more: bool = False
    sort: str = "relevance"
    search: str = ""
    window_hours: int | None = None


class HubStatusResponse(BaseModel):
    status: str = "ok"
    hub: Dict[str, Any] = Field(default_factory=dict)
    message: str = ""
    news_page: HubNewsPage = Field(default_factory=HubNewsPage)


class HubStagingDrainResponse(BaseModel):
    status: str = "ok"
    summary: Dict[str, Any] = Field(default_factory=dict)
    message: str = ""


class HubNewsPipelineConfigResponse(BaseModel):
    status: str = "ok"
    config: Dict[str, Any] = Field(default_factory=dict)
    message: str = ""


class HubNewsPipelineConfigUpdate(BaseModel):
    full_ingest_cron: str | None = None
    light_ingest_cron: str | None = None
    entity_drain_cron: str | None = None
    entity_maintenance_cron: str | None = None
    entity_drain_continuous_cron: str | None = None
    entity_backpressure_threshold: int | None = None
    full_ingest_sources: str | None = None
    light_ingest_sources: str | None = None
    full_lookback_days: int | None = None
    light_lookback_days: int | None = None
    entity_batch_size: int | None = None
    cluster_threshold: float | None = None
    relevance_gate_enabled: bool | None = None
    relevance_min_confidence: float | None = None
    relevance_score_llm_ambiguous_low: float | None = None
    relevance_score_llm_ambiguous_high: float | None = None
    relevance_rule_first: bool | None = None
    discard_retention_days: int | None = None
    wiki_search_enabled: bool | None = None
    wiki_search_top_k: int | None = None
    wiki_search_max_per_pass: int | None = None
    wiki_search_min_score: float | None = None


class HubNewsCalendarEventArticle(BaseModel):
    event_id: str = ""
    title: str = ""
    url: str = ""
    publisher: str = ""
    source: str = ""
    verification_status: str = ""
    predicted_impact: Dict[str, Any] | None = None
    actual_impact: Dict[str, Any] | None = None


class HubNewsCalendarEvent(BaseModel):
    date: str = ""
    event: str = ""
    type: str = ""
    timeline_phrase: str = ""
    date_confidence: str = ""
    index_impact_mechanism: str = ""
    verification_status: str = ""
    fact_check: Dict[str, Any] | None = None
    articles: List[HubNewsCalendarEventArticle] = Field(default_factory=list)


class HubNewsEventsCalendarResponse(BaseModel):
    status: str = "ok"
    events: List[HubNewsCalendarEvent] = Field(default_factory=list)
    message: str = ""


class HubNewsDiscardRequest(BaseModel):
    entity_id: str = "NIFTY"
    item_id: str = ""
    source_kind: str = "staging"
    reason: str | None = None
    discard_similar: bool = False


class HubNewsDiscardUndoRequest(BaseModel):
    entity_id: str = "NIFTY"
    discard_id: str = ""


class HubNewsDiscardResponse(BaseModel):
    status: str = "ok"
    discarded_count: int = 0
    discard_ids: list[str] = Field(default_factory=list)
    discarded: list[Dict[str, Any]] = Field(default_factory=list)
    similar_preview: Dict[str, Any] | None = None
    message: str = ""


class FactorRegistryGapCandidate(BaseModel):
    candidate: str = ""
    status: str = "pending"
    distinct_ref_count: int = 0
    occurrence_count: int = 0
    first_seen: str | None = None
    last_seen: str | None = None
    sample_titles: list[str] = Field(default_factory=list)
    note: str = ""
    decided_at: str | None = None


class FactorRegistryGapReviewResponse(BaseModel):
    status: str = "ok"
    # Verbatim from the facade. The UI renders this next to the Add action so the button cannot
    # be read as "this factor now exists" — accepting queues a hand-written FactorSpec, it does
    # not modify `factors.registry`.
    accepted_means: str = ""
    counts: Dict[str, int] = Field(default_factory=dict)
    candidates: list[FactorRegistryGapCandidate] = Field(default_factory=list)
    message: str = ""


class FactorRegistryGapDecisionRequest(BaseModel):
    candidate: str = ""
    decision: str = ""  # accepted | ignored | reset
    note: str = ""


class FactorRegistryGapDecisionResponse(BaseModel):
    status: str = "ok"
    candidate: FactorRegistryGapCandidate | None = None
    message: str = ""


class HubNewsDiscardedListResponse(BaseModel):
    status: str = "ok"
    items: list[Dict[str, Any]] = Field(default_factory=list)
    count: int = 0
    message: str = ""


class HubNewsIngestRequest(BaseModel):
    mode: str = "full"
    ticker: str = "NIFTY"
    sources: str | None = None
    lookback_days: int | None = None


class HubNewsPipelineTraceSummaryResponse(BaseModel):
    status: str = "ok"
    summary: Dict[str, Any] = Field(default_factory=dict)
    message: str = ""


class HubNewsPipelineTraceItemsResponse(BaseModel):
    status: str = "ok"
    items: list[Dict[str, Any]] = Field(default_factory=list)
    count: int = 0
    message: str = ""


class SimulateIndexPredictionRequest(BaseModel):
    ticker: str = "NIFTY"
    horizon_days: int | None = None
    factor_overrides: Dict[str, float] = Field(default_factory=dict)
    primary_factor: str | None = None
    primary_shock_pct: float | None = None
    cascade: bool = True
    event_preset_id: str | None = None
    force_heuristic_cascade: bool = False


class SimulateIndexPredictionResponse(BaseModel):
    status: str
    ticker: str = ""
    simulation: Dict[str, Any] = Field(default_factory=dict)
    message: str = ""


class IndexPlaygroundContextResponse(BaseModel):
    status: str
    ticker: str = ""
    context: Dict[str, Any] = Field(default_factory=dict)
    message: str = ""


class IndexNewsImpactResponse(BaseModel):
    status: str
    ticker: str = ""
    report: Dict[str, Any] | None = None
    message: str = ""


class NewsScenarioSessionRequest(BaseModel):
    ticker: str = "NIFTY"
    pipeline_as_of: str
    horizon_days: int | None = 14
    session_id: str | None = None


class NewsScenarioSessionPatchRequest(BaseModel):
    date_range: Dict[str, Any] | None = None
    selected_outcome_id: str | None = None
    active_draft_id: str | None = None
    active_scenario_id: str | None = None


class NewsScenarioSessionResponse(BaseModel):
    status: str = "ok"
    session_id: str = ""
    pipeline_as_of: str = ""
    ticker: str = "NIFTY"
    message: str = ""


class NewsEventScenarioResponse(BaseModel):
    status: str = "ok"
    ticker: str = "NIFTY"
    scenario: Dict[str, Any] | None = None
    scenarios: List[Dict[str, Any]] = Field(default_factory=list)
    message: str = ""


class IndexVerifiedNewsResponse(BaseModel):
    status: str
    ticker: str = ""
    count: int = 0
    items: list[Dict[str, Any]] = Field(default_factory=list)
    inventory: Dict[str, Any] | None = None
    message: str = ""


class IndexQuantReviewResponse(BaseModel):
    status: str
    ticker: str = ""
    review: Dict[str, Any] | None = None
    message: str = ""


class ExternalPredictionsResponse(BaseModel):
    status: str = "ok"
    ticker: str = "NIFTY"
    snapshot: Dict[str, Any] | None = None
    message: str = ""


def _external_predictions_status(snapshot: Any) -> str:
    if snapshot is None:
        return "ok"
    had_errors = bool(getattr(snapshot, "had_errors", False))
    attempt_failures = int(getattr(snapshot, "refresh_attempt_failures", 0) or 0)
    if had_errors or attempt_failures > 0:
        return "partial"
    return "ok"


def _external_prediction_source_health(snapshot: Any) -> list[dict[str, Any]]:
    """Per-source health view, computed from the snapshot rather than stored — mirrors the
    vendor/capability/status pattern used for Hub's `source_availability` card."""
    if snapshot is None:
        return []
    sources_by_id = {s.id: s for s in getattr(snapshot, "sources", None) or []}
    rows: list[dict[str, Any]] = []
    for record in getattr(snapshot, "predictions", None) or []:
        source = sources_by_id.get(record.source_id)
        fetch_status = str(getattr(record, "fetch_status", "") or "unknown")
        status = {"ok": "available", "stale": "stale", "error": "error", "not_found": "error"}.get(
            fetch_status, "unknown"
        )
        provenance = dict(getattr(record, "provenance", None) or {})
        last_attempt = provenance.get("last_refresh_attempt") or {}
        rows.append(
            {
                "source_id": record.source_id,
                "display_name": (source.display_name if source else record.source_id),
                "kind": (source.kind if source else None),
                "fetch_strategy": (getattr(source, "fetch_strategy", None) if source else None),
                "last_success_at": (
                    last_attempt.get("at") if fetch_status == "ok" else provenance.get("last_success_at")
                )
                or (getattr(record, "as_of", "") if fetch_status == "ok" else ""),
                "status": status,
                "last_error_code": getattr(record, "error_message", "") or None,
            }
        )
    return rows


class ExternalPredictionsRefreshRequest(BaseModel):
    ticker: str = "NIFTY"
    horizon_days: int = 14


class ExternalPredictionsRefreshStartResponse(BaseModel):
    status: str = "ok"
    job_id: str
    job_status: str
    reused: bool = False


class ExternalPredictionsRefreshJobSnapshot(BaseModel):
    job_id: str
    status: str
    ticker: str = "NIFTY"
    horizon_days: int = 14
    created_at: str | None = None
    error: str | None = None
    logs: List[Dict[str, Any]] = Field(default_factory=list)
    snapshot: Dict[str, Any] | None = None


class ExternalPredictionsRefreshActiveResponse(BaseModel):
    status: str = "ok"
    job: ExternalPredictionsRefreshJobSnapshot | None = None


class ExternalPredictionsRefreshJobResponse(BaseModel):
    status: str = "ok"
    job: ExternalPredictionsRefreshJobSnapshot | None = None


class ExternalPredictionSourceRequest(BaseModel):
    id: str | None = None
    display_name: str
    domains: List[str] = Field(default_factory=list)
    entry_urls: List[str] = Field(default_factory=list)
    search_queries: List[str] = Field(default_factory=list)
    kind: str = "media"


class ExternalPredictionSourcesResponse(BaseModel):
    status: str = "ok"
    ticker: str = "NIFTY"
    sources: List[Dict[str, Any]] = Field(default_factory=list)
    candidates: List[Dict[str, Any]] = Field(default_factory=list)
    message: str = ""


def _index_artifact_degraded_reason(artifact: Dict[str, Any] | None) -> str | None:
    """Surface a human-readable reason when a plan_status=incomplete artifact is served as ok.

    `plan_status`/`data_warnings` already ride inside the artifact body (see
    `hub_bridge._index_doc_to_panel`), but nothing at the response level flagged an
    incomplete plan loudly — a 200 + status:"ok" with an empty `prediction` dict looked
    identical to a genuine "nothing changed" response.
    """
    if not artifact or artifact.get("plan_status") != "incomplete":
        return None
    warnings = artifact.get("data_warnings") or []
    if warnings:
        return f"Index prediction incomplete: {warnings[0]}"
    return "Index prediction incomplete: no view/contributors available."


def _attach_latest_forecast_fan(artifact: dict[str, Any] | None, ticker: str) -> None:
    """Mutate ``artifact["prediction"]["forecast_fan"]`` in place with the most recently
    persisted `nifty_forecast_fan` band, when one exists.

    Shared by both index-prediction read paths (`get_index_prediction` below and the
    command-center SSE stream's own `load_hub_plan_artifact` poll) — the hub-cached artifact
    itself never carries fan data (`aggregator.py` persists the fan straight to the
    prediction ledger, not into the hub JSON blob), so every reader of the cached artifact
    needs this same enrichment step rather than just one of them.
    See .claude/backlog/items/2026-08-26-wire-nifty-forecast-fan-into-consumers.md.
    """
    if not isinstance(artifact, dict) or not isinstance(artifact.get("prediction"), dict):
        return
    try:
        from trade_integrations.dataflows.index_research.nifty_forecast_fan import (
            load_latest_fan_band,
        )

        fan_band = load_latest_fan_band(ticker=ticker)
    except Exception:
        logger.exception("index-prediction: failed to load latest forecast fan for %s", ticker)
        return
    if fan_band:
        artifact["prediction"]["forecast_fan"] = fan_band


@trade_router.get("/index-prediction", response_model=IndexPredictionResponse)
def get_index_prediction(
    ticker: str = "NIFTY",
    horizon_days: int | None = None,
    refresh: bool = False,
    _auth: None = Depends(require_local_or_auth),
) -> IndexPredictionResponse:
    """Load cached index research artifact for the prediction page."""
    key = _ticker_key(ticker)
    if horizon_days is not None and horizon_days < 1:
        raise HTTPException(status_code=422, detail=f"horizon_days must be >= 1, got {horizon_days}")
    try:
        from src.trade.hub_bridge import load_hub_plan_artifact, prefetch_index_hub_plan

        if refresh:
            artifact = prefetch_index_hub_plan(key)
        else:
            artifact = load_hub_plan_artifact(key, "index")
            if artifact is None:
                artifact = prefetch_index_hub_plan(key)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("index-prediction GET failed for %s", key)
        raise HTTPException(status_code=502, detail=redacted_error_detail(exc)) from exc

    if artifact is None:
        return IndexPredictionResponse(status="not_found", ticker=key, message="No index research")
    if horizon_days is not None and artifact.get("horizon", {}).get("days") != horizon_days:
        artifact["_horizon_mismatch"] = True
    _attach_latest_forecast_fan(artifact, key)
    return IndexPredictionResponse(
        status="ok",
        ticker=key,
        artifact=artifact,
        degraded_reason=_index_artifact_degraded_reason(artifact),
    )


@trade_router.get("/index-prediction/factors", response_model=IndexFactorCatalogResponse)
def get_index_prediction_factors(
    _auth: None = Depends(require_local_or_auth),
) -> IndexFactorCatalogResponse:
    """Return catalog of macro, technical, bottom-up, and model factors."""
    try:
        from trade_integrations.dataflows.index_research.pipeline_stage_catalog import (
            list_factor_catalog,
        )

        payload = list_factor_catalog()
        return IndexFactorCatalogResponse(status="ok", **payload)
    except Exception as exc:
        logger.exception("index-prediction factors catalog failed")
        raise HTTPException(status_code=502, detail=redacted_error_detail(exc)) from exc


@trade_router.get("/capture-registry", response_model=CaptureRegistryResponse)
def get_capture_registry(
    entity_id: str = "NIFTY",
    _auth: None = Depends(require_local_or_auth),
) -> CaptureRegistryResponse:
    """Return hub capture registry, factor tiers, and storage stats."""
    key = _ticker_key(entity_id, default="")
    try:
        from src.trade.hub_bridge import ensure_trade_stack_path

        ensure_trade_stack_path()
        from trade_integrations.hub_capture.registry import (
            build_capture_stats,
            build_factor_tree,
            load_registry,
        )
        from trade_integrations.hub_capture.rollup import capture_coverage_stats

        reg = load_registry()
        return CaptureRegistryResponse(
            status="ok",
            registry=reg,
            factor_tree=build_factor_tree(),
            stats=build_capture_stats(key),
            coverage=capture_coverage_stats(entity_id=key),
        )
    except Exception as exc:
        logger.exception("capture-registry get failed")
        raise HTTPException(status_code=502, detail=redacted_error_detail(exc)) from exc


@trade_router.put("/capture-registry", response_model=CaptureRegistryResponse)
def update_capture_registry(
    body: CaptureRegistryUpdateRequest,
    _auth: None = Depends(require_local_or_auth),
) -> CaptureRegistryResponse:
    """Update capture settings for an entity (v1: NIFTY)."""
    key = _ticker_key(body.entity_id, default="")
    try:
        from src.trade.hub_bridge import ensure_trade_stack_path

        ensure_trade_stack_path()
        from trade_integrations.hub_capture.registry import (
            build_capture_stats,
            build_factor_tree,
            load_registry,
            set_capture_enabled,
        )
        from trade_integrations.hub_capture.rollup import capture_coverage_stats

        set_capture_enabled(key, body.patch.capture_enabled)
        reg = load_registry()
        return CaptureRegistryResponse(
            status="ok",
            registry=reg,
            factor_tree=build_factor_tree(),
            stats=build_capture_stats(key),
            coverage=capture_coverage_stats(entity_id=key),
        )
    except Exception as exc:
        logger.exception("capture-registry update failed")
        raise HTTPException(status_code=502, detail=redacted_error_detail(exc)) from exc


@trade_router.post("/capture-registry/backfill")
def run_capture_registry_backfill(
    body: CaptureRegistryBackfillRequest,
    _auth: None = Depends(require_local_or_auth),
) -> Dict[str, Any]:
    """Backfill proprietary NIFTY factor history (participant OI, flows)."""
    key = _ticker_key(body.entity_id, default="")
    try:
        from src.trade.hub_bridge import ensure_trade_stack_path

        ensure_trade_stack_path()
        from trade_integrations.hub_capture.intraday import run_capture_backfill

        return run_capture_backfill(entity_id=key, days=max(30, min(body.days, 730)))
    except Exception as exc:
        logger.exception("capture-registry backfill failed")
        raise HTTPException(status_code=502, detail=redacted_error_detail(exc)) from exc


@trade_router.post("/capture-registry/intraday")
def run_capture_registry_intraday(
    entity_id: str = "NIFTY",
    _auth: None = Depends(require_local_or_auth),
) -> Dict[str, Any]:
    """Run one intraday chain capture now (OpenAlgo → hub)."""
    key = _ticker_key(entity_id, default="")
    try:
        from src.trade.hub_bridge import ensure_trade_stack_path

        ensure_trade_stack_path()
        from trade_integrations.hub_capture.intraday import run_intraday_capture

        return run_intraday_capture(entity_id=key)
    except Exception as exc:
        logger.exception("capture-registry intraday failed")
        raise HTTPException(status_code=502, detail=redacted_error_detail(exc)) from exc


@trade_router.get("/hub/status", response_model=HubStatusResponse)
def get_hub_status(
    entity_id: str = "NIFTY",
    search: str | None = None,
    sort: str = "relevance",
    page: int = 1,
    page_size: int = 20,
    window_hours: int | None = None,
    provenance: str | None = None,
    detail: str = "summary",
    _auth: None = Depends(require_local_or_auth),
) -> HubStatusResponse:
    """Return hub inventory: staging queue, verified news, cache health, capture stats.

    ``detail=full`` additionally runs the LLM-Wiki network probes in the news_pipeline block.
    The default is deliberately cheap: this endpoint is polled every 30s by the Hub page.
    """
    key = _ticker_key(entity_id, default="")
    size = max(1, min(int(page_size or 20), 100))
    current_page = max(1, int(page or 1))
    try:
        from src.trade.hub_bridge import ensure_trade_stack_path

        ensure_trade_stack_path()
        from trade_integrations.hub_storage.hub_status import build_hub_status
        from trade_integrations.dataflows.news_hub_bridge import apply_news_feed_to_hub

        # The feed pool has to cover every page the client can ask for, so the
        # inventory load scales with the requested page rather than the old 50.
        pool_size = max(50, min(current_page * size + size, 400))
        news_since: str | None = None
        if window_hours is not None and int(window_hours) > 0:
            # A window is filtered at the source (parquet/JSONL scan), not by
            # truncating an already-capped pool, so it isn't limited to the
            # page-scaled pool_size above.
            news_since = (datetime.now(timezone.utc) - timedelta(hours=int(window_hours))).isoformat()
        hub = build_hub_status(
            entity_id=key, news_limit=pool_size, news_since=news_since, detail=detail
        )
        page_meta = apply_news_feed_to_hub(
            hub,
            ticker=key,
            search=search,
            sort=sort,
            page=current_page,
            page_size=size,
            window_hours=window_hours,
            provenance=provenance,
        )
        return HubStatusResponse(status="ok", hub=hub, news_page=HubNewsPage(**page_meta))
    except Exception as exc:
        logger.exception("hub status failed")
        raise HTTPException(status_code=502, detail=redacted_error_detail(exc)) from exc


@trade_router.post("/hub/staging/drain", response_model=HubStagingDrainResponse)
def drain_hub_staging(
    entity_id: str = "NIFTY",
    limit: int = 20,
    _auth: None = Depends(require_local_or_auth),
) -> HubStagingDrainResponse:
    """Manually process a batch of queued staging news refs."""
    key = _ticker_key(entity_id, default="")
    try:
        from src.trade.hub_bridge import ensure_trade_stack_path

        ensure_trade_stack_path()
        from trade_integrations.dataflows.news_hub_bridge import process_staging_batch

        summary = process_staging_batch(ticker=key, limit=max(1, min(limit, 100)))
        if summary.get("pipeline_paused") or summary.get("paused"):
            return HubStagingDrainResponse(
                status="paused",
                summary=summary,
                message=str(summary.get("pause_reason") or "News distillation pipeline is paused."),
            )
        return HubStagingDrainResponse(status="ok", summary=summary)
    except Exception as exc:
        logger.exception("hub staging drain failed")
        raise HTTPException(status_code=502, detail=redacted_error_detail(exc)) from exc


@trade_router.get("/hub/news-pipeline/config", response_model=HubNewsPipelineConfigResponse)
def get_hub_news_pipeline_config(
    _auth: None = Depends(require_local_or_auth),
) -> HubNewsPipelineConfigResponse:
    """Return hub news ingest/distill schedule (env defaults + hub override file)."""
    try:
        from src.trade.hub_bridge import ensure_trade_stack_path

        ensure_trade_stack_path()
        from trade_integrations.dataflows.news_hub_bridge import get_pipeline_config

        return HubNewsPipelineConfigResponse(status="ok", config=get_pipeline_config())
    except Exception as exc:
        logger.exception("hub news pipeline config read failed")
        raise HTTPException(status_code=502, detail=redacted_error_detail(exc)) from exc


@trade_router.patch("/hub/news-pipeline/config", response_model=HubNewsPipelineConfigResponse)
def patch_hub_news_pipeline_config(
    body: HubNewsPipelineConfigUpdate,
    _auth: None = Depends(require_local_or_auth),
) -> HubNewsPipelineConfigResponse:
    """Update persisted pipeline config and sync scheduled job crons."""
    try:
        from src.trade.hub_bridge import ensure_trade_stack_path

        ensure_trade_stack_path()
        from trade_integrations.dataflows.news_hub_bridge import update_pipeline_config

        patch = {k: v for k, v in body.model_dump().items() if v is not None}
        payload = update_pipeline_config(patch)
        return HubNewsPipelineConfigResponse(status="ok", config=payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("hub news pipeline config update failed")
        raise HTTPException(status_code=502, detail=redacted_error_detail(exc)) from exc


@trade_router.get("/hub/news-events/calendar", response_model=HubNewsEventsCalendarResponse)
def get_hub_news_events_calendar(
    start: Optional[str] = None,
    end: Optional[str] = None,
    ticker: str = "NIFTY",
    _auth: None = Depends(require_local_or_auth),
) -> HubNewsEventsCalendarResponse:
    """News-extracted future events across the whole corpus, each resolved back to its
    source article(s) for the Hub Events calendar view."""
    try:
        from src.trade.hub_bridge import ensure_trade_stack_path

        ensure_trade_stack_path()
        from trade_integrations.dataflows.news_hub_bridge import (
            now_instant,
            list_extracted_future_events,
            query_verified_news,
        )

        rows = list_extracted_future_events(market="IN", start=start, end=end)

        # Resolve all source articles with a single bulk read rather than one facade
        # call per event_id — get_distilled_event() reloads the whole events frame on
        # every call, so calling it in a per-row loop is an N+1 that times out once the
        # corpus has more than a handful of events.
        article_by_id: Dict[str, HubNewsCalendarEventArticle] = {}
        for raw in query_verified_news(as_of=now_instant(), ticker=ticker, market="IN", include_rejected=True, limit=10_000):
            event_id = str(raw.get("event_id") or raw.get("id") or "")
            if not event_id:
                continue
            sources = raw.get("sources") or raw.get("references") or []
            first = sources[0] if sources and isinstance(sources[0], dict) else {}
            article_by_id[event_id] = HubNewsCalendarEventArticle(
                event_id=event_id,
                title=str(raw.get("title") or ""),
                url=str(raw.get("url") or first.get("url") or ""),
                publisher=str(first.get("publisher") or first.get("vendor") or ""),
                source=str(raw.get("source") or first.get("vendor") or ""),
                verification_status=str(raw.get("verification_status") or ""),
                predicted_impact=(raw.get("predicted_impact") or None),
                actual_impact=(raw.get("actual_impact") or None),
            )

        events: List[HubNewsCalendarEvent] = []
        for row in rows:
            event_ids = [str(eid) for eid in (row.get("source_event_ids") or []) if eid]
            articles = [article_by_id[eid] for eid in event_ids if eid in article_by_id]
            events.append(
                HubNewsCalendarEvent(
                    date=str(row.get("date") or ""),
                    event=str(row.get("event") or ""),
                    type=str(row.get("type") or ""),
                    timeline_phrase=str(row.get("timeline_phrase") or ""),
                    date_confidence=str(row.get("date_confidence") or ""),
                    index_impact_mechanism=str(row.get("index_impact_mechanism") or ""),
                    verification_status=str(row.get("verification_status") or ""),
                    fact_check=row.get("fact_check"),
                    articles=articles,
                )
            )

        return HubNewsEventsCalendarResponse(status="ok", events=events)
    except Exception as exc:
        logger.exception("hub news events calendar read failed")
        raise HTTPException(status_code=502, detail=redacted_error_detail(exc)) from exc


class ModelAdapterRateLimit(BaseModel):
    rpm: int
    max_concurrent: int
    max_attempts: int
    base_delay_s: float
    max_delay_s: float
    jitter: bool
    honor_retry_after: bool


class ModelAdapter(BaseModel):
    adapter_id: str
    kind: str
    provider: str
    model: str
    enabled: bool
    priority: int
    fallback_adapter_id: str | None
    rate_limit: ModelAdapterRateLimit


class ModelAdaptersResponse(BaseModel):
    status: str = "ok"
    adapters: List[ModelAdapter] = Field(default_factory=list)
    message: str = ""


class ModelAdapterUpdate(BaseModel):
    enabled: bool | None = None
    priority: int | None = None
    fallback_adapter_id: str | None = None
    rate_limit: Dict[str, Any] | None = None
    retry: Dict[str, Any] | None = None


def _model_adapter_to_dict(spec: Any) -> Dict[str, Any]:
    return {
        "adapter_id": spec.adapter_id,
        "kind": spec.kind,
        "provider": spec.provider,
        "model": spec.model,
        "enabled": spec.enabled,
        "priority": spec.priority,
        "fallback_adapter_id": spec.fallback_adapter_id,
        "rate_limit": {
            "rpm": spec.rate_limit.rpm,
            "max_concurrent": spec.rate_limit.max_concurrent,
            "max_attempts": spec.rate_limit.max_attempts,
            "base_delay_s": spec.rate_limit.base_delay_s,
            "max_delay_s": spec.rate_limit.max_delay_s,
            "jitter": spec.rate_limit.jitter,
            "honor_retry_after": spec.rate_limit.honor_retry_after,
        },
    }


@trade_router.get("/model-adapters", response_model=ModelAdaptersResponse)
def get_model_adapters(
    _auth: None = Depends(require_local_or_auth),
) -> ModelAdaptersResponse:
    """List every registered LLM/embedding model adapter (catalog + runtime overrides)."""
    try:
        from src.trade.hub_bridge import ensure_trade_stack_path

        ensure_trade_stack_path()
        from trade_integrations.dataflows.model_adapters import list_adapters

        adapters = [_model_adapter_to_dict(spec) for spec in list_adapters()]
        return ModelAdaptersResponse(status="ok", adapters=adapters)
    except Exception as exc:
        logger.exception("model adapters list failed")
        raise HTTPException(status_code=502, detail=redacted_error_detail(exc)) from exc


@trade_router.patch("/model-adapters/{adapter_id}", response_model=ModelAdaptersResponse)
def patch_model_adapter(
    adapter_id: str,
    body: ModelAdapterUpdate,
    _auth: None = Depends(require_local_or_auth),
) -> ModelAdaptersResponse:
    """Persist a runtime override for one adapter (enable/disable, priority, rate limits, ...)."""
    try:
        from src.trade.hub_bridge import ensure_trade_stack_path

        ensure_trade_stack_path()
        from trade_integrations.dataflows.model_adapters import list_adapters, update_adapter

        # exclude_unset, not "drop every None": an explicit fallback_adapter_id=null is how the UI
        # clears a fallback ("(none)"); dropping it made that selection a silent no-op.
        patch = {
            k: v
            for k, v in body.model_dump(exclude_unset=True).items()
            if v is not None or k == "fallback_adapter_id"
        }
        update_adapter(adapter_id, patch)
        adapters = [_model_adapter_to_dict(spec) for spec in list_adapters()]
        return ModelAdaptersResponse(status="ok", adapters=adapters)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("model adapter update failed")
        raise HTTPException(status_code=502, detail=redacted_error_detail(exc)) from exc


@trade_router.post("/hub/news-pipeline/maintenance", response_model=HubStagingDrainResponse)
def run_hub_news_maintenance_now(
    entity_id: str = "NIFTY",
    lookback_days: int = 365,
    _auth: None = Depends(require_local_or_auth),
) -> HubStagingDrainResponse:
    """Run full entity maintainer: repair, backfill, compact, cleanup, rollup."""
    key = _ticker_key(entity_id, default="")
    try:
        from src.trade.hub_bridge import ensure_trade_stack_path

        ensure_trade_stack_path()
        from trade_integrations.dataflows.news_hub_bridge import run_entity_worker_job as run_hub_news_entity_job

        summary = run_hub_news_entity_job(
            {
                "ticker": key,
                "mode": "maintenance",
                "batch_size": 200,
                "lookback_days": max(7, min(lookback_days, 730)),
            }
        )
        if summary.get("pipeline_paused"):
            return HubStagingDrainResponse(
                status="paused",
                summary=summary,
                message=str(summary.get("pause_reason") or "News distillation pipeline is paused."),
            )
        if summary.get("had_errors"):
            return HubStagingDrainResponse(
                status="partial",
                summary=summary,
                message="Maintainer finished with one or more stage errors; see summary.had_errors.",
            )
        return HubStagingDrainResponse(status="ok", summary=summary)
    except Exception as exc:
        logger.exception("hub news maintenance failed")
        raise HTTPException(status_code=502, detail=redacted_error_detail(exc)) from exc


@trade_router.post("/hub/news-pipeline/ingest", response_model=HubStagingDrainResponse)
def run_hub_news_ingest_now(
    body: HubNewsIngestRequest,
    _auth: None = Depends(require_local_or_auth),
) -> HubStagingDrainResponse:
    """Trigger ingest immediately (full or light mode)."""
    key = _ticker_key(body.ticker)
    mode = (body.mode or "full").strip().lower()
    try:
        from src.trade.hub_bridge import ensure_trade_stack_path

        ensure_trade_stack_path()
        from trade_integrations.dataflows.news_hub_bridge import run_hub_news_ingest

        summary = run_hub_news_ingest(
            ticker=key,
            mode=mode,
            sources=body.sources or "default",
            lookback_days=body.lookback_days, market=market_for_ticker(key),
        )
        if summary.get("blocked") or (
            summary.get("pipeline_paused")
            and str(summary.get("pause_reason") or "") == "llm_wiki_unavailable"
        ):
            return HubStagingDrainResponse(
                status="paused",
                summary=summary,
                message=str(
                    summary.get("user_message")
                    or summary.get("pause_reason")
                    or "News ingest blocked — LLM-Wiki unavailable."
                ),
            )
        if summary.get("pipeline_paused"):
            return HubStagingDrainResponse(
                status="paused",
                summary=summary,
                message=str(summary.get("pause_reason") or "News distillation pipeline is paused."),
            )
        return HubStagingDrainResponse(status="ok", summary=summary)
    except Exception as exc:
        logger.exception("hub news ingest now failed")
        raise HTTPException(status_code=502, detail=redacted_error_detail(exc)) from exc


@trade_router.get("/hub/factor-registry-gaps", response_model=FactorRegistryGapReviewResponse)
def get_factor_registry_gaps(
    status: str | None = None,
    limit: int = 100,
    _auth: None = Depends(require_local_or_auth),
) -> FactorRegistryGapReviewResponse:
    """Factor names the news attribution pass could not place in `factors.registry`.

    Refreshed at the end of every `process_staging_batch`, so this is a plain read — it does not
    rescan or recompute. `status` filters to pending / accepted / ignored.
    """
    try:
        from src.trade.hub_bridge import ensure_trade_stack_path

        ensure_trade_stack_path()
        from trade_integrations.dataflows.news_hub_bridge import factor_registry_gap_review

        payload = factor_registry_gap_review(status=status, limit=max(1, min(int(limit or 100), 500)))
        return FactorRegistryGapReviewResponse(
            status="ok",
            accepted_means=str(payload.get("accepted_means") or ""),
            counts={str(k): int(v) for k, v in (payload.get("counts") or {}).items()},
            candidates=[FactorRegistryGapCandidate(**row) for row in (payload.get("candidates") or [])],
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("factor registry gap review failed")
        raise HTTPException(status_code=502, detail=redacted_error_detail(exc)) from exc


@trade_router.post("/hub/factor-registry-gaps/decision", response_model=FactorRegistryGapDecisionResponse)
def decide_factor_registry_gap_route(
    body: FactorRegistryGapDecisionRequest,
    _auth: None = Depends(require_local_or_auth),
) -> FactorRegistryGapDecisionResponse:
    """Accept, ignore, or reset one registry-gap candidate.

    `accepted` queues the name for a hand-written `FactorSpec`; it does **not** write anything
    into `factors.registry`, which is built from Python source. Decisions are sticky — an
    answered name does not return to `pending` when the pipeline sees it again.
    """
    try:
        from src.trade.hub_bridge import ensure_trade_stack_path

        ensure_trade_stack_path()
        from trade_integrations.dataflows.news_hub_bridge import decide_factor_registry_gap

        entry = decide_factor_registry_gap(
            str(body.candidate or ""),
            str(body.decision or ""),
            note=str(body.note or ""),
        )
        return FactorRegistryGapDecisionResponse(
            status="ok",
            candidate=FactorRegistryGapCandidate(**entry),
        )
    except ValueError as exc:
        # A bad candidate/decision is the caller's error, not a server fault — a 502 here would
        # look like the hub is down when the request was simply malformed.
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("factor registry gap decision failed")
        raise HTTPException(status_code=502, detail=redacted_error_detail(exc)) from exc


@trade_router.post("/hub/news/discard", response_model=HubNewsDiscardResponse)
def discard_hub_news(
    body: HubNewsDiscardRequest,
    _auth: None = Depends(require_local_or_auth),
) -> HubNewsDiscardResponse:
    """Discard one news item or discard similar cluster."""
    key = _ticker_key(body.entity_id)
    try:
        from src.trade.hub_bridge import ensure_trade_stack_path

        ensure_trade_stack_path()
        from trade_integrations.dataflows.news_hub_bridge import (
            discard_news_item,
            discard_similar_items,
            get_distilled_event,
            list_pending_staging_refs,
            now_instant,
            preview_discard_similar,
        )

        reason = str(body.reason or "manual discard")
        if body.discard_similar:
            anchor: dict[str, Any] = {}
            iid = str(body.item_id or "").strip()
            if body.source_kind == "staging" or iid.startswith("ref:"):
                for ref in list_pending_staging_refs(ticker=key, limit=10_000):
                    if str(ref.get("ref_id") or "") == iid:
                        anchor = {**ref, "provenance": "staging"}
                        break
            else:
                ev = get_distilled_event(iid, as_of=now_instant())
                if ev:
                    anchor = {**ev, "provenance": "distilled_event"}
            if not anchor:
                raise HTTPException(status_code=404, detail=f"item not found: {iid}")
            preview = preview_discard_similar(anchor, ticker=key)
            result = discard_similar_items(anchor, ticker=key, reason=reason)
            return HubNewsDiscardResponse(
                status="ok",
                discarded_count=int(result.get("discarded_count") or 0),
                discard_ids=list(result.get("discard_ids") or []),
                discarded=list(result.get("discarded") or []),
                similar_preview=preview,
            )

        result = discard_news_item(
            str(body.item_id or ""),
            ticker=key,
            source_kind=str(body.source_kind or "staging"),
            reason=reason,
        )
        rows = list(result.get("discarded") or [])
        return HubNewsDiscardResponse(
            status="ok",
            discarded_count=int(result.get("count") or len(rows)),
            discard_ids=[str(r.get("discard_id") or "") for r in rows if r.get("discard_id")],
            discarded=rows,
        )
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("hub news discard failed")
        raise HTTPException(status_code=502, detail=redacted_error_detail(exc)) from exc


@trade_router.post("/hub/news/discard/undo", response_model=HubNewsDiscardResponse)
def undo_hub_news_discard(
    body: HubNewsDiscardUndoRequest,
    _auth: None = Depends(require_local_or_auth),
) -> HubNewsDiscardResponse:
    """Restore a soft-discarded news item within retention window."""
    try:
        from src.trade.hub_bridge import ensure_trade_stack_path

        ensure_trade_stack_path()
        from trade_integrations.dataflows.news_hub_bridge import undo_news_discard

        result = undo_news_discard(str(body.discard_id or "").strip())
        if not result.get("restored"):
            return HubNewsDiscardResponse(
                status="failed",
                message=str(result.get("reason") or "restore failed"),
            )
        return HubNewsDiscardResponse(status="ok", message="restored", discarded=[result])
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("hub news discard undo failed")
        raise HTTPException(status_code=502, detail=redacted_error_detail(exc)) from exc


@trade_router.get("/hub/news/discarded", response_model=HubNewsDiscardedListResponse)
def list_hub_discarded_news(
    entity_id: str = "NIFTY",
    limit: int = 50,
    _auth: None = Depends(require_local_or_auth),
) -> HubNewsDiscardedListResponse:
    """List soft-discarded news items (30d retention)."""
    key = _ticker_key(entity_id, default="")
    try:
        from src.trade.hub_bridge import ensure_trade_stack_path

        ensure_trade_stack_path()
        from trade_integrations.dataflows.news_hub_bridge import list_discarded_news

        items = list_discarded_news(ticker=key, limit=max(1, min(limit, 200)))
        return HubNewsDiscardedListResponse(status="ok", items=items, count=len(items))
    except Exception as exc:
        logger.exception("hub discarded news list failed")
        raise HTTPException(status_code=502, detail=redacted_error_detail(exc)) from exc


@trade_router.get("/hub/news-pipeline/trace/summary", response_model=HubNewsPipelineTraceSummaryResponse)
def get_hub_news_pipeline_trace_summary(
    entity_id: str = "NIFTY",
    _auth: None = Depends(require_local_or_auth),
) -> HubNewsPipelineTraceSummaryResponse:
    """Per-source and per-stage counts for the Hub news pipeline node diagram."""
    key = _ticker_key(entity_id, default="")
    try:
        from src.trade.hub_bridge import ensure_trade_stack_path

        ensure_trade_stack_path()
        from trade_integrations.dataflows.news_hub_bridge import pipeline_trace_summary

        summary = pipeline_trace_summary(ticker=key)
        return HubNewsPipelineTraceSummaryResponse(status="ok", summary=summary)
    except Exception as exc:
        logger.exception("hub news pipeline trace summary failed")
        raise HTTPException(status_code=502, detail=redacted_error_detail(exc)) from exc


@trade_router.get("/hub/news-pipeline/trace/items", response_model=HubNewsPipelineTraceItemsResponse)
def list_hub_news_pipeline_trace_items(
    entity_id: str = "NIFTY",
    source: str = "",
    stage: str = "",
    status: str = "",
    limit: int = 40,
    _auth: None = Depends(require_local_or_auth),
) -> HubNewsPipelineTraceItemsResponse:
    """List traced refs at a given pipeline node (source and/or stage+status)."""
    key = _ticker_key(entity_id, default="")
    try:
        from src.trade.hub_bridge import ensure_trade_stack_path

        ensure_trade_stack_path()
        from trade_integrations.dataflows.news_hub_bridge import list_pipeline_trace_items

        items = list_pipeline_trace_items(
            ticker=key,
            source=source or None,
            stage=stage or None,
            status=status or None,
            limit=max(1, min(limit, 200)),
        )
        return HubNewsPipelineTraceItemsResponse(status="ok", items=items, count=len(items))
    except Exception as exc:
        logger.exception("hub news pipeline trace items failed")
        raise HTTPException(status_code=502, detail=redacted_error_detail(exc)) from exc


@trade_router.post("/index-prediction/simulate", response_model=SimulateIndexPredictionResponse)
def simulate_index_prediction(
    body: SimulateIndexPredictionRequest,
    _auth: None = Depends(require_local_or_auth),
) -> SimulateIndexPredictionResponse:
    """What-if: adjust macro factors and recompute index forecast."""
    key = _ticker_key(body.ticker)
    try:
        from trade_integrations.context.hub import load_index_research_json
        from trade_integrations.dataflows.index_research.cascade.calibration_store import (
            load_calibration_from_doc,
        )
        from trade_integrations.dataflows.index_research.cascade.types import CascadeCalibration
        from trade_integrations.dataflows.index_research.simulate import (
            macro_factors_from_rows,
            simulate_index_prediction as run_simulate,
        )
        from src.trade.hub_bridge import ensure_trade_stack_path

        ensure_trade_stack_path()
        doc = load_index_research_json(key)
        if doc is None or not doc.spot:
            return SimulateIndexPredictionResponse(
                status="not_found",
                ticker=key,
                message="Run index analysis first",
            )

        macro = macro_factors_from_rows(doc.global_factors or [])
        pred = doc.prediction or {}
        bottom_up = float(pred.get("bottom_up_return_pct") or 0.0)
        headline = float(pred.get("expected_return_pct") or 0.0)
        horizon_days = body.horizon_days or (doc.horizon or {}).get("days")
        calibration = load_calibration_from_doc(doc)
        india_vix = macro.get("india_vix")
        if india_vix is None and isinstance(doc.regime, dict):
            india_vix = doc.regime.get("india_vix")

        simulation = run_simulate(
            macro_factors=macro,
            factor_overrides=body.factor_overrides,
            spot=float(doc.spot),
            bottom_up_return_pct=bottom_up,
            horizon_days=horizon_days,
            headline_return_pct=headline,
            primary_factor=body.primary_factor,
            primary_shock_pct=body.primary_shock_pct,
            cascade=body.cascade,
            event_preset_id=body.event_preset_id,
            event_impact_curves=doc.event_impact_curves or [],
            cascade_calibration=calibration,
            india_vix=float(india_vix) if india_vix is not None else None,
            force_heuristic_cascade=bool(body.force_heuristic_cascade),
        )
        if simulation.get("error"):
            return SimulateIndexPredictionResponse(
                status="error",
                ticker=key,
                message=str(simulation["error"]),
            )
        return SimulateIndexPredictionResponse(status="ok", ticker=key, simulation=simulation)
    except Exception as exc:
        logger.exception("index-prediction simulate failed for %s", key)
        raise HTTPException(status_code=502, detail=redacted_error_detail(exc)) from exc


@trade_router.get("/index-prediction/playground-context", response_model=IndexPlaygroundContextResponse)
def get_index_playground_context(
    ticker: str = "NIFTY",
    refresh: bool = False,
    _auth: None = Depends(require_local_or_auth),
) -> IndexPlaygroundContextResponse:
    """Headlines, events, and ranked factors for the factor impact workbench."""
    key = _ticker_key(ticker)
    try:
        from trade_integrations.context.hub import load_index_research_json
        from trade_integrations.dataflows.index_research.playground_context import (
            doc_as_of_iso,
            load_playground_context,
            resolve_playground_context,
        )
        from src.trade.hub_bridge import ensure_trade_stack_path
        from src.trade.prediction_heavy_pool import run_single_flight

        ensure_trade_stack_path()
        doc = load_index_research_json(key)
        if doc is None:
            return IndexPlaygroundContextResponse(
                status="not_found",
                ticker=key,
                message="Run index analysis first",
            )

        doc_as_of = doc_as_of_iso(doc)[:19]
        if not refresh:
            cached = load_playground_context(key)
            if cached and str(cached.get("as_of") or "")[:19] == doc_as_of:
                return IndexPlaygroundContextResponse(status="ok", ticker=key, context=cached)

        flight_key = f"playground:{key}:{doc_as_of}:refresh={int(refresh)}"
        ctx = run_single_flight(
            flight_key,
            lambda: resolve_playground_context(doc, ticker=key, refresh=refresh),
        )
        return IndexPlaygroundContextResponse(status="ok", ticker=key, context=ctx)
    except Exception as exc:
        logger.exception("index-prediction playground-context failed for %s", key)
        raise HTTPException(status_code=502, detail=redacted_error_detail(exc)) from exc


@trade_router.get("/index-prediction/backtest", response_model=IndexBacktestResponse)
def get_index_prediction_backtest(
    ticker: str = "NIFTY",
    refresh: bool = False,
    days: int = 500,
    horizon_days: int | None = None,
    include_bottom_up: str = "auto",
    _auth: None = Depends(require_local_or_auth),
) -> IndexBacktestResponse:
    """Load cached walk-forward backtest or recompute from factor history."""
    key = _ticker_key(ticker)
    try:
        from trade_integrations.dataflows.index_research.backtest_runner import (
            load_backtest_report,
            run_and_save_backtest,
        )
        from src.trade.hub_bridge import ensure_trade_stack_path

        ensure_trade_stack_path()
        backtest_kwargs = {
            "days": days,
            "horizon_days": horizon_days,
            "include_bottom_up": include_bottom_up,
        }
        from src.trade.prediction_heavy_pool import run_single_flight

        if refresh:
            flight_key = (
                f"backtest:{key}:{days}:{horizon_days}:{include_bottom_up}:refresh=1"
            )
            report = run_single_flight(
                flight_key,
                lambda: run_and_save_backtest(**backtest_kwargs),
            )
        else:
            report = load_backtest_report(key)
            if report is None:
                flight_key = (
                    f"backtest:{key}:{days}:{horizon_days}:{include_bottom_up}:cold"
                )
                report = run_single_flight(
                    flight_key,
                    lambda: run_and_save_backtest(**backtest_kwargs),
                )
        status = str(report.get("status") or "ok")
        return IndexBacktestResponse(status=status, ticker=key, report=report)
    except Exception as exc:
        logger.exception("index-prediction backtest failed for %s", key)
        raise HTTPException(status_code=502, detail=redacted_error_detail(exc)) from exc


@trade_router.get("/index-prediction/forecast-lab", response_model=IndexForecastLabResponse)
@trade_router.post("/index-prediction/forecast-lab", response_model=IndexForecastLabResponse)
def index_prediction_forecast_lab(
    ticker: str = "NIFTY",
    horizon_days: int = 14,
    mode: str = "tracks_only",
    combiner_id: str | None = None,
    use_hub_cache: bool = True,
    body: Dict[str, Any] | None = None,
    _auth: None = Depends(require_local_or_auth),
) -> IndexForecastLabResponse:
    """Plug-and-play forecast lab — independent tracks + optional combiner."""
    key = _ticker_key(ticker)
    try:
        from trade_integrations.dataflows.index_research.prediction_algorithms.api import run_forecast_lab
        from trade_integrations.dataflows.index_research.prediction_algorithms.config import (
            default_combiner_id,
            lab_enabled,
        )
        from trade_integrations.dataflows.index_research.prediction_algorithms.context_builder import (
            context_from_hub,
        )
        from trade_integrations.dataflows.index_research.prediction_algorithms.promotion import (
            resolve_active_combiner,
            resolve_combiner_runtime_kwargs,
        )
        from src.trade.hub_bridge import ensure_trade_stack_path

        ensure_trade_stack_path()
        if not lab_enabled():
            return IndexForecastLabResponse(
                status="disabled",
                ticker=key,
                message="INDEX_PREDICTION_LAB_ENABLED=0",
            )

        payload = body or {}
        hz = int(payload.get("horizon_days") or horizon_days)
        run_mode = str(payload.get("mode") or mode or "tracks_only")
        combiner = payload.get("combiner_id") or combiner_id
        use_cache = payload.get("use_hub_cache", use_hub_cache)

        ctx = None
        if use_cache:
            ctx = context_from_hub(key, horizon_days=hz)
        if ctx is None:
            return IndexForecastLabResponse(
                status="error",
                ticker=key,
                message="hub_cache_unavailable",
            )

        lab_mode_val = "combine" if run_mode == "combine" else "tracks_only"
        active = None
        runtime_kwargs: dict[str, Any] = {}
        if lab_mode_val == "combine":
            active = combiner or resolve_active_combiner(default=default_combiner_id(), ticker=key)
            if active:
                runtime_kwargs = resolve_combiner_runtime_kwargs(
                    str(active),
                    ticker=key,
                    as_of_day=getattr(ctx, "as_of_day", None),
                )
        result = run_forecast_lab(
            ctx,
            mode=lab_mode_val,
            combiner_id=combiner or active,
            mae_by_track=runtime_kwargs.get("mae_by_track"),
            lam=runtime_kwargs.get("lam"),
        )
        lab_dict = result.to_dict()
        artifact = None
        persist = payload.get("persist", True)
        if persist is not False:
            from trade_integrations.context.hub import load_index_research_json
            from trade_integrations.dataflows.index_research.prediction_algorithms.pipeline_lab import (
                persist_forecast_lab_to_hub,
            )
            from src.trade.hub_bridge import _index_doc_to_panel

            if persist_forecast_lab_to_hub(key, lab_dict):
                doc = load_index_research_json(key)
                if doc is not None:
                    artifact = _index_doc_to_panel(doc)
                    artifact["asset_type"] = "index"
        return IndexForecastLabResponse(
            status="ok",
            ticker=key,
            result=lab_dict,
            artifact=artifact,
        )
    except Exception as exc:
        logger.exception("index-prediction forecast-lab failed for %s", key)
        raise HTTPException(status_code=502, detail=redacted_error_detail(exc)) from exc


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


@trade_router.get("/index-prediction/execution-backtest", response_model=IndexExecutionBacktestResponse)
def get_index_execution_backtest(
    ticker: str = "NIFTY",
    track: str = "quant_ridge",
    strategy: str = "futures_trend",
    refresh: bool = False,
    _auth: None = Depends(require_local_or_auth),
) -> IndexExecutionBacktestResponse:
    """Load or compute execution simulation from track scoreboard."""
    from trade_integrations.dataflows.index_research.prediction_algorithms.config import exec_sim_enabled

    if not exec_sim_enabled():
        return IndexExecutionBacktestResponse(
            status="disabled",
            ticker=_ticker_key(ticker),
            message="Set INDEX_PREDICTION_EXEC_SIM_ENABLED=1",
        )
    key = _ticker_key(ticker)
    try:
        from trade_integrations.dataflows.index_research.execution_sim.runner import (
            execution_backtest_path,
            run_execution_backtest,
        )
        from src.trade.hub_bridge import ensure_trade_stack_path

        ensure_trade_stack_path()
        path = execution_backtest_path(key)
        if refresh or not path.is_file():
            report = run_execution_backtest(
                ticker=key,
                track_id=(track or "quant_ridge").strip(),
                strategy=(strategy or "futures_trend").strip(),
                persist=True,
            )
        else:
            import json

            report = json.loads(path.read_text(encoding="utf-8"))
        status = str(report.get("status") or "ok")
        return IndexExecutionBacktestResponse(status=status, ticker=key, report=report)
    except Exception as exc:
        logger.exception("index-prediction execution-backtest failed for %s", key)
        raise HTTPException(status_code=502, detail=redacted_error_detail(exc)) from exc


@trade_router.get("/index-prediction/miss-analysis", response_model=IndexMissAnalysisResponse)
def get_index_prediction_miss_analysis(
    ticker: str = "NIFTY",
    refresh: bool = False,
    days: int = 365,
    horizon_days: int | None = None,
    _auth: None = Depends(require_local_or_auth),
) -> IndexMissAnalysisResponse:
    """Load cached prediction miss RCA or recompute from backtest eval rows."""
    key = _ticker_key(ticker)
    try:
        from trade_integrations.dataflows.index_research.prediction_miss_analysis import (
            load_miss_analysis_report,
            run_and_save_miss_analysis,
        )
        from src.trade.hub_bridge import ensure_trade_stack_path

        ensure_trade_stack_path()
        from src.trade.prediction_heavy_pool import run_single_flight

        miss_kwargs = {
            "days": days,
            "horizon_days": horizon_days,
            "ticker": key,
        }
        if refresh:
            flight_key = f"miss-analysis:{key}:{days}:{horizon_days}:refresh=1"
            report = run_single_flight(
                flight_key,
                lambda: run_and_save_miss_analysis(**miss_kwargs),
            )
        else:
            report = load_miss_analysis_report(key)
            if report is None:
                flight_key = f"miss-analysis:{key}:{days}:{horizon_days}:cold"
                report = run_single_flight(
                    flight_key,
                    lambda: run_and_save_miss_analysis(**miss_kwargs),
                )
        status = str(report.get("status") or "ok")
        return IndexMissAnalysisResponse(status=status, ticker=key, report=report)
    except Exception as exc:
        logger.exception("index-prediction miss-analysis failed for %s", key)
        raise HTTPException(status_code=502, detail=redacted_error_detail(exc)) from exc


@trade_router.get("/index-prediction/quant-review", response_model=IndexQuantReviewResponse)
def get_index_quant_review(
    ticker: str = "NIFTY",
    horizon_days: int | None = 14,
    refresh: bool = False,
    _auth: None = Depends(require_local_or_auth),
) -> IndexQuantReviewResponse:
    """Load cached India Quant Reviewer artifact (second opinion vs Ridge)."""
    key = _ticker_key(ticker)
    try:
        from trade_integrations.bridge.quant_review import run_quant_review
        from trade_integrations.context.hub import (
            is_quant_review_cache_fresh,
            load_quant_review_json,
        )
        from src.trade.hub_bridge import ensure_trade_stack_path

        ensure_trade_stack_path()
        if refresh or not is_quant_review_cache_fresh(key):
            review = run_quant_review(key, horizon_days=horizon_days, save=True)
        else:
            review = load_quant_review_json(key)
            if review is None:
                review = run_quant_review(key, horizon_days=horizon_days, save=True)
        return IndexQuantReviewResponse(status="ok", ticker=key, review=review)
    except Exception as exc:
        logger.exception("index-prediction quant-review failed for %s", key)
        raise HTTPException(status_code=502, detail=redacted_error_detail(exc)) from exc


@trade_router.get("/index-prediction/data-audit", response_model=IndexDataAuditResponse)
def get_index_prediction_data_audit(
    ticker: str = "NIFTY",
    refresh: bool = False,
    days: int = 365,
    horizon_days: int = 14,
    _auth: None = Depends(require_local_or_auth),
) -> IndexDataAuditResponse:
    """Load hub data completeness audit for prediction RCA."""
    key = _ticker_key(ticker)
    try:
        from trade_integrations.dataflows.index_research.hub_data_audit import (
            load_data_audit_report,
            run_and_save_data_audit,
        )
        from src.trade.hub_bridge import ensure_trade_stack_path

        ensure_trade_stack_path()
        if refresh:
            report = run_and_save_data_audit(
                days=days,
                horizon_days=horizon_days,
                ticker=key,
            )
        else:
            report = load_data_audit_report(key)
            if report is None:
                report = run_and_save_data_audit(
                    days=days,
                    horizon_days=horizon_days,
                    ticker=key,
                )
        status = str(report.get("status") or "ok")
        return IndexDataAuditResponse(status=status, ticker=key, report=report)
    except Exception as exc:
        logger.exception("index-prediction data-audit failed for %s", key)
        raise HTTPException(status_code=502, detail=redacted_error_detail(exc)) from exc


@trade_router.get("/index-prediction/counterfactual", response_model=IndexCounterfactualResponse)
def get_index_prediction_counterfactual(
    ticker: str = "NIFTY",
    refresh: bool = False,
    days: int = 365,
    horizon_days: int = 14,
    _auth: None = Depends(require_local_or_auth),
) -> IndexCounterfactualResponse:
    """Load cached counterfactual decomposition or recompute from backtest eval rows."""
    key = _ticker_key(ticker)
    try:
        from trade_integrations.dataflows.index_research.prediction_counterfactual import (
            load_counterfactual_report,
            run_and_save_counterfactual,
        )
        from src.trade.hub_bridge import ensure_trade_stack_path

        ensure_trade_stack_path()
        from src.trade.prediction_heavy_pool import run_single_flight

        cf_kwargs = {
            "days": days,
            "horizon_days": horizon_days,
            "ticker": key,
        }
        if refresh:
            flight_key = f"counterfactual:{key}:{days}:{horizon_days}:refresh=1"
            report = run_single_flight(
                flight_key,
                lambda: run_and_save_counterfactual(**cf_kwargs),
            )
        else:
            report = load_counterfactual_report(key)
            if report is None:
                flight_key = f"counterfactual:{key}:{days}:{horizon_days}:cold"
                report = run_single_flight(
                    flight_key,
                    lambda: run_and_save_counterfactual(**cf_kwargs),
                )
        status = str(report.get("status") or "ok")
        return IndexCounterfactualResponse(status=status, ticker=key, report=report)
    except Exception as exc:
        logger.exception("index-prediction counterfactual failed for %s", key)
        raise HTTPException(status_code=502, detail=redacted_error_detail(exc)) from exc


@trade_router.get("/index-prediction/news-impact", response_model=IndexNewsImpactResponse)
def get_index_prediction_news_impact(
    ticker: str = "NIFTY",
    refresh: bool = False,
    horizon_days: int = 14,
    include_rejected: bool = False,
    _auth: None = Depends(require_local_or_auth),
) -> IndexNewsImpactResponse:
    """Verified news → Nifty impact snapshot from hub SSOT.

    Default load is hub-read-only (``resolve_news_impact``). ``refresh=true`` runs
    index-level ingest for NIFTY only (tiered sources allowed) — not Nifty-50 batch.
    """
    key = _ticker_key(ticker)
    try:
        from trade_integrations.context.hub import load_index_research_json
        from trade_integrations.dataflows import news_hub_bridge
        from src.trade.hub_bridge import ensure_trade_stack_path

        ensure_trade_stack_path()
        spot: float | None = None
        macro: dict[str, float] = {}
        doc = load_index_research_json(key)
        if doc is not None:
            spot = float(getattr(doc, "spot", 0) or 0) or None
            for row in getattr(doc, "global_factors", None) or []:
                if row.get("factor") is not None and row.get("value") is not None:
                    macro[str(row["factor"])] = float(row["value"])

        if refresh:
            report = news_hub_bridge.refresh_news_impact(
                ticker=key,
                horizon_days=horizon_days,
                spot=spot,
                macro_factors=macro or None,
                refresh_ingest=True,
                include_rejected=include_rejected, market=market_for_ticker(key),
            )
        else:
            report = news_hub_bridge.resolve_news_impact(
                ticker=key, doc=doc, limit=12, horizon_days=horizon_days, market=market_for_ticker(key)
            )
        status = str((report or {}).get("status") or "ok")
        return IndexNewsImpactResponse(status=status, ticker=key, report=report)
    except Exception as exc:
        logger.exception("index-prediction news-impact failed for %s", key)
        raise HTTPException(status_code=502, detail=redacted_error_detail(exc)) from exc


@trade_router.get("/index-prediction/news-impact/factor-timeline")
def get_index_prediction_news_factor_timeline(
    ticker: str = "NIFTY",
    factor_id: str = "",
    shock_pct: float | None = None,
    horizon_days: int = 14,
    _auth: None = Depends(require_local_or_auth),
) -> dict:
    """Single-factor up/down point-impact-over-time timeline for one top-ranked news factor.

    Anchored to the current index doc's spot; no cascading/second-order effects (see
    news_factor_scenario.py's module docstring for why).
    """
    key = _ticker_key(ticker)
    if not factor_id.strip():
        raise HTTPException(status_code=400, detail="factor_id is required")
    try:
        from trade_integrations.dataflows import news_hub_bridge

        result = news_hub_bridge.factor_scenario_timeline(
            factor_id.strip(),
            ticker=key,
            shock_pct=shock_pct,
            horizon_days=horizon_days,
        )
        return result
    except Exception as exc:
        logger.exception("index-prediction news factor-timeline failed for %s/%s", key, factor_id)
        raise HTTPException(status_code=502, detail=redacted_error_detail(exc)) from exc


@trade_router.post("/index-prediction/news-scenarios/session", response_model=NewsScenarioSessionResponse)
def create_news_scenario_session(
    body: NewsScenarioSessionRequest,
    _auth: None = Depends(require_local_or_auth),
) -> NewsScenarioSessionResponse:
    """Create or resume a news-scenario advisor Vibe session bound to pipeline_as_of."""
    key = _ticker_key(body.ticker)
    pipeline_as_of = (body.pipeline_as_of or "").strip()
    if not pipeline_as_of:
        raise HTTPException(status_code=400, detail="pipeline_as_of is required")
    if body.session_id:
        safe_path_component(body.session_id, "session_id")
    try:
        from src.api.state import _get_session_service
        from src.trade.hub_bridge import ensure_trade_stack_path
        from trade_integrations.dataflows.index_research.pipeline_snapshot import (
            normalize_as_of,
            resolve_bound_pipeline_doc,
        )

        ensure_trade_stack_path()
        resolve_bound_pipeline_doc(key, pipeline_as_of)

        svc = _get_session_service()
        if svc is None:
            raise HTTPException(status_code=503, detail="session runtime not enabled")

        bound = normalize_as_of(pipeline_as_of)

        if body.session_id:
            existing = svc.get_session(body.session_id)
            if existing is None:
                raise HTTPException(status_code=404, detail="session not found")
            if str((existing.config or {}).get("session_kind") or "") != "news_scenario_advisor":
                raise HTTPException(status_code=403, detail="not a news scenario session")
            existing_as_of = normalize_as_of((existing.config or {}).get("pipeline_as_of"))
            if existing_as_of == bound:
                return NewsScenarioSessionResponse(
                    session_id=existing.session_id,
                    pipeline_as_of=pipeline_as_of,
                    ticker=key,
                )
        else:
            for existing in svc.list_sessions(limit=200):
                cfg = existing.config or {}
                if str(cfg.get("session_kind") or "") != "news_scenario_advisor":
                    continue
                if str(cfg.get("pipeline_ticker") or "NIFTY").upper() != key:
                    continue
                if normalize_as_of(cfg.get("pipeline_as_of")) == bound:
                    return NewsScenarioSessionResponse(
                        session_id=existing.session_id,
                        pipeline_as_of=pipeline_as_of,
                        ticker=key,
                    )

        session = svc.create_session(
            title=f"news-scenario:{key}",
            config={
                "session_kind": "news_scenario_advisor",
                "pipeline_ticker": key,
                "pipeline_as_of": pipeline_as_of,
                "horizon_days": body.horizon_days or 14,
                "system_note": (
                    "News Predictions advisor — use pipeline tools only; "
                    "load_skill news-scenario-advisor on first turn."
                ),
            },
        )
        return NewsScenarioSessionResponse(
            session_id=session.session_id,
            pipeline_as_of=pipeline_as_of,
            ticker=key,
        )
    except HTTPException:
        raise
    except Exception as exc:
        from trade_integrations.dataflows.index_research.pipeline_snapshot import (
            MissingSnapshotError,
            StaleSnapshotError,
        )

        if isinstance(exc, (MissingSnapshotError, StaleSnapshotError)):
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        logger.exception("news-scenario session create failed for %s", key)
        raise HTTPException(status_code=502, detail=redacted_error_detail(exc)) from exc


@trade_router.patch(
    "/index-prediction/news-scenarios/session/{session_id}",
    response_model=NewsScenarioSessionResponse,
)
def patch_news_scenario_session(
    session_id: str,
    body: NewsScenarioSessionPatchRequest,
    _auth: None = Depends(require_local_or_auth),
) -> NewsScenarioSessionResponse:
    """Update date_range / selection fields on a news-scenario session."""
    try:
        from src.api.state import _get_session_service

        svc = _get_session_service()
        if svc is None:
            raise HTTPException(status_code=503, detail="session runtime not enabled")
        session = svc.get_session(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail="session not found")
        cfg = dict(session.config or {})
        if str(cfg.get("session_kind") or "") != "news_scenario_advisor":
            raise HTTPException(status_code=403, detail="not a news scenario session")
        if body.date_range is not None:
            from trade_integrations.dataflows.news_hub_bridge import (
                validate_scenario_date_range,
            )

            cfg["date_range"] = validate_scenario_date_range(body.date_range)
        if body.selected_outcome_id is not None:
            cfg["selected_outcome_id"] = body.selected_outcome_id
        if body.active_draft_id is not None:
            cfg["active_draft_id"] = body.active_draft_id
        if body.active_scenario_id is not None:
            cfg["active_scenario_id"] = body.active_scenario_id
        session.config = cfg
        svc.store.update_session(session)
        return NewsScenarioSessionResponse(
            session_id=session.session_id,
            pipeline_as_of=str(cfg.get("pipeline_as_of") or ""),
            ticker=str(cfg.get("pipeline_ticker") or "NIFTY"),
        )
    except HTTPException:
        raise
    except Exception as exc:
        from trade_integrations.dataflows.news_hub_bridge import NewsScenarioError

        if isinstance(exc, NewsScenarioError):
            raise HTTPException(status_code=400, detail=exc.to_dict()) from exc
        logger.exception("news-scenario session patch failed")
        raise HTTPException(status_code=502, detail=redacted_error_detail(exc)) from exc


@trade_router.get("/index-prediction/news-scenarios/recent", response_model=NewsEventScenarioResponse)
def list_news_scenarios(
    ticker: str = "NIFTY",
    limit: int = 10,
    _auth: None = Depends(require_local_or_auth),
) -> NewsEventScenarioResponse:
    key = _ticker_key(ticker)
    try:
        from src.trade.hub_bridge import ensure_trade_stack_path
        from trade_integrations.dataflows.news_hub_bridge import (
            list_recent_news_scenarios,
        )

        ensure_trade_stack_path()
        rows = list_recent_news_scenarios(key, limit=limit)
        return NewsEventScenarioResponse(status="ok", ticker=key, scenarios=rows)
    except Exception as exc:
        logger.exception("list news scenarios failed for %s", key)
        raise HTTPException(status_code=502, detail=redacted_error_detail(exc)) from exc


@trade_router.get(
    "/index-prediction/news-scenarios/{scenario_id}",
    response_model=NewsEventScenarioResponse,
)
def get_news_scenario(
    scenario_id: str,
    ticker: str = "NIFTY",
    _auth: None = Depends(require_local_or_auth),
) -> NewsEventScenarioResponse:
    key = _ticker_key(ticker)
    try:
        from src.trade.hub_bridge import ensure_trade_stack_path
        from trade_integrations.dataflows.news_hub_bridge import (
            load_news_event_scenario,
        )

        ensure_trade_stack_path()
        scenario = load_news_event_scenario(key, scenario_id)
        if scenario is None:
            return NewsEventScenarioResponse(
                status="not_found",
                ticker=key,
                message=f"Scenario {scenario_id} not found",
            )
        return NewsEventScenarioResponse(status="ok", ticker=key, scenario=scenario)
    except Exception as exc:
        logger.exception("get news scenario failed for %s", scenario_id)
        raise HTTPException(status_code=502, detail=redacted_error_detail(exc)) from exc


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
    try:
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
    except Exception as exc:
        logger.exception("get external predictions failed for %s", key)
        raise HTTPException(status_code=502, detail=redacted_error_detail(exc)) from exc


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
    try:
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
    except Exception as exc:
        logger.exception("refresh external predictions failed for %s", key)
        raise HTTPException(status_code=502, detail=redacted_error_detail(exc)) from exc


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
    try:
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
    except Exception as exc:
        logger.exception("list external prediction sources failed")
        raise HTTPException(status_code=502, detail=redacted_error_detail(exc)) from exc


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
    try:
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
    except Exception as exc:
        logger.exception("remove external prediction source failed")
        raise HTTPException(status_code=502, detail=redacted_error_detail(exc)) from exc


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
    try:
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
    except Exception as exc:
        logger.exception("discover external prediction sources failed")
        raise HTTPException(status_code=502, detail=redacted_error_detail(exc)) from exc


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


@trade_router.get("/index-prediction/verified-news", response_model=IndexVerifiedNewsResponse)
def get_index_verified_news(
    ticker: str = "NIFTY",
    since: str | None = None,
    until: str | None = None,
    day: str | None = None,
    symbols: str | None = None,
    topics: str | None = None,
    factors: str | None = None,
    themes: str | None = None,
    tags: str | None = None,
    include_rejected: bool = False,
    inventory: bool = False,
    limit: int = 25,
    _auth: None = Depends(require_local_or_auth),
) -> IndexVerifiedNewsResponse:
    """Filter verified hub news by date, symbol, topic, factor, or theme tags."""
    key = _ticker_key(ticker)
    since, until, day = _query_window(since, until, day)
    row_limit = _query_limit(limit, maximum=100)

    def _csv(raw: str | None) -> list[str] | None:
        if not raw:
            return None
        return [part.strip() for part in raw.split(",") if part.strip()]

    try:
        from trade_integrations.dataflows import news_hub_bridge
        from src.trade.hub_bridge import ensure_trade_stack_path

        ensure_trade_stack_path()
        inv = news_hub_bridge.tag_inventory(ticker=key) if inventory else None
        items = news_hub_bridge.query_verified_news(
            as_of=news_hub_bridge.now_instant(),
            ticker=key,
            since=since,
            until=until,
            publish_day=day,
            symbols=_csv(symbols),
            topics=_csv(topics),
            factors=_csv(factors),
            themes=_csv(themes),
            tags=_csv(tags),
            include_rejected=include_rejected,
            limit=row_limit,
        )
        items = [news_hub_bridge.without_legacy_forecast(item) for item in items]  # D410: one honesty rule, not a copy
        return IndexVerifiedNewsResponse(
            status="ok",
            ticker=key,
            count=len(items),
            items=items,
            inventory=inv,
        )
    except Exception as exc:
        logger.exception("index-prediction verified-news failed for %s", key)
        raise HTTPException(status_code=502, detail=redacted_error_detail(exc)) from exc


@trade_router.get("/index-prediction/day-attribution", response_model=DayAttributionResponse)
def get_index_day_attribution(
    date: str,
    days: int = 365,
    _auth: None = Depends(require_local_or_auth),
) -> DayAttributionResponse:
    """Explain factor and calendar drivers for one Nifty trading day."""
    try:
        from trade_integrations.dataflows.index_research.day_attribution import explain_nifty_day
        from src.trade.hub_bridge import ensure_trade_stack_path

        ensure_trade_stack_path()
        payload = explain_nifty_day(date, history_days=max(30, min(days, 365)))
        status = str(payload.get("status") or "ok")
        if status == "error":
            return DayAttributionResponse(status="error", message=str(payload.get("message") or "failed"))
        if status == "not_found":
            return DayAttributionResponse(
                status="not_found",
                date=str(payload.get("date") or date),
                message=str(payload.get("message") or "Date not found"),
            )
        return DayAttributionResponse(status="ok", date=str(payload.get("date") or date), attribution=payload)
    except Exception as exc:
        logger.exception("day-attribution failed for %s", date)
        raise HTTPException(status_code=502, detail=redacted_error_detail(exc)) from exc


_INDEX_PREDICTION_RUN_POLL_SECONDS = 0.5
_INDEX_PREDICTION_RUN_HEARTBEAT_SECONDS = 15.0


def _index_prediction_run_sse_frame(event: str, data: dict[str, Any]) -> str:
    payload = json.dumps(data, ensure_ascii=False, default=str)
    return f"event: {event}\ndata: {payload}\n\n"


async def _reconcile_and_read_job(
    reconcile: Callable[[str], Any],
    get_record: Callable[[str], dict[str, Any] | None],
    job_id: str,
) -> dict[str, Any] | None:
    """One SSE poll tick of a file-backed job store, run off the event loop thread.

    ``reconcile`` probes the worker PID and may rewrite the job file; ``get_record`` reads
    the job file from disk and takes the store's ``_JOBS_LOCK``. Called from an async
    generator every poll tick per connected client, so on the loop thread either one would
    freeze every request on the process while it ran, ``/health`` included. Same fix as the
    Command Center legs (fork 19f04199).
    """

    def _poll() -> dict[str, Any] | None:
        reconcile(job_id)
        return get_record(job_id)

    return await asyncio.to_thread(_poll)


async def _sse_event_stream(
    *,
    job_id: str,
    request: Request,
    reconcile: Callable[[str], Any],
    get_record: Callable[[str], dict[str, Any] | None],
    frame: Callable[[str, dict[str, Any]], str],
    log_to_events: Callable[[dict[str, Any]], "list[tuple[str, dict[str, Any]]]"],
    terminal: Callable[[dict[str, Any]], "tuple[str, dict[str, Any]] | None"],
    heartbeat: Callable[[str, str], str],
    heartbeat_seconds: float,
    poll_seconds: float,
):
    """Generic poll-loop skeleton shared by the three job-store SSE endpoints
    (index-prediction run, external-predictions refresh, recording): replay
    buffered logs off the last-seen index, then poll the job store until
    ``terminal(job)`` reports a frame to emit and end the stream.

    Genuinely different per caller behavior stays caller-owned via hooks
    rather than being forced into one shape here: ``log_to_events`` (external
    predictions turns a ``source_complete`` log entry into a differently-
    shaped event instead of a raw ``log`` frame), ``terminal`` (each system's
    own done/error payload field name and status set), and ``heartbeat``
    (index-prediction/recording emit a bare SSE comment, external-predictions
    emits a real named ``heartbeat`` event with a JSON body).
    """
    import time as time_mod

    last_log_idx = 0
    last_emit = time_mod.monotonic()
    while True:
        if await request.is_disconnected():
            return

        job = await _reconcile_and_read_job(reconcile, get_record, job_id)
        if job is None:
            yield frame("error", {"message": "job not found"})
            return

        logs = list(job.get("logs") or [])
        while last_log_idx < len(logs):
            for event, data in log_to_events(logs[last_log_idx]):
                yield frame(event, data)
            last_log_idx += 1
            last_emit = time_mod.monotonic()

        done = terminal(job)
        if done is not None:
            event, data = done
            yield frame(event, data)
            return

        if time_mod.monotonic() - last_emit >= heartbeat_seconds:
            yield heartbeat(job_id, str(job.get("status") or ""))
            last_emit = time_mod.monotonic()

        await asyncio.sleep(poll_seconds)


def _index_prediction_run_log_to_events(entry: dict[str, Any]) -> "list[tuple[str, dict[str, Any]]]":
    return [("log", {"entry": entry})]


def _index_prediction_run_terminal(job: dict[str, Any]) -> "tuple[str, dict[str, Any]] | None":
    status = str(job.get("status") or "")
    if status in ("done", "done_with_warnings"):
        artifact = job.get("artifact")
        ticker = str(job.get("ticker") or "")
        if artifact is not None:
            return (
                "done",
                {"ticker": ticker, "artifact": artifact, "warnings": job.get("warnings") or []},
            )
        return ("error", {"message": "job completed without artifact"})
    if status == "error":
        return ("error", {"message": job.get("error") or "unknown error"})
    return None


def _index_prediction_run_heartbeat(job_id: str, status: str) -> str:
    return ": keepalive\n\n"


async def _index_prediction_run_event_stream(job_id: str, request: Request):
    """Replay stored logs then poll job store until done/error."""
    from src.trade.index_prediction_run_jobs import _get_job_record, reconcile_job

    async for chunk in _sse_event_stream(
        job_id=job_id,
        request=request,
        reconcile=reconcile_job,
        get_record=_get_job_record,
        frame=_index_prediction_run_sse_frame,
        log_to_events=_index_prediction_run_log_to_events,
        terminal=_index_prediction_run_terminal,
        heartbeat=_index_prediction_run_heartbeat,
        heartbeat_seconds=_INDEX_PREDICTION_RUN_HEARTBEAT_SECONDS,
        poll_seconds=_INDEX_PREDICTION_RUN_POLL_SECONDS,
    ):
        yield chunk


def _index_prediction_run_stream_response(job_id: str, request: Request) -> StreamingResponse:
    return StreamingResponse(
        _index_prediction_run_event_stream(job_id, request),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


_COMMAND_CENTER_POSITIONS_POLL_SECONDS = 5.0
_COMMAND_CENTER_PREDICTION_POLL_SECONDS = 20.0
_COMMAND_CENTER_NEWS_POLL_SECONDS = 20.0
_COMMAND_CENTER_HEARTBEAT_SECONDS = 15.0
_COMMAND_CENTER_TICK_SECONDS = 1.0


def _command_center_sse_frame(event: str, data: dict[str, Any]) -> str:
    payload = json.dumps(data, ensure_ascii=False, default=str)
    return f"event: {event}\ndata: {payload}\n\n"


def _command_center_snapshot_hash(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, default=str, sort_keys=True)


async def _command_center_event_stream(agent_id: str, ticker: str, request: Request):
    """Server-push replacement for Command Center's client-side poll timers +
    manual refresh buttons — per user request (see
    [[2026-08-28-command-center-real-time-push]]). Each of the 3 data legs (positions,
    prediction, news) is polled server-side on its own cadence — matching this module's
    established job-store-poll-as-SSE pattern (see `_index_prediction_run_event_stream`
    above) since none of positions/prediction/news has a real change-notification hook to
    piggyback on (confirmed while scoping: positions is recomputed fresh from the broker per
    call with no cache; the prediction artifact is written by a scheduled pipeline with no
    publish hook; news has no pub/sub either) — but a snapshot is only ever emitted to the
    client when it actually differs from the last one sent, so this isn't just polling
    relabeled as push: an unchanged tick produces no frame, no re-render, no flicker.
    """
    import time as time_mod

    from nautilus_openalgo_bridge.live_pop import compute_live_pop_for_agent

    last_positions_snapshot: str | None = None
    last_prediction_snapshot: str | None = None
    last_news_snapshot: str | None = None
    last_positions_poll = 0.0
    last_prediction_poll = 0.0
    last_news_poll = 0.0
    last_emit = time_mod.monotonic()

    while True:
        if await request.is_disconnected():
            return
        now = time_mod.monotonic()

        if agent_id and now - last_positions_poll >= _COMMAND_CENTER_POSITIONS_POLL_SECONDS:
            last_positions_poll = now
            try:
                positions = await asyncio.to_thread(compute_live_pop_for_agent, agent_id)
                snapshot = _command_center_snapshot_hash(positions)
                if snapshot != last_positions_snapshot:
                    last_positions_snapshot = snapshot
                    yield _command_center_sse_frame("positions", positions)
                    last_emit = now
            except Exception as exc:  # noqa: BLE001 — one leg's failure must not kill the stream
                logger.warning("command-center stream: positions poll failed", exc_info=exc)
                yield _command_center_sse_frame("positions_error", {"message": redacted_error_detail(exc)})
                last_emit = now

        if now - last_prediction_poll >= _COMMAND_CENTER_PREDICTION_POLL_SECONDS:
            last_prediction_poll = now
            try:
                from src.trade.hub_bridge import load_hub_plan_artifact

                def _load_prediction() -> Any:
                    artifact = load_hub_plan_artifact(ticker, "index")
                    _attach_latest_forecast_fan(artifact, ticker)
                    return artifact

                artifact = await asyncio.to_thread(_load_prediction)
                snapshot = _command_center_snapshot_hash(artifact)
                if snapshot != last_prediction_snapshot:
                    last_prediction_snapshot = snapshot
                    yield _command_center_sse_frame(
                        "prediction", {"status": "ok", "ticker": ticker, "artifact": artifact}
                    )
                    last_emit = now
            except Exception as exc:  # noqa: BLE001
                logger.warning("command-center stream: prediction poll failed", exc_info=exc)
                yield _command_center_sse_frame("prediction_error", {"message": redacted_error_detail(exc)})
                last_emit = now

        if now - last_news_poll >= _COMMAND_CENTER_NEWS_POLL_SECONDS:
            last_news_poll = now
            try:
                from trade_integrations.context.hub import load_index_research_json
                from trade_integrations.dataflows import news_hub_bridge

                def _load_news() -> Any:
                    doc = load_index_research_json(ticker)
                    return news_hub_bridge.resolve_news_impact(
                        ticker=ticker,
                        doc=doc,
                        limit=12,
                        horizon_days=7,
                        force_rebuild=True,
                        market=market_for_ticker(ticker),
                    )

                report = await asyncio.to_thread(_load_news)
                snapshot = _command_center_snapshot_hash(report)
                if snapshot != last_news_snapshot:
                    last_news_snapshot = snapshot
                    yield _command_center_sse_frame("news", {"status": "ok", "ticker": ticker, "report": report})
                    last_emit = now
            except Exception as exc:  # noqa: BLE001
                logger.warning("command-center stream: news poll failed", exc_info=exc)
                yield _command_center_sse_frame("news_error", {"message": redacted_error_detail(exc)})
                last_emit = now

        if now - last_emit >= _COMMAND_CENTER_HEARTBEAT_SECONDS:
            yield ": keepalive\n\n"
            last_emit = now

        await asyncio.sleep(_COMMAND_CENTER_TICK_SECONDS)


@trade_router.get("/command-center/stream", dependencies=[Depends(require_event_stream_auth)])
async def command_center_stream(
    request: Request,
    agent_id: str = "",
    ticker: str = "NIFTY",
) -> StreamingResponse:
    """SSE: push positions/prediction/news updates to the Command Center dashboard as they
    change, so the page never needs a manual refresh button or client-side poll timer."""
    key = _ticker_key(ticker)
    return StreamingResponse(
        _command_center_event_stream(agent_id.strip(), key, request),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


def _kick_index_prediction_run(body: RunIndexPredictionRequest) -> tuple[str, str, bool]:
    from src.trade.index_prediction_run_jobs import _STORE, spawn_worker, start_job

    key = _ticker_key(body.ticker)
    job_id, reused = start_job(
        ticker=key,
        horizon_days=body.horizon_days,
        refresh_constituents=body.refresh_constituents,
        run_forecast_lab=body.run_forecast_lab,
    )
    _STORE.ensure_worker(job_id, reused, spawn_worker)
    from src.trade.index_prediction_run_jobs import get_job

    snap = get_job(job_id) or {}
    return job_id, str(snap.get("status") or "queued"), reused


@trade_router.post(
    "/index-prediction/run/start",
    response_model=IndexPredictionRunStartResponse,
    status_code=202,
)
def start_index_prediction_run(
    body: RunIndexPredictionRequest,
    _auth: None = Depends(require_local_or_auth),
) -> IndexPredictionRunStartResponse:
    """Queue index research pipeline and return a trackable job_id."""
    job_id, job_status, reused = _kick_index_prediction_run(body)
    return IndexPredictionRunStartResponse(
        job_id=job_id,
        job_status=job_status,
        reused=reused,
    )


@trade_router.get("/index-prediction/run/active", response_model=IndexPredictionRunActiveResponse)
def get_active_index_prediction_run(
    ticker: str = "NIFTY",
    _auth: None = Depends(require_local_or_auth),
) -> IndexPredictionRunActiveResponse:
    from src.trade.index_prediction_run_jobs import get_active_job

    snap = get_active_job(ticker)
    if snap is None:
        return IndexPredictionRunActiveResponse(job=None)
    return IndexPredictionRunActiveResponse(job=IndexPredictionRunJobSnapshot(**snap))


@trade_router.get("/index-prediction/run/{job_id}", response_model=IndexPredictionRunJobResponse)
def get_index_prediction_run_job(
    job_id: str,
    _auth: None = Depends(require_local_or_auth),
) -> IndexPredictionRunJobResponse:
    from src.trade.index_prediction_run_jobs import get_job, job_id_valid

    if not job_id_valid(job_id):
        raise HTTPException(status_code=400, detail="invalid job_id")
    snap = get_job(job_id)
    if snap is None:
        raise HTTPException(status_code=404, detail=f"job {job_id} not found")
    return IndexPredictionRunJobResponse(job=IndexPredictionRunJobSnapshot(**snap))


@trade_router.post("/index-prediction/run/{job_id}/cancel")
def cancel_index_prediction_run(
    job_id: str,
    _auth: None = Depends(require_local_or_auth),
) -> dict[str, str]:
    """Request cooperative cancel for an in-flight manual run."""
    from trade_integrations.dataflows.index_research.pipeline_cancel import request_pipeline_cancel
    from src.trade.index_prediction_run_jobs import _ACTIVE_STATUSES, _get_job_record, _now_iso, append_log, job_id_valid

    if not job_id_valid(job_id):
        raise HTTPException(status_code=400, detail="invalid job_id")
    job = _get_job_record(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"job {job_id} not found")
    status = str(job.get("status") or "")
    if status not in _ACTIVE_STATUSES:
        raise HTTPException(status_code=409, detail=f"job is not active (status={status})")
    request_pipeline_cancel(f"user_cancel:{job_id}", job_id=job_id)
    append_log(
        job_id,
        {
            "stage": "cancel",
            "message": "Cancel requested — stopping pipeline…",
            "level": "warn",
            "at": _now_iso(),
        },
    )
    return {"status": "ok", "message": "cancel requested"}


@trade_router.get("/index-prediction/run/{job_id}/stream")
async def stream_index_prediction_run_job(
    job_id: str,
    request: Request,
    _auth: None = Depends(require_local_or_auth),
) -> StreamingResponse:
    """SSE: replay pipeline logs and stream until the run terminates."""
    from src.trade.index_prediction_run_jobs import _get_job_record, job_id_valid

    if not job_id_valid(job_id):
        raise HTTPException(status_code=400, detail="invalid job_id")
    if await asyncio.to_thread(_get_job_record, job_id) is None:
        raise HTTPException(status_code=404, detail=f"job {job_id} not found")
    return _index_prediction_run_stream_response(job_id, request)


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


@trade_router.get("/markets/registry")
def get_markets_registry(
    _auth: None = Depends(require_local_or_auth),
) -> dict[str, Any]:
    """Supported markets + their headline indices, for the frontend's market picker.

    Sourced from `market_registry.py` (the Layer-1 shared registry) rather than
    duplicating the country/index list in the frontend.
    """
    from trade_integrations.market_registry import SUPPORTED_MARKETS, get_market

    markets = []
    for code in SUPPORTED_MARKETS:
        spec = get_market(code)
        markets.append(
            {
                "code": spec.code,
                "currency": spec.currency,
                "timezone": spec.timezone,
                "indices": [idx.name for idx in spec.indices],
            }
        )
    return {"status": "ok", "markets": markets}


@trade_router.get("/markets/{country}/index/{index}")
def get_market_index_history(
    country: str,
    index: str,
    period: str = "1y",
    _auth: None = Depends(require_local_or_auth),
) -> dict[str, Any]:
    """Historical index OHLCV for a non-India market — proxies `stock_simulator`'s
    global-markets vertical (`/history/{country}/index/{index}`)."""
    return _run_control(lambda c: c.get_market_index_history(country=country, index=index, period=period))


@trade_router.get("/markets/{country}/live_spot/{index}")
def get_market_live_spot(
    country: str,
    index: str,
    max_age_seconds: int | None = None,
    force_refresh: bool = False,
    _auth: None = Depends(require_local_or_auth),
) -> dict[str, Any]:
    return _run_control(
        lambda c: c.get_live_market_spot(
            country=country, index=index, max_age_seconds=max_age_seconds, force_refresh=force_refresh
        )
    )


@trade_router.get("/markets/{country}/factors/{series}")
def get_market_policy_factors(
    country: str,
    series: str,
    _auth: None = Depends(require_local_or_auth),
) -> dict[str, Any]:
    return _run_control(lambda c: c.get_policy_factors(country=country, series=series))


@trade_router.get("/markets/{country}/flow/{series}")
def get_market_flow_of_funds(
    country: str,
    series: str,
    _auth: None = Depends(require_local_or_auth),
) -> dict[str, Any]:
    return _run_control(lambda c: c.get_flow_of_funds(country=country, series=series))


@trade_router.get("/markets/{country}/economy/{series}")
def get_market_economy_factor(
    country: str,
    series: str,
    _auth: None = Depends(require_local_or_auth),
) -> dict[str, Any]:
    """Economy factor (GDP growth, fiscal balance, current-account balance, unemployment,
    consumption share of GDP, industrial production, PMI manufacturing) for any of the 7
    `market_registry` markets — proxies `stock_simulator`'s `/history/{country}/economy/{series}`,
    added for the Economy tab frontend ([[2026-08-23-economy-section-frontend-ui]])."""
    return _run_control(lambda c: c.get_economy_factor(country=country, series=series))


@trade_router.get("/markets/{country}/sector_indices")
def get_market_sector_indices(
    country: str,
    _auth: None = Depends(require_local_or_auth),
) -> dict[str, Any]:
    """List of sector/headline indices TradingView has wired for a market — proxies
    `stock_simulator`'s `/history/{country}/sector_indices`, added for the sector-indices
    frontend wiring ([[2026-08-23-tradingview-sector-constituent-integration]])."""
    return _run_control(lambda c: c.get_market_sector_indices(country=country))


@trade_router.get("/markets/{country}/bundle")
def get_market_bundle(
    country: str,
    period: str = "3mo",
    _auth: None = Depends(require_local_or_auth),
) -> dict[str, Any]:
    """Headline + sector index histories for a market in one request — proxies
    `stock_simulator`'s `/history/{country}/bundle`. Replaces the per-index `index/{index}`
    fan-out `GlobalMarketsPanel`/`SectorIndicesPanel` used to do (up to ~15 requests for a market
    like US) — the frontend already calls this route, but it was never wired up here, so every
    non-India market card surfaced as "Not Found" (see
    2026-08-27-global-markets-non-india-bundle-route-missing)."""
    return _run_control(lambda c: c.get_market_bundle(country=country, period=period))


@trade_router.get("/markets/{country}/top_constituents")
def get_market_top_constituents(
    country: str,
    top_n: int = 10,
    _auth: None = Depends(require_local_or_auth),
) -> dict[str, Any]:
    """Top-N constituents by market cap for a market — proxies `stock_simulator`'s
    `/history/{country}/top_constituents`. Not sourced for CN/RU/US."""
    return _run_control(lambda c: c.get_market_top_constituents(country=country, top_n=top_n))


@trade_router.get("/markets/factor_coverage")
def get_market_factor_coverage(
    _auth: None = Depends(require_local_or_auth),
) -> dict[str, Any]:
    return _run_control(lambda c: c.get_market_factor_coverage())


@trade_router.get("/markets/{country}/replay/calendar")
def get_market_replay_calendar(
    country: str,
    lookback_days: int | None = None,
    before: str | None = None,
    _auth: None = Depends(require_local_or_auth),
) -> dict[str, Any]:
    """Per-day `market_ticks` presence for a non-India market's indices — proxies
    `stock_simulator`'s `/history/{country}/replay/calendar`, driving the per-country
    Replay/Data-coverage calendar (the India-tab analog of `/replay/calendar` above).

    `lookback_days`/`before` let the frontend page further back in history a window at a time
    instead of always seeing the most recent `lookback_days` days (see `history_data.py`'s
    route docstring)."""
    return _run_control(
        lambda c: c.get_market_replay_calendar(
            country=country, lookback_days=lookback_days, before=before,
        )
    )


@trade_router.get("/markets/global_macro/refreshable_series")
def list_market_global_macro_refreshable_series(
    _auth: None = Depends(require_local_or_auth),
) -> dict[str, Any]:
    """Registered before `/markets/global_macro/{series}` below so `refreshable_series` isn't
    swallowed as a `series` path parameter (same ordering rule the auto-record route above
    already follows for `/recording/{job_id}`)."""
    return _run_control(lambda c: c.list_eod_refreshable_series())


@trade_router.get("/markets/global_macro/ui_cards")
def get_market_global_macro_ui_cards(
    _auth: None = Depends(require_local_or_auth),
) -> dict[str, Any]:
    """Card metadata for `GlobalMarketsPanel.tsx`'s GLOBAL/CURRENCY tabs, generated from the
    factor registry — registered before `/markets/global_macro/{series}` below for the same
    routing reason as `refreshable_series` above."""
    return _run_control(lambda c: c.get_global_macro_ui_cards())


@trade_router.get("/markets/global_macro/{series}")
def get_market_global_macro(
    series: str,
    start: str | None = None,
    end: str | None = None,
    field: str | None = None,
    _auth: None = Depends(require_local_or_auth),
) -> dict[str, Any]:
    """Historical read for a cross-market `global_macro_store` series — currencies
    (`usd_inr`/`usd_cny`/`usd_jpy`/`usd_rub`/`usd_sar`/`usd_brl`) and global factors
    (`gold`, `oil_brent_daily`, `oil_wti_daily`, `vix_daily`, `us_10y`, `sp500`). Proxies
    `stock_simulator`'s `/history/global_macro`, not the per-country `/markets/{country}/...`
    dispatch — these series aren't owned by any one market."""
    return _run_control(lambda c: c.get_global_macro(series=series, start=start, end=end, field=field))


@trade_router.get("/markets/global_macro/{series}/live_spot")
def get_market_global_macro_live_spot(
    series: str,
    max_age_seconds: int | None = None,
    force_refresh: bool = False,
    _auth: None = Depends(require_local_or_auth),
) -> dict[str, Any]:
    return _run_control(
        lambda c: c.get_live_macro_spot(series=series, max_age_seconds=max_age_seconds, force_refresh=force_refresh)
    )


@trade_router.post("/markets/global_macro/{series}/refresh")
def refresh_market_global_macro(
    series: str,
    lookback_days: int = 90,
    _auth: None = Depends(require_local_or_auth),
) -> dict[str, Any]:
    """On-demand EOD-history backfill for a cross-market `global_macro_store` series
    (currencies included) — populates what `GET /markets/global_macro/{series}` reads back.
    Nothing calls this automatically; the caller decides when to (re)backfill."""
    return _run_control(lambda c: c.refresh_global_macro_eod(series=series, lookback_days=lookback_days))


class StartMarketTickRecordingRequest(BaseModel):
    kind: Literal["fx", "index"]
    country: str | None = None
    symbols: list[str] | None = None
    interval_seconds: float


@trade_router.post("/markets/recording/start")
def start_market_tick_recording(
    body: StartMarketTickRecordingRequest,
    _auth: None = Depends(require_local_or_auth),
) -> dict[str, Any]:
    """User-triggered append-only tick recording for FX pairs or a non-India market's
    indices — separate from the always-on `stock_simulator` capture supervisors, which only
    write one overwritten value per factor per day. See
    `stock_simulator/recorder/tick_recorder.py` for the actual poll loop."""
    return _run_control(
        lambda c: c.start_tick_recording(
            kind=body.kind, country=body.country, symbols=body.symbols, interval_seconds=body.interval_seconds
        )
    )


@trade_router.post("/markets/recording/{job_id}/stop")
def stop_market_tick_recording(
    job_id: str,
    _auth: None = Depends(require_local_or_auth),
) -> dict[str, Any]:
    return _run_control(lambda c: c.stop_tick_recording(job_id))


@trade_router.get("/markets/recording/active")
def list_market_tick_recordings(
    _auth: None = Depends(require_local_or_auth),
) -> dict[str, Any]:
    return _run_control(lambda c: c.list_tick_recordings())


class ArmMultiMarketRequest(BaseModel):
    markets: list[str]
    start_utc: str | None = None
    end_utc: str | None = None
    speed: float = 1.0
    loop: bool = False


class SeekMultiMarketRequest(BaseModel):
    time: str


class SetMultiMarketSpeedRequest(BaseModel):
    speed: float


@trade_router.post("/markets/multi_market/arm")
def arm_multi_market(
    body: ArmMultiMarketRequest,
    _auth: None = Depends(require_local_or_auth),
) -> dict[str, Any]:
    """Arm a cross-market simultaneous replay session — one UTC master clock watching several
    markets at once. See `stock_simulator/multi_market_replay.py`'s module docstring: this is
    live-forward tick data + backfilled daily closes only, not a deep historical intraday scrub
    for non-India markets yet."""
    return _run_control(
        lambda c: c.arm_multi_market_replay(
            markets=body.markets,
            start_utc=body.start_utc,
            end_utc=body.end_utc,
            speed=body.speed,
            loop=body.loop,
        )
    )


@trade_router.get("/markets/multi_market/status")
def get_multi_market_status(
    _auth: None = Depends(require_local_or_auth),
) -> dict[str, Any]:
    return _run_control(lambda c: c.get_multi_market_status())


@trade_router.post("/markets/multi_market/pause")
def pause_multi_market(
    _auth: None = Depends(require_local_or_auth),
) -> dict[str, Any]:
    return _run_control(lambda c: c.pause_multi_market_replay())


@trade_router.post("/markets/multi_market/resume")
def resume_multi_market(
    _auth: None = Depends(require_local_or_auth),
) -> dict[str, Any]:
    return _run_control(lambda c: c.resume_multi_market_replay())


@trade_router.post("/markets/multi_market/seek")
def seek_multi_market(
    body: SeekMultiMarketRequest,
    _auth: None = Depends(require_local_or_auth),
) -> dict[str, Any]:
    return _run_control(lambda c: c.seek_multi_market_replay(time=body.time))


@trade_router.post("/markets/multi_market/speed")
def set_multi_market_speed(
    body: SetMultiMarketSpeedRequest,
    _auth: None = Depends(require_local_or_auth),
) -> dict[str, Any]:
    return _run_control(lambda c: c.set_multi_market_replay_speed(speed=body.speed))


@trade_router.post("/markets/multi_market/stop")
def stop_multi_market(
    _auth: None = Depends(require_local_or_auth),
) -> dict[str, Any]:
    return _run_control(lambda c: c.stop_multi_market_replay())


@trade_router.get("/markets/multi_market/quote")
def get_multi_market_quote(
    market: str,
    symbol: str,
    exchange: str,
    _auth: None = Depends(require_local_or_auth),
) -> dict[str, Any]:
    return _run_control(lambda c: c.get_multi_market_quote(market=market, symbol=symbol, exchange=exchange))


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


_LIGHT_REFRESH_LOCK = threading.Lock()


def _light_refresh_skip_reason(key: str) -> str | None:
    """Why a non-forced poll must not run now: `replay_armed`, `market_closed` or `simulator_unavailable`."""
    from src.trade.hub_bridge import ensure_trade_stack_path

    ensure_trade_stack_path()
    from nautilus_openalgo_bridge.market_hours import is_market_open_for_market
    from trade_integrations.dataflows.news_hub_bridge.replay_gate import is_replaying
    from trade_integrations.market_registry import market_for_ticker

    market = market_for_ticker(key)
    try:
        if is_replaying(market):
            return "replay_armed"
    except Exception:  # simulator cannot answer: polling blind during a possible replay is worse than skipping
        logger.warning("light refresh skipped: replay state unknown", exc_info=True)
        return "simulator_unavailable"
    return None if is_market_open_for_market(market) else "market_closed"


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


@trade_router.get("/index-prediction/history", response_model=IndexPredictionHistoryResponse)
def get_index_prediction_history(
    ticker: str = "NIFTY",
    limit: int = 50,
    horizon_days: int | None = None,
    _auth: None = Depends(require_local_or_auth),
) -> IndexPredictionHistoryResponse:
    """Prediction-ledger rows (last run per day + today's intraday revisions) for the ledger-only columns
    (bottom-up / macro / direction hit); the headline forecast history is read from /forecast-replay."""
    key = _ticker_key(ticker)
    try:
        from trade_integrations.dataflows.prediction_ledger_bridge import list_forecast_history_bundle

        bundle = list_forecast_history_bundle(
            key,
            limit=max(1, min(limit, 200)),
            horizon_days=horizon_days,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("index-prediction history failed for %s", key)
        raise HTTPException(status_code=502, detail=redacted_error_detail(exc)) from exc
    return IndexPredictionHistoryResponse(
        status="ok",
        ticker=key,
        daily=bundle["daily"],
        intraday=bundle.get("intraday") or [],
        meta=bundle.get("meta") or {},
    )


_FORECAST_REPLAY_MAX_DAYS = 400
_FORECAST_REPLAY_MAX_SOURCES = 40
_FORECAST_REPLAY_MAX_HORIZON = 366
_FORECAST_REPLAY_PROVENANCE = ("live", "replay", "backtest")


def _csv(value: str | None) -> list[str]:
    return list(dict.fromkeys(v.strip() for v in (value or "").split(",") if v.strip()))


@trade_router.get("/index-prediction/forecast-replay", response_model=ForecastReplayResponse)
def get_index_prediction_forecast_replay(
    ticker: str = "NIFTY",
    sources: str | None = None,
    start: str | None = None,
    end: str | None = None,
    horizon: int = 14,
    provenance: str = "live",
    revisions: bool = False,
    _auth: None = Depends(require_local_or_auth),
) -> ForecastReplayResponse:
    """Every forecast source's records joined to realised closes (read-only; one bad family is a 200)."""
    sent = (ticker or "NIFTY").strip()  # an unsupported ticker is echoed in the 422 exactly as sent
    key = sent.upper()
    try:
        end_d = datetime.strptime(end, "%Y-%m-%d").date() if end else datetime.now(timezone.utc).date()
        start_d = datetime.strptime(start, "%Y-%m-%d").date() if start else end_d - timedelta(days=90)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"start/end must be YYYY-MM-DD: {exc}") from exc
    if start_d > end_d:
        raise HTTPException(status_code=422, detail=f"start {start_d} is after end {end_d}")
    if (end_d - start_d).days > _FORECAST_REPLAY_MAX_DAYS:
        raise HTTPException(status_code=422, detail=f"window is limited to {_FORECAST_REPLAY_MAX_DAYS} days")
    if not 1 <= horizon <= _FORECAST_REPLAY_MAX_HORIZON:
        raise HTTPException(status_code=422, detail=f"horizon must be 1..{_FORECAST_REPLAY_MAX_HORIZON}")
    source_ids = _csv(sources)
    if len(source_ids) > _FORECAST_REPLAY_MAX_SOURCES:
        raise HTTPException(status_code=422, detail=f"at most {_FORECAST_REPLAY_MAX_SOURCES} sources per request")
    prov = _csv(provenance) or ["live"]
    if bad := [p for p in prov if p not in _FORECAST_REPLAY_PROVENANCE]:
        raise HTTPException(status_code=422, detail=f"unknown provenance {bad}; valid: {list(_FORECAST_REPLAY_PROVENANCE)}")
    try:
        from trade_integrations.dataflows.prediction_ledger_bridge import build_forecast_replay, forecast_replay_error_text

        bundle = build_forecast_replay(
            sent, source_ids or None, start_d.isoformat(), end_d.isoformat(), horizon, prov, revisions
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=forecast_replay_error_text(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=forecast_replay_error_text(exc)) from exc
    except Exception as exc:
        logger.exception("forecast replay failed for %s", key)
        raise HTTPException(status_code=502, detail=forecast_replay_error_text(exc)) from exc
    return ForecastReplayResponse(status="ok", ticker=key, **bundle)


@trade_router.get("/index-prediction/factor-history", response_model=IndexFactorHistoryResponse)
def get_index_factor_history(
    ticker: str = "NIFTY",
    days: int = 90,
    start: str | None = None,
    factors: str | None = None,
    _auth: None = Depends(require_local_or_auth),
) -> IndexFactorHistoryResponse:
    """Return macro factor time series for historical charts."""
    key = _ticker_key(ticker)
    try:
        from trade_integrations.dataflows.prediction_ledger_bridge import (
            list_factor_history_series,
        )
        from src.trade.hub_bridge import ensure_trade_stack_path

        ensure_trade_stack_path()
        factor_list = [f.strip() for f in factors.split(",") if f.strip()] if factors else None
        payload = list_factor_history_series(
            days=max(7, min(days, 5000)),
            start=start,
            factors=factor_list,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("index-prediction factor-history failed for %s", key)
        raise HTTPException(status_code=502, detail=redacted_error_detail(exc)) from exc

    return IndexFactorHistoryResponse(
        status="ok",
        ticker=key,
        series=payload.get("series", []),
        factors=payload.get("factors", []),
        coverage=payload.get("coverage") or {},
        coverage_notes=payload.get("coverage_notes") or [],
    )


@trade_router.get("/index-prediction/forecast-engine-recipes", response_model=ForecastEngineRecipesResponse)
def get_forecast_engine_recipes(
    _auth: None = Depends(require_local_or_auth),
) -> ForecastEngineRecipesResponse:
    """Every forecast_engine recipe registered in `forecast_engine.registry` (backend-defined,
    docs/add/forecast_engine.md) -- what the UI's dataset selector lists."""
    try:
        from src.trade.hub_bridge import ensure_trade_stack_path

        ensure_trade_stack_path()
        from trade_integrations.forecast_engine.registry import DEFAULT_RECIPE_NAME, RECIPES

        recipes = [
            ForecastEngineRecipeInfo(
                name=r.name,
                version=r.version,
                covariate_keys=list(r.covariate_keys),
                target=r.target,
                description=r.description,
            )
            for r in RECIPES.values()
        ]
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("forecast-engine-recipes failed")
        raise HTTPException(status_code=502, detail=redacted_error_detail(exc)) from exc

    return ForecastEngineRecipesResponse(status="ok", recipes=recipes, default_recipe=DEFAULT_RECIPE_NAME)


@trade_router.get("/index-prediction/forecast-engine-latest", response_model=ForecastEngineArtifactResponse)
def get_forecast_engine_latest(
    ticker: str = "NIFTY",
    recipe: str | None = None,
    _auth: None = Depends(require_local_or_auth),
) -> ForecastEngineArtifactResponse:
    """The forecast_engine module's cached latest forecast for one recipe
    (docs/add/forecast_engine.md) -- dataset covariates + a TimesFM point/quantile forecast,
    refreshed offline by scripts/run_forecast_engine_latest.py. `recipe` defaults to the
    registry's default recipe when omitted. `available=False` (not a 404/502) when no artifact
    has been generated yet -- this is an expected, normal state, not a service failure."""
    key = _ticker_key(ticker)
    try:
        from src.trade.hub_bridge import ensure_trade_stack_path

        ensure_trade_stack_path()
        from trade_integrations.forecast_engine.artifact import read_latest_forecast
        from trade_integrations.forecast_engine.registry import DEFAULT_RECIPE_NAME

        recipe_name = (recipe or DEFAULT_RECIPE_NAME).strip()
        artifact = read_latest_forecast(key, recipe_name)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("forecast-engine-latest failed for %s", key)
        raise HTTPException(status_code=502, detail=redacted_error_detail(exc)) from exc

    if artifact is None:
        return ForecastEngineArtifactResponse(
            status="ok", available=False, ticker=key, recipe_name=recipe_name, message="no artifact yet"
        )

    return ForecastEngineArtifactResponse(
        status="ok",
        available=True,
        ticker=artifact.ticker,
        recipe_name=artifact.recipe_name,
        recipe_version=artifact.recipe_version,
        engine_checkpoint=artifact.engine_checkpoint,
        horizon_days=artifact.horizon_days,
        as_of=artifact.as_of,
        generated_at=artifact.generated_at,
        spot=artifact.spot,
        expected_return_pct=artifact.expected_return_pct,
        quantiles_pct=artifact.quantiles_pct,
        dataset=artifact.dataset,
        covariate_keys=artifact.covariate_keys,
    )


@trade_router.get("/index-prediction/forecast-engine-evaluation", response_model=ForecastEngineEvaluationResponse)
def get_forecast_engine_evaluation(
    ticker: str = "NIFTY",
    recipe: str | None = None,
    _auth: None = Depends(require_local_or_auth),
) -> ForecastEngineEvaluationResponse:
    """One recipe's walk-forward backtest history -- predicted vs actual NIFTY over past forecast
    cutoffs (docs/add/forecast_engine.md rule 5), refreshed offline by the same job as the latest
    forecast. `available=False` when no evaluation has been run for this recipe yet."""
    key = _ticker_key(ticker)
    try:
        from src.trade.hub_bridge import ensure_trade_stack_path

        ensure_trade_stack_path()
        from trade_integrations.forecast_engine.artifact import read_evaluation_artifact
        from trade_integrations.forecast_engine.registry import DEFAULT_RECIPE_NAME

        recipe_name = (recipe or DEFAULT_RECIPE_NAME).strip()
        artifact = read_evaluation_artifact(key, recipe_name)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("forecast-engine-evaluation failed for %s", key)
        raise HTTPException(status_code=502, detail=redacted_error_detail(exc)) from exc

    if artifact is None:
        return ForecastEngineEvaluationResponse(
            status="ok", available=False, ticker=key, recipe_name=recipe_name, message="no evaluation yet"
        )

    return ForecastEngineEvaluationResponse(
        status="ok",
        available=True,
        ticker=artifact.ticker,
        recipe_name=artifact.recipe_name,
        recipe_version=artifact.recipe_version,
        engine_checkpoint=artifact.engine_checkpoint,
        horizon_days=artifact.horizon_days,
        generated_at=artifact.generated_at,
        n_observations=artifact.n_observations,
        n_skipped_gaps=artifact.n_skipped_gaps,
        mean_pinball_loss=artifact.mean_pinball_loss,
        mean_naive_pinball_loss=artifact.mean_naive_pinball_loss,
        passes_gate=artifact.passes_gate,
        gate_lower_bound=artifact.gate_lower_bound,
        observations=artifact.observations,
    )


@trade_router.post(
    "/index-prediction/forecast-engine-run/start",
    response_model=ForecastEngineRunStartResponse,
    status_code=202,
)
def start_forecast_engine_run(
    body: ForecastEngineRunStartRequest,
    _auth: None = Depends(require_local_or_auth),
) -> ForecastEngineRunStartResponse:
    """Queue an on-demand forecast_engine evaluation run over a user-chosen date range and
    return a trackable job_id + the run_id it will be filed under once done."""
    from src.trade.hub_bridge import ensure_trade_stack_path

    ensure_trade_stack_path()
    from src.trade.forecast_engine_run_jobs import kick_forecast_engine_run

    job_id, run_id, job_status, reused = kick_forecast_engine_run(
        ticker=body.ticker, recipe=body.recipe, start=body.start, end=body.end, horizon=body.horizon
    )
    return ForecastEngineRunStartResponse(job_id=job_id, run_id=run_id, job_status=job_status, reused=reused)


@trade_router.get(
    "/index-prediction/forecast-engine-run/{job_id}", response_model=ForecastEngineRunJobResponse
)
def get_forecast_engine_run_job(
    job_id: str,
    _auth: None = Depends(require_local_or_auth),
) -> ForecastEngineRunJobResponse:
    from src.trade.forecast_engine_run_jobs import get_job, job_id_valid

    if not job_id_valid(job_id):
        raise HTTPException(status_code=400, detail="invalid job_id")
    snap = get_job(job_id)
    if snap is None:
        raise HTTPException(status_code=404, detail=f"job {job_id} not found")
    return ForecastEngineRunJobResponse(job=ForecastEngineRunJobSnapshot(**snap))


_FORECAST_ENGINE_RUN_POLL_SECONDS = 0.5
_FORECAST_ENGINE_RUN_HEARTBEAT_SECONDS = 15.0


def _forecast_engine_run_sse_frame(event: str, data: dict[str, Any]) -> str:
    payload = json.dumps(data, ensure_ascii=False, default=str)
    return f"event: {event}\ndata: {payload}\n\n"


def _forecast_engine_run_log_to_events(entry: dict[str, Any]) -> "list[tuple[str, dict[str, Any]]]":
    return [("log", {"entry": entry})]


def _forecast_engine_run_terminal(job: dict[str, Any]) -> "tuple[str, dict[str, Any]] | None":
    status = str(job.get("status") or "")
    if status == "done":
        return ("done", {"result": job.get("result")})
    if status == "error":
        return ("error", {"message": job.get("error") or "unknown error"})
    return None


def _forecast_engine_run_heartbeat(job_id: str, status: str) -> str:
    return ": keepalive\n\n"


async def _forecast_engine_run_event_stream(job_id: str, request: Request):
    """Replay stored logs then poll job store until done/error."""
    from src.trade.forecast_engine_run_jobs import _get_job_record, reconcile_job

    async for chunk in _sse_event_stream(
        job_id=job_id,
        request=request,
        reconcile=reconcile_job,
        get_record=_get_job_record,
        frame=_forecast_engine_run_sse_frame,
        log_to_events=_forecast_engine_run_log_to_events,
        terminal=_forecast_engine_run_terminal,
        heartbeat=_forecast_engine_run_heartbeat,
        heartbeat_seconds=_FORECAST_ENGINE_RUN_HEARTBEAT_SECONDS,
        poll_seconds=_FORECAST_ENGINE_RUN_POLL_SECONDS,
    ):
        yield chunk


@trade_router.get("/index-prediction/forecast-engine-run/{job_id}/stream")
async def stream_forecast_engine_run_job(
    job_id: str,
    request: Request,
    _auth: None = Depends(require_local_or_auth),
) -> StreamingResponse:
    """SSE: replay run logs and stream until the run terminates."""
    from src.trade.forecast_engine_run_jobs import _get_job_record, job_id_valid

    if not job_id_valid(job_id):
        raise HTTPException(status_code=400, detail="invalid job_id")
    if await asyncio.to_thread(_get_job_record, job_id) is None:
        raise HTTPException(status_code=404, detail=f"job {job_id} not found")
    return StreamingResponse(
        _forecast_engine_run_event_stream(job_id, request),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@trade_router.get("/index-prediction/forecast-engine-runs", response_model=ForecastEngineRunListResponse)
def get_forecast_engine_runs(
    ticker: str = "NIFTY",
    recipe: str = "",
    _auth: None = Depends(require_local_or_auth),
) -> ForecastEngineRunListResponse:
    """Past named runs for one recipe -- newest first."""
    key = _ticker_key(ticker)
    try:
        from src.trade.hub_bridge import ensure_trade_stack_path

        ensure_trade_stack_path()
        from trade_integrations.forecast_engine.artifact import list_runs
        from trade_integrations.forecast_engine.registry import DEFAULT_RECIPE_NAME

        recipe_name = (recipe or DEFAULT_RECIPE_NAME).strip()
        runs = list_runs(key, recipe_name)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("forecast-engine-runs failed for %s", key)
        raise HTTPException(status_code=502, detail=redacted_error_detail(exc)) from exc

    return ForecastEngineRunListResponse(runs=[ForecastEngineRunSummary(**r) for r in runs])


@trade_router.get(
    "/index-prediction/forecast-engine-run-detail", response_model=ForecastEngineEvaluationResponse
)
def get_forecast_engine_run_detail(
    ticker: str = "NIFTY",
    recipe: str = "",
    run_id: str = "",
    _auth: None = Depends(require_local_or_auth),
) -> ForecastEngineEvaluationResponse:
    """One past run's full evaluation detail -- same field shape as
    ``/index-prediction/forecast-engine-evaluation`` (the "latest" evaluation), just for a named
    historical run instead of the always-overwritten latest one."""
    key = _ticker_key(ticker)
    try:
        from src.trade.hub_bridge import ensure_trade_stack_path

        ensure_trade_stack_path()
        from trade_integrations.forecast_engine.artifact import read_run_artifact
        from trade_integrations.forecast_engine.registry import DEFAULT_RECIPE_NAME

        recipe_name = (recipe or DEFAULT_RECIPE_NAME).strip()
        artifact = read_run_artifact(key, recipe_name, run_id)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("forecast-engine-run-detail failed for %s/%s", key, run_id)
        raise HTTPException(status_code=502, detail=redacted_error_detail(exc)) from exc

    if artifact is None:
        return ForecastEngineEvaluationResponse(
            status="ok", available=False, ticker=key, recipe_name=recipe_name, message="run not found"
        )

    return ForecastEngineEvaluationResponse(
        status="ok",
        available=True,
        ticker=artifact.ticker,
        recipe_name=artifact.recipe_name,
        recipe_version=artifact.recipe_version,
        engine_checkpoint=artifact.engine_checkpoint,
        horizon_days=artifact.horizon_days,
        generated_at=artifact.generated_at,
        n_observations=artifact.n_observations,
        n_skipped_gaps=artifact.n_skipped_gaps,
        mean_pinball_loss=artifact.mean_pinball_loss,
        mean_naive_pinball_loss=artifact.mean_naive_pinball_loss,
        passes_gate=artifact.passes_gate,
        gate_lower_bound=artifact.gate_lower_bound,
        observations=artifact.observations,
    )


@trade_router.get("/index-prediction/constituent-history", response_model=ConstituentHistoryResponse)
def get_constituent_history(
    symbol: str,
    days: int = 90,
    weight: float | None = None,
    _auth: None = Depends(require_local_or_auth),
) -> ConstituentHistoryResponse:
    """Return archived company research trend for one Nifty constituent."""
    key = _ticker_key(symbol, default="")
    if not key:
        return ConstituentHistoryResponse(status="error", message="symbol required")
    try:
        from trade_integrations.dataflows.index_research.constituent_history import (
            build_constituent_history_series,
        )
        from src.trade.hub_bridge import ensure_trade_stack_path

        ensure_trade_stack_path()
        payload = build_constituent_history_series(key, days=max(7, min(days, 365)), weight=weight)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("constituent-history failed for %s", key)
        raise HTTPException(status_code=502, detail=redacted_error_detail(exc)) from exc

    return ConstituentHistoryResponse(
        status="ok",
        symbol=payload.get("symbol", key),
        days=int(payload.get("days") or days),
        snapshot_count=int(payload.get("snapshot_count") or 0),
        has_research_archive=bool(payload.get("has_research_archive")),
        points=payload.get("points") or [],
    )


@trade_router.get("/index-prediction/jobs", response_model=IndexPredictionJobsResponse)
def get_index_prediction_jobs(
    _auth: None = Depends(require_local_or_auth),
) -> IndexPredictionJobsResponse:
    """List scheduled cron jobs that feed the prediction pipeline."""
    try:
        from src.trade.index_prediction_jobs import list_index_prediction_jobs
        from src.trade.hub_bridge import ensure_trade_stack_path

        ensure_trade_stack_path()
        payload = list_index_prediction_jobs()
    except Exception as exc:
        logger.exception("index-prediction jobs list failed")
        raise HTTPException(status_code=502, detail=redacted_error_detail(exc)) from exc
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
    try:
        from src.trade.index_prediction_jobs import pause_index_prediction_job
        from src.trade.hub_bridge import ensure_trade_stack_path

        ensure_trade_stack_path()
        payload = pause_index_prediction_job(job_id)
    except Exception as exc:
        logger.exception("pause index job %s failed", job_id)
        raise HTTPException(status_code=502, detail=redacted_error_detail(exc)) from exc
    if payload.get("status") == "error":
        return IndexPredictionJobsResponse(status="error", message=str(payload.get("message") or "not found"))
    return IndexPredictionJobsResponse(status="ok", job=payload.get("job"))


@trade_router.post("/index-prediction/jobs/{job_id}/resume", response_model=IndexPredictionJobsResponse)
def resume_index_prediction_job_route(
    job_id: str,
    _auth: None = Depends(require_local_or_auth),
) -> IndexPredictionJobsResponse:
    """Resume a paused index prediction cron job."""
    try:
        from src.trade.index_prediction_jobs import resume_index_prediction_job
        from src.trade.hub_bridge import ensure_trade_stack_path

        ensure_trade_stack_path()
        payload = resume_index_prediction_job(job_id)
    except Exception as exc:
        logger.exception("resume index job %s failed", job_id)
        raise HTTPException(status_code=502, detail=redacted_error_detail(exc)) from exc
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
    try:
        from src.trade.index_prediction_jobs import trigger_index_prediction_job
        from src.trade.hub_bridge import ensure_trade_stack_path

        ensure_trade_stack_path()
        payload = trigger_index_prediction_job(job_id)
    except Exception as exc:
        logger.exception("trigger index job %s failed", job_id)
        raise HTTPException(status_code=502, detail=redacted_error_detail(exc)) from exc
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
    try:
        from src.trade.index_prediction_jobs import cancel_index_prediction_job
        from src.trade.hub_bridge import ensure_trade_stack_path

        ensure_trade_stack_path()
        payload = cancel_index_prediction_job(job_id)
    except Exception as exc:
        logger.exception("cancel index job %s failed", job_id)
        raise HTTPException(status_code=502, detail=redacted_error_detail(exc)) from exc
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
    try:
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
    except Exception as exc:
        logger.exception("index-prediction upcoming-events history failed for %s", key)
        raise HTTPException(status_code=502, detail=redacted_error_detail(exc)) from exc


@trade_router.get("/agent-debate", response_model=AgentDebateResponse)
def get_agent_debate(
    ticker: str,
    _auth: None = Depends(require_local_or_auth),
) -> AgentDebateResponse:
    """Load cached TradingAgents debate summary from the hub."""
    key = _ticker_key(ticker, default="")
    if not key:
        raise HTTPException(status_code=400, detail="ticker required")
    try:
        from src.trade.hub_bridge import is_debate_running, load_debate_artifact
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    if is_debate_running(key):
        return AgentDebateResponse(status="running", ticker=key, running=True)
    debate = load_debate_artifact(key)
    if debate is None:
        return AgentDebateResponse(status="not_found", ticker=key, message="No agent debate yet")
    return AgentDebateResponse(status="ok", ticker=key, debate=debate)


@trade_router.post("/run-debate", response_model=AgentDebateResponse)
def run_debate(
    body: RunDebateRequest,
    _auth: None = Depends(require_local_or_auth),
) -> AgentDebateResponse:
    """Start TradingAgents multi-agent debate (async) or return cached hub summary."""
    key = _ticker_key(body.ticker, default="")
    if not key:
        raise HTTPException(status_code=400, detail="ticker required")
    asset_type = (body.asset_type or "options").strip().lower()
    try:
        from src.trade.hub_bridge import (
            ensure_trade_stack_path,
            is_debate_running,
            load_debate_artifact,
            run_agent_debate_sync,
        )

        ensure_trade_stack_path()
        if not body.refresh:
            from trade_integrations.context.hub import is_agent_debate_cache_fresh

            cached = load_debate_artifact(key)
            if cached and is_agent_debate_cache_fresh(key):
                return AgentDebateResponse(status="ok", ticker=key, debate=cached)
        if is_debate_running(key):
            return AgentDebateResponse(status="running", ticker=key, running=True)

        session_id = (body.session_id or "").strip()

        def _worker() -> None:
            try:
                debate = run_agent_debate_sync(key, asset_type=asset_type)
                if session_id:
                    from src.api.state import _get_session_service  # noqa: PLC0415

                    svc = _get_session_service()
                    if svc:
                        svc.event_bus.emit(
                            session_id,
                            "research.debate",
                            {"ticker": key, "status": "ready", "debate": debate},
                        )
            except Exception as exc:
                logger.exception("Background run-debate failed for %s", key)
                if session_id:
                    try:
                        from src.api.state import _get_session_service  # noqa: PLC0415

                        svc = _get_session_service()
                        if svc:
                            svc.event_bus.emit(
                                session_id,
                                "research.debate",
                                {"ticker": key, "status": "error", "message": redacted_error_detail(exc)},
                            )
                    except Exception:
                        logger.exception("could not relay the run-debate failure for %s to session %s", key, session_id)

        threading.Thread(target=_worker, daemon=True, name=f"debate-{key}").start()
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("run-debate failed for %s", key)
        raise HTTPException(status_code=502, detail=redacted_error_detail(exc)) from exc
    return AgentDebateResponse(status="running", ticker=key, running=True)


# ============================================================
# /trade/hub/{market-data,macro-factors,index-history,constituents}/
# ============================================================
# All endpoints are thin pass-throughs to dataflows.stock_history_bridge.
# The bridge is the single source of truth for in-process Python callers;
# HTTP is for cross-process callers (Vibe frontend).

class HubMarketDataTicksResponse(BaseModel):
    status: str
    symbol: str
    exchange: str
    source: str
    ticks: list[dict[str, Any]] = []
    error: str | None = None


class HubMarketDataSpotResponse(BaseModel):
    status: str
    symbol: str
    exchange: str
    spot: dict[str, Any] | None = None
    # Whether the underlying's real trading session is open right now (IST,
    # weekday + simulator-forced-open aware). None when the check itself
    # failed — the UI should not claim either state in that case. Lets the
    # frontend tell "market closed" apart from "broker unreachable", which
    # otherwise render identically as an empty spot quote.
    session_open: bool | None = None
    error: str | None = None


class HubMarketDataOptionChainStrike(BaseModel):
    strike: float
    ce: dict[str, Any] | None = None
    pe: dict[str, Any] | None = None


class HubMarketDataOptionChainResponse(BaseModel):
    status: str
    underlying: str
    exchange: str
    expiry_date: str | None = None
    underlying_ltp: float | None = None
    underlying_prev_close: float | None = None
    strikes: list[HubMarketDataOptionChainStrike] = []
    source: str = "openalgo"
    error: str | None = None


class HubMarketDataOptionExpiriesResponse(BaseModel):
    status: str
    underlying: str
    exchange: str
    expiries: list[str] = []
    error: str | None = None


class HubMacroFactorPanelResponse(BaseModel):
    status: str
    start: str
    end: str
    rows: list[dict[str, Any]] = []
    columns: list[str] = []
    error: str | None = None


class HubMacroFactorLatestResponse(BaseModel):
    status: str
    day: str | None = None
    factors: dict[str, float | None] | None = None
    error: str | None = None


class HubMacroFactorDatesResponse(BaseModel):
    status: str
    dates: list[str] = []
    error: str | None = None


class HubIndexHistoryDaysResponse(BaseModel):
    status: str
    symbol: str
    exchange: str
    days: list[str] = []
    error: str | None = None


class HubIndexHistoryExpiriesResponse(BaseModel):
    status: str
    symbol: str
    exchange: str
    expiries: list[str] = []
    error: str | None = None


class HubIndexHistoryBarsResponse(BaseModel):
    status: str
    symbol: str
    exchange: str
    bars: list[dict[str, Any]] = []
    truncated: bool = False
    error: str | None = None


class HubConstituentsPanelResponse(BaseModel):
    status: str
    rows: list[dict[str, Any]] = []
    error: str | None = None


class HubStockHistoryCoverageResponse(BaseModel):
    """Per-week, per-bucket availability gate for the stock_simulator.

    `days[*].buckets[name]` carries the per-bucket status (present flag,
    row count, on-disk location, primary_source, fetch_command). When
    `is_complete=False` the UI should render the missing cells as white
    blocks; clicking a cell opens the matching fetch_command.
    """

    status: str
    week_start: str
    week_end: str
    symbol: str
    is_complete: bool
    missing_days: list[str] = []
    bucket_labels: list[str] = []
    days: list[dict[str, Any]] = []
    fetch_list: list[dict[str, Any]] = []
    error: str | None = None


class HubReplayDayOverviewResponse(BaseModel):
    status: str
    date: str
    equities: list[dict[str, Any]] = []
    options: dict[str, list[str]] = {}
    macro_factor_keys: list[str] = []
    constituents_available: bool = False
    error: str | None = None


@trade_router.get("/hub/market-data/ticks", response_model=HubMarketDataTicksResponse)
def hub_market_data_ticks(
    symbol: str = "NIFTY",
    exchange: str = "NSE_INDEX",
    since_minutes: int = 240,
    limit: int = 500,
    replay: int = 0,
    _auth: None = Depends(require_local_or_auth),
) -> HubMarketDataTicksResponse:
    """Recent market_ticks rows for the underlying. Empty list when Timescale disabled.

    When ``replay=1``, route through the simulator's ReplayService so the
    chart shows ticks from the recorded session (advances the sim clock and
    reads the catalog bar at sim_now). Falls back to Timescale on miss.
    """
    if replay:
        from trade_integrations.dataflows.stock_history_bridge import get_replay_market_ticks

        try:
            ticks, reason = get_replay_market_ticks(
                symbol=symbol, exchange=exchange,
                since_minutes=since_minutes, limit=limit,
            )
            return HubMarketDataTicksResponse(
                status="ok", symbol=symbol.upper(), exchange=exchange.upper(),
                source="simulator" if ticks else "empty", ticks=ticks,
                error=reason if not ticks else None,
            )
        except Exception as exc:
            logger.exception("hub market-data ticks (replay) failed for %s/%s", symbol, exchange)
            return HubMarketDataTicksResponse(
                status="error", symbol=symbol.upper(), exchange=exchange.upper(),
                source="empty", ticks=[], error=redacted_error_detail(exc),
            )

    from trade_integrations.dataflows.stock_history_bridge import get_recent_market_ticks

    try:
        ticks = get_recent_market_ticks(
            symbol=symbol, exchange=exchange,
            since_minutes=since_minutes, limit=limit,
        )
        # Each tick's per-row ``source`` field tells the bridge where it
        # came from ("openalgo"/"indmoney_recorder_ws" from the hot tier,
        # "parquet_fallback" from the replay bundle when the hot tier is
        # empty — used to render a chart line even when the recorder
        # hasn't populated TimescaleDB yet, which previously rendered a
        # blank plot). Surface that here so the UI badge can tell the
        # truth rather than silently labelling stale bars as live ticks.
        if ticks and any(t.get("source") == "parquet_fallback" for t in ticks):
            wrapper_source = "parquet_fallback"
        else:
            wrapper_source = "timescale" if ticks else "empty"
        return HubMarketDataTicksResponse(
            status="ok", symbol=symbol.upper(), exchange=exchange.upper(),
            source=wrapper_source, ticks=ticks,
        )
    except Exception as exc:
        logger.exception("hub market-data ticks failed for %s/%s", symbol, exchange)
        return HubMarketDataTicksResponse(
            status="error", symbol=symbol.upper(), exchange=exchange.upper(),
            source="empty", ticks=[], error=redacted_error_detail(exc),
        )


def _spot_unavailable_message() -> str:
    """Why there is no live spot, naming the cause the simulator's /health knows: a capture loop waiting for
    a fresh INDmoney token, or the simulator being down (the tile otherwise says nothing useful)."""
    from trade_integrations.stock_simulator.client import StockSimulatorClient, StockSimulatorClientError

    base = "no spot quote available (Timescale empty and OpenAlgo unreachable)"
    try:
        waiting = StockSimulatorClient().health_check().get("capture_waiting_for_credentials") or []
    except StockSimulatorClientError as exc:
        return f"{base}; stock_simulator unreachable: {exc}"
    if waiting:
        return f"{base}; {', '.join(waiting)} capture is waiting for credentials (paste a fresh INDmoney token)"
    return base


@trade_router.get("/hub/market-data/spot", response_model=HubMarketDataSpotResponse)
def hub_market_data_spot(
    symbol: str = "NIFTY",
    exchange: str = "NSE_INDEX",
    replay: int = 0,
    _auth: None = Depends(require_local_or_auth),
) -> HubMarketDataSpotResponse:
    """Current spot — Timescale first, OpenAlgo fallback. Returns 200 with
    status='error' on degraded state so the UI renders an empty-state cleanly.

    When ``replay=1``, read from the simulator's ReplayService (advances sim
    clock and returns the catalog bar at sim_now). Tag the response with
    ``source=simulator`` so the UI can badge it.
    """
    from trade_integrations.dataflows.stock_history_bridge import (
        get_live_spot_quote,
        get_replay_spot_quote,
    )

    replay_reason: str | None = None
    if replay:
        quote, replay_reason = get_replay_spot_quote(symbol=symbol, exchange=exchange)
    else:
        quote = get_live_spot_quote(symbol=symbol, exchange=exchange)

    session_open: bool | None = None
    if not replay:
        try:
            from trade_integrations.autonomous_agents.market_hours import is_trading_session_open

            session_open = is_trading_session_open(market="IN")
        except Exception:
            logger.exception("session_open check failed for %s/%s", symbol, exchange)

    if quote is None:
        return HubMarketDataSpotResponse(
            status="error", symbol=symbol.upper(), exchange=exchange.upper(),
            session_open=session_open,
            error=replay_reason if replay else _spot_unavailable_message(),
        )
    return HubMarketDataSpotResponse(
        status="ok", symbol=symbol.upper(), exchange=exchange.upper(),
        session_open=session_open,
        spot={
            "symbol": quote.symbol, "exchange": quote.exchange,
            "ltp": quote.ltp, "prev_close": quote.prev_close,
            "bid": quote.bid, "ask": quote.ask,
            "volume": quote.volume, "source": quote.source,
            "as_of": quote.as_of,
        },
    )


@trade_router.get("/hub/market-data/option-chain", response_model=HubMarketDataOptionChainResponse)
def hub_market_data_option_chain(
    symbol: str = "NIFTY",
    exchange: str = "NSE_INDEX",
    strike_count: int = 10,
    expiry_date: str | None = None,
    replay: int = 0,
    _auth: None = Depends(require_local_or_auth),
) -> HubMarketDataOptionChainResponse:
    """Option chain — live from OpenAlgo, or replay from the simulator.

    When ``replay=1``, reads through the simulator's ReplayService (recorded
    parquet chain -> Black-Scholes synthesizer fallback) instead of the live
    broker, which isn't running during a replay session — hitting the live
    path there just times out with nothing to show.
    """
    if replay:
        from trade_integrations.dataflows.stock_history_bridge import get_replay_option_chain

        data, reason = get_replay_option_chain(
            underlying=symbol, exchange=exchange,
            strike_count=strike_count, expiry_date=expiry_date,
        )
        if data is None:
            return HubMarketDataOptionChainResponse(
                status="error", underlying=symbol.upper(), exchange=exchange.upper(),
                error=reason or "no replay chain available (simulator not running?)",
            )
        legs = data.get("chain") or []
        strikes: list[HubMarketDataOptionChainStrike] = []
        for leg in legs:
            if not isinstance(leg, dict):
                continue
            strikes.append(HubMarketDataOptionChainStrike(
                strike=float(leg.get("strike") or leg.get("strike_price") or 0),
                ce={
                    "last_price": leg.get("ce_ltp"), "oi": leg.get("ce_oi"), "volume": leg.get("ce_volume"),
                    "iv": leg.get("ce_iv"), "delta": leg.get("ce_delta"), "gamma": leg.get("ce_gamma"),
                    "theta": leg.get("ce_theta"), "vega": leg.get("ce_vega"),
                },
                pe={
                    "last_price": leg.get("pe_ltp"), "oi": leg.get("pe_oi"), "volume": leg.get("pe_volume"),
                    "iv": leg.get("pe_iv"), "delta": leg.get("pe_delta"), "gamma": leg.get("pe_gamma"),
                    "theta": leg.get("pe_theta"), "vega": leg.get("pe_vega"),
                },
            ))
        return HubMarketDataOptionChainResponse(
            status="ok", underlying=symbol.upper(), exchange=exchange.upper(),
            expiry_date=str(data.get("expiry_date") or "")[:10] or None,
            underlying_ltp=_safe_float(data.get("underlying_ltp") or data.get("spot")),
            underlying_prev_close=None,
            strikes=strikes, source=str(data.get("source") or "simulator"),
        )

    from trade_integrations.dataflows.stock_history_bridge import get_live_option_chain

    data = get_live_option_chain(
        underlying=symbol, exchange=exchange,
        strike_count=strike_count, expiry_date=expiry_date,
    )
    if data is None:
        return HubMarketDataOptionChainResponse(
            status="error", underlying=symbol.upper(), exchange=exchange.upper(),
            error="OpenAlgo returned no chain (broker unreachable?)",
        )
    strikes_raw = data.get("strikes") or data.get("chain") or []
    strikes: list[HubMarketDataOptionChainStrike] = []
    for s in strikes_raw:
        if not isinstance(s, dict):
            continue
        strikes.append(HubMarketDataOptionChainStrike(
            strike=float(s.get("strike") or s.get("strike_price") or 0),
            ce=s.get("ce") if "ce" in s else s,
            pe=s.get("pe") if "pe" in s else s,
        ))
    return HubMarketDataOptionChainResponse(
        status="ok", underlying=symbol.upper(), exchange=exchange.upper(),
        expiry_date=str(data.get("expiry_date") or data.get("expiry") or "")[:10] or None,
        underlying_ltp=_safe_float(data.get("underlying_ltp") or data.get("ltp") or data.get("spot")),
        underlying_prev_close=_safe_float(data.get("underlying_prev_close") or data.get("prev_close")),
        strikes=strikes,
        source=str(data.get("source") or "openalgo"),
    )


@trade_router.get("/hub/market-data/option-expiries", response_model=HubMarketDataOptionExpiriesResponse)
def hub_market_data_option_expiries(
    symbol: str = "NIFTY",
    exchange: str = "NSE_INDEX",
    replay: int = 0,
    _auth: None = Depends(require_local_or_auth),
) -> HubMarketDataOptionExpiriesResponse:
    """Expiries selectable in the option-chain dropdown.

    In replay mode, the next several recorded expiries on/after the
    currently-armed day (see `get_replay_option_expiries` for why this isn't
    scoped to "expiries with a parquet bundle covering this exact day" — that
    predicate collapses to a single entry almost every session). Live mode
    returns every expiry OpenAlgo/the recorder has ever captured for the
    underlying, since there's no "current day" to scope to.
    """
    if replay:
        from trade_integrations.dataflows.stock_history_bridge import get_replay_option_expiries

        expiries, reason = get_replay_option_expiries(underlying=symbol, exchange=exchange)
        if reason and not expiries:
            return HubMarketDataOptionExpiriesResponse(
                status="error", underlying=symbol.upper(), exchange=exchange.upper(),
                expiries=[], error=reason,
            )
        return HubMarketDataOptionExpiriesResponse(
            status="ok", underlying=symbol.upper(), exchange=exchange.upper(), expiries=expiries,
        )

    from trade_integrations.dataflows.stock_history_bridge import list_recorded_option_expiries

    try:
        all_expiries = list_recorded_option_expiries(symbol=symbol, exchange=exchange)
        # `all_expiries` holds every expiry the recorder has ever captured,
        # sorted ascending — over weeks of recording that includes long-
        # expired chains, and the dropdown defaults to index 0, so without
        # this filter it silently opens on the oldest expiry ever recorded
        # instead of the current one. Mirrors the replay path's `e >=
        # replay_date` filter (see `get_replay_option_expiries`), scoped to
        # today since live mode has no replay day to anchor to. Anchored to
        # IST (not UTC) since expiry dates are NSE trading days — using UTC
        # would misfire for ~5.5h/day when the UTC and IST calendar dates
        # differ.
        from zoneinfo import ZoneInfo

        today = datetime.now(ZoneInfo("Asia/Kolkata")).date().isoformat()
        upcoming = sorted(e for e in all_expiries if e >= today)
        return HubMarketDataOptionExpiriesResponse(
            status="ok", underlying=symbol.upper(), exchange=exchange.upper(),
            expiries=upcoming or all_expiries,
        )
    except Exception as exc:
        logger.exception("hub market-data option-expiries failed for %s/%s", symbol, exchange)
        return HubMarketDataOptionExpiriesResponse(
            status="error", underlying=symbol.upper(), exchange=exchange.upper(),
            expiries=[], error=redacted_error_detail(exc),
        )


@trade_router.get("/hub/macro-factors/panel", response_model=HubMacroFactorPanelResponse)
def hub_macro_factor_panel(
    start: str,
    end: str,
    _auth: None = Depends(require_local_or_auth),
) -> HubMacroFactorPanelResponse:
    """Wide-form macro factor panel for [start, end] (YYYY-MM-DD)."""
    from trade_integrations.dataflows.stock_history_bridge import get_macro_factor_panel

    try:
        df = get_macro_factor_panel(start=start, end=end)
        if df.empty:
            return HubMacroFactorPanelResponse(
                status="ok", start=start, end=end, rows=[], columns=[],
            )
        # Replace NaN with None so JSON encoding is clean.
        df_clean = df.where(df.notna(), None)
        rows = df_clean.to_dict(orient="records")
        return HubMacroFactorPanelResponse(
            status="ok", start=start, end=end,
            rows=rows, columns=[str(c) for c in df.columns.tolist()],
        )
    except Exception as exc:
        logger.exception("hub macro-factors panel failed for %s/%s", start, end)
        return HubMacroFactorPanelResponse(
            status="error", start=start, end=end, rows=[], columns=[], error=redacted_error_detail(exc),
        )


@trade_router.get("/hub/macro-factors/latest", response_model=HubMacroFactorLatestResponse)
def hub_macro_factor_latest(
    _auth: None = Depends(require_local_or_auth),
) -> HubMacroFactorLatestResponse:
    """Most recent day's macro factor snapshot, or {status='error'} when no data."""
    from trade_integrations.dataflows.stock_history_bridge import get_latest_macro_snapshot

    snap = get_latest_macro_snapshot()
    if snap is None:
        return HubMacroFactorLatestResponse(
            status="error", day=None, factors=None, error="no macro factor data",
        )
    return HubMacroFactorLatestResponse(
        status="ok", day=snap["day"], factors=snap["factors"],
    )


@trade_router.get("/hub/macro-factors/dates", response_model=HubMacroFactorDatesResponse)
def hub_macro_factor_dates(
    _auth: None = Depends(require_local_or_auth),
) -> HubMacroFactorDatesResponse:
    from trade_integrations.dataflows.stock_history_bridge import list_macro_factor_dates
    try:
        return HubMacroFactorDatesResponse(
            status="ok", dates=list_macro_factor_dates(),
        )
    except Exception as exc:
        logger.exception("hub macro-factors dates failed")
        return HubMacroFactorDatesResponse(
            status="error", dates=[], error=redacted_error_detail(exc),
        )


@trade_router.get("/hub/index-history/days", response_model=HubIndexHistoryDaysResponse)
def hub_index_history_days(
    symbol: str = "NIFTY",
    exchange: str = "NSE_INDEX",
    _auth: None = Depends(require_local_or_auth),
) -> HubIndexHistoryDaysResponse:
    from trade_integrations.dataflows.stock_history_bridge import list_recorded_index_days
    try:
        return HubIndexHistoryDaysResponse(
            status="ok", symbol=symbol.upper(), exchange=exchange.upper(),
            days=list_recorded_index_days(symbol=symbol, exchange=exchange),
        )
    except Exception as exc:
        logger.exception("hub index-history days failed")
        return HubIndexHistoryDaysResponse(
            status="error", symbol=symbol.upper(), exchange=exchange.upper(),
            days=[], error=redacted_error_detail(exc),
        )


@trade_router.get("/hub/index-history/expiries", response_model=HubIndexHistoryExpiriesResponse)
def hub_index_history_expiries(
    symbol: str = "NIFTY",
    exchange: str = "NSE_INDEX",
    _auth: None = Depends(require_local_or_auth),
) -> HubIndexHistoryExpiriesResponse:
    from trade_integrations.dataflows.stock_history_bridge import list_recorded_option_expiries
    try:
        return HubIndexHistoryExpiriesResponse(
            status="ok", symbol=symbol.upper(), exchange=exchange.upper(),
            expiries=list_recorded_option_expiries(symbol=symbol, exchange=exchange),
        )
    except Exception as exc:
        logger.exception("hub index-history expiries failed")
        return HubIndexHistoryExpiriesResponse(
            status="error", symbol=symbol.upper(), exchange=exchange.upper(),
            expiries=[], error=redacted_error_detail(exc),
        )


@trade_router.get("/hub/index-history/bars", response_model=HubIndexHistoryBarsResponse)
def hub_index_history_bars(
    symbol: str = "NIFTY",
    exchange: str = "NSE_INDEX",
    since_ist: str = "2015-01-09T09:15:00",
    until_ist: str = "2026-12-31T15:30:00",
    _auth: None = Depends(require_local_or_auth),
) -> HubIndexHistoryBarsResponse:
    from datetime import datetime
    from trade_integrations.dataflows.stock_history_bridge import get_index_history

    try:
        since_dt = datetime.fromisoformat(since_ist)
        until_dt = datetime.fromisoformat(until_ist)
        bars, truncated = get_index_history(
            symbol=symbol, exchange=exchange,
            since_ist=since_dt, until_ist=until_dt,
        )
        return HubIndexHistoryBarsResponse(
            status="ok", symbol=symbol.upper(), exchange=exchange.upper(), bars=bars,
            truncated=truncated,
        )
    except Exception as exc:
        logger.exception("hub index-history bars failed")
        return HubIndexHistoryBarsResponse(
            status="error", symbol=symbol.upper(), exchange=exchange.upper(),
            bars=[], error=redacted_error_detail(exc),
        )


@trade_router.get("/hub/replay/day-overview", response_model=HubReplayDayOverviewResponse)
def hub_replay_day_overview(
    date: str,
    _auth: None = Depends(require_local_or_auth),
) -> HubReplayDayOverviewResponse:
    """What's on disk for one replay-calendar day: equities, option-chain
    coverage per underlying, macro factor keys, constituents availability.

    Index/equity spot-bar counts for NIFTY/BANKNIFTY/SENSEX are already in the
    calendar payload the UI fetches separately — this fills in everything the
    calendar heatmap doesn't carry.
    """
    from trade_integrations.dataflows.stock_history_bridge import get_replay_day_overview

    try:
        overview = get_replay_day_overview(date=date)
        return HubReplayDayOverviewResponse(status="ok", **overview)
    except Exception as exc:
        logger.exception("hub replay day-overview failed for %s", date)
        return HubReplayDayOverviewResponse(status="error", date=date, error=redacted_error_detail(exc))


@trade_router.get("/hub/constituents/panel", response_model=HubConstituentsPanelResponse)
def hub_constituents_panel(
    start: str | None = None,
    end: str | None = None,
    limit: int = 1000,
    _auth: None = Depends(require_local_or_auth),
) -> HubConstituentsPanelResponse:
    # Constituents live under stock_history.api, reached through the stock_simulator facade
    # (StockSimulatorClient) rather than StockHistory() directly — see
    # .claude/backlog/items/2026-08-23-india-dedicated-methods-retirement.md, Tier 4.
    from trade_integrations.stock_simulator.client import StockSimulatorClient, StockSimulatorClientError

    try:
        resp = StockSimulatorClient().get_constituents_history(start=start, end=end)
        rows = resp["data"][:limit]
        return HubConstituentsPanelResponse(status="ok", rows=rows)
    except StockSimulatorClientError as exc:
        logger.exception("hub constituents panel failed")
        return HubConstituentsPanelResponse(status="error", rows=[], error=redacted_error_detail(exc))


@trade_router.get("/hub/stock-history/coverage", response_model=HubStockHistoryCoverageResponse)
def hub_stock_history_coverage(
    week: str,
    symbol: str = "NIFTY",
    include_optional: int = 0,
    _auth: None = Depends(require_local_or_auth),
) -> HubStockHistoryCoverageResponse:
    """Per-week bucket-coverage snapshot for the stock_simulator.

    `week` is any ISO date in the target ISO week (rounded to Mon..Fri).
    The response carries per-day, per-bucket status with `primary_source`
    and `fetch_command` for every missing bucket — the UI uses this to
    render the white-cell gap and surface the backfill command on click.
    """
    from src.trade.stock_simulator_facade import sim_client

    try:
        payload = sim_client().get_coverage_report(
            week_start=week,
            symbol=symbol,
            include_optional=bool(include_optional),
        )["data"]
        return HubStockHistoryCoverageResponse(status="ok", **payload)
    except Exception as exc:
        logger.exception("stock-history coverage failed for week=%s", week)
        return HubStockHistoryCoverageResponse(
            status="error", week_start="", week_end="",
            symbol=symbol, is_complete=False, error=redacted_error_detail(exc),
        )


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


@trade_router.post("/hub/stock-history/backfill-runs/{run_id}/cancel")
def hub_stock_history_backfill_run_cancel(
    run_id: str, _auth: None = Depends(require_local_or_auth),
) -> dict[str, Any]:
    return _backfill_run_call("cancel_backfill_run", run_id)


def _safe_float(v: Any) -> float | None:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def register_trade_and_watch_routes(app) -> None:
    """Mount the trade, trading-connector, and watch routers onto ``app``."""
    from src.api.trading_connector_routes import router as trading_connector_router
    from src.api.watch_routes import watch_router

    app.include_router(trade_router)
    app.include_router(trading_connector_router)
    app.include_router(watch_router)
