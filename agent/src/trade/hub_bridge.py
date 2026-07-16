"""Bridge Vibe agent sessions to trade-stack hub research and TradingAgents debate."""

from __future__ import annotations

import logging
import os
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from src.trade.symbol_detect import (
    detect_finalize_intent,
    extract_primary_ticker,
    infer_asset_type,
)

if TYPE_CHECKING:
    from src.session.events import EventBus

logger = logging.getLogger(__name__)

_debate_running: set[str] = set()


def trade_repo_root() -> Path | None:
    """Locate the trade monorepo root when co-located with vibetrading."""
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "integrations" / "trade_integrations").is_dir():
            return parent
    env = os.getenv("TRADE_STACK_ROOT", "").strip()
    if env:
        path = Path(env).expanduser().resolve()
        if (path / "integrations" / "trade_integrations").is_dir():
            return path
    return None


def ensure_trade_stack_path() -> Path:
    root = trade_repo_root()
    if root is None:
        raise RuntimeError(
            "Trade stack not found — set TRADE_STACK_ROOT or run Vibe from the trade repo"
        )
    integrations = root / "integrations"
    tradingagents = root / "tradingagents"
    for path in (integrations, tradingagents):
        if path.is_dir() and str(path) not in sys.path:
            sys.path.insert(0, str(path))
    if os.getenv("TRADE_INTEGRATIONS_SKIP_APPLY") != "1":
        import trade_integrations  # noqa: F401
    return root


def _emit(event_bus: EventBus | None, session_id: str, event_type: str, data: dict[str, Any]) -> None:
    if event_bus is None or not session_id:
        return
    try:
        event_bus.emit(session_id, event_type, data)
    except Exception:
        logger.exception("Failed to emit %s for session %s", event_type, session_id)


def prefetch_hub_plan(ticker: str, asset_type: str) -> dict[str, Any] | None:
    """Load or generate the structured trade plan for the side-panel artifact."""
    ensure_trade_stack_path()
    from trade_integrations.context.hub import (
        is_options_cache_fresh,
        is_stock_cache_fresh,
        load_options_research_json,
        load_stock_research_json,
    )
    from trade_integrations.dataflows.options_research.market import is_options_research_eligible

    key = ticker.strip().upper()
    if asset_type == "options" and is_options_research_eligible(key):
        from trade_integrations.tools.options_research_tools import fetch_options_research_report

        use_cache = is_options_cache_fresh(key)
        fetch_options_research_report(key, use_cache=use_cache)
        doc = load_options_research_json(key)
        if doc is None:
            return None
        payload = _options_doc_to_panel(doc)
        payload["asset_type"] = "options"
        return payload

    from trade_integrations.context.hub import is_stock_research_eligible

    if is_stock_research_eligible(key):
        from trade_integrations.tools.stock_research_tools import fetch_stock_research_report

        use_cache = is_stock_cache_fresh(key)
        fetch_stock_research_report(key, use_cache=use_cache)
        doc = load_stock_research_json(key)
        if doc is None:
            return None
        payload = _stock_doc_to_panel(doc)
        payload["asset_type"] = "stock"
        return payload

    if is_options_research_eligible(key):
        return prefetch_hub_plan(key, "options")
    return None


def _options_doc_to_panel(doc) -> dict[str, Any]:
    rec = doc.recommended or {}
    errors = []
    for stage in doc.stages or []:
        if getattr(stage, "status", None) == "error":
            errors.extend(getattr(stage, "errors", None) or [])
    warnings = []
    if not rec.get("name"):
        if errors:
            warnings.append(
                "Live option chain was unavailable — strategies could not be ranked. "
                "Ensure OpenAlgo is running, then ask the agent to refresh."
            )
        elif not doc.ranked_strategies:
            warnings.append(
                "No strategies ranked yet. Ask the agent to run a fresh options research pass."
            )
    status = "ready" if rec.get("name") and doc.ranked_strategies else (
        "partial" if doc.ranked_strategies or rec.get("name") else "incomplete"
    )
    return {
        "ticker": doc.underlying,
        "underlying": doc.underlying,
        "asset_type": "options",
        "as_of": doc.as_of.isoformat() if hasattr(doc.as_of, "isoformat") else str(doc.as_of),
        "expiry": doc.expiry or None,
        "spot": doc.spot,
        "prediction": doc.prediction or {},
        "events": doc.events or [],
        "scenarios": doc.scenarios or [],
        "ranked_strategies": doc.ranked_strategies or [],
        "recommended": rec,
        "recommended_name": rec.get("name"),
        "recommended_rationale": rec.get("rationale"),
        "recommended_tier": rec.get("tier"),
        "recommended_score": rec.get("score"),
        "recommended_legs": rec.get("legs") or [],
        "max_profit": rec.get("max_profit"),
        "max_loss": rec.get("max_loss"),
        "plan_status": status,
        "data_warnings": warnings,
        "stage_errors": errors[:3],
    }


def _stock_doc_to_panel(doc) -> dict[str, Any]:
    rec = doc.recommended or {}
    action = rec.get("action") or rec.get("side") or prediction_action(doc.prediction or {})
    rationale = rec.get("rationale") or _stock_action_rationale(action, doc.prediction or {})
    status = "ready" if rec.get("name") or action else "incomplete"
    return {
        "ticker": doc.ticker,
        "underlying": doc.ticker,
        "asset_type": "stock",
        "as_of": doc.as_of.isoformat() if hasattr(doc.as_of, "isoformat") else str(doc.as_of),
        "spot": doc.spot,
        "prediction": doc.prediction or {},
        "events": doc.events or [],
        "scenarios": doc.scenarios or [],
        "ranked_strategies": doc.ranked_strategies or [],
        "recommended": rec,
        "recommended_name": rec.get("name") or action,
        "recommended_rationale": rationale,
        "recommended_tier": rec.get("tier"),
        "recommended_score": rec.get("score"),
        "plan_status": status,
        "data_warnings": [] if status == "ready" else ["Stock plan incomplete — refresh research for a clear entry view."],
        "stage_errors": [],
    }


def prediction_action(prediction: dict) -> str | None:
    view = str(prediction.get("view") or "").lower()
    if view in {"bullish", "buy"}:
        return "buy"
    if view in {"bearish", "sell"}:
        return "sell"
    if view:
        return "hold"
    return None


def _stock_action_rationale(action: str | None, prediction: dict) -> str:
    view = format_view_plain(prediction.get("view"))
    if action == "buy":
        return f"Stock view: accumulate / go long ({view})." if view else "Stock view: accumulate / go long."
    if action == "sell":
        return f"Stock view: reduce or exit ({view})." if view else "Stock view: reduce or exit."
    if action == "hold":
        return f"Stock view: hold — no strong directional edge ({view})." if view else "Stock view: hold."
    return "No stock action ranked yet."


def format_view_plain(view: object) -> str:
    if not view:
        return "neutral"
    text = str(view).replace("_", " ")
    mapping = {
        "event volatility": "event-driven volatility",
    }
    return mapping.get(text.lower(), text)


def load_hub_plan_artifact(ticker: str, asset_type: str = "options") -> dict[str, Any] | None:
    """Read cached hub plan without regenerating."""
    ensure_trade_stack_path()
    from trade_integrations.context.hub import load_options_research_json, load_stock_research_json
    from trade_integrations.dataflows.options_research.market import is_options_research_eligible

    key = ticker.strip().upper()
    if asset_type == "stock":
        doc = load_stock_research_json(key)
        return _stock_doc_to_panel(doc) if doc else None
    doc = load_options_research_json(key)
    if doc:
        payload = _options_doc_to_panel(doc)
        payload["asset_type"] = "options"
        return payload
    if is_options_research_eligible(key):
        return None
    doc = load_stock_research_json(key)
    if doc:
        payload = _stock_doc_to_panel(doc)
        payload["asset_type"] = "stock"
        return payload
    return None


def load_debate_artifact(ticker: str) -> dict[str, Any] | None:
    ensure_trade_stack_path()
    from trade_integrations.context.hub import load_agent_debate_json

    return load_agent_debate_json(ticker.strip().upper())


def is_debate_running(ticker: str) -> bool:
    return ticker.strip().upper() in _debate_running


def run_agent_debate_sync(ticker: str, *, asset_type: str = "stock") -> dict[str, Any]:
    key = ticker.strip().upper()
    if key in _debate_running:
        cached = load_debate_artifact(key)
        if cached:
            return cached
        raise RuntimeError(f"Agent debate already running for {key}")
    _debate_running.add(key)
    try:
        ensure_trade_stack_path()
        from trade_integrations.bridge.agent_debate import run_agent_debate

        return run_agent_debate(key, asset_type=asset_type)
    finally:
        _debate_running.discard(key)


def handle_user_message_research(
    session_id: str,
    content: str,
    event_bus: EventBus | None = None,
) -> None:
    """Prefetch hub plan on symbol mention; run TradingAgents debate on finalize intent."""
    try:
        ensure_trade_stack_path()
    except RuntimeError:
        logger.debug("Trade stack path unavailable for research prefetch")
    ticker = extract_primary_ticker(content)
    if not ticker:
        return
    asset_type = infer_asset_type(content, ticker)
    try:
        artifact = prefetch_hub_plan(ticker, asset_type)
        if artifact:
            _emit(
                event_bus,
                session_id,
                "research.artifact",
                {"ticker": ticker, "asset_type": asset_type, "artifact": artifact},
            )
    except Exception:
        logger.exception("Hub prefetch failed for %s", ticker)

    if not detect_finalize_intent(content):
        return

    cached = load_debate_artifact(ticker)
    ensure_trade_stack_path()
    from trade_integrations.context.hub import is_agent_debate_cache_fresh

    if cached and is_agent_debate_cache_fresh(ticker):
        _emit(
            event_bus,
            session_id,
            "research.debate",
            {"ticker": ticker, "status": "ready", "debate": cached},
        )
        return

    if is_debate_running(ticker):
        _emit(
            event_bus,
            session_id,
            "research.debate",
            {"ticker": ticker, "status": "running"},
        )
        return

    _emit(
        event_bus,
        session_id,
        "research.debate",
        {"ticker": ticker, "status": "started", "started_at": datetime.now(timezone.utc).isoformat()},
    )

    def _debate_worker() -> None:
        try:
            debate = run_agent_debate_sync(ticker, asset_type=asset_type)
            _emit(
                event_bus,
                session_id,
                "research.debate",
                {"ticker": ticker, "status": "ready", "debate": debate},
            )
        except Exception as exc:
            logger.exception("Agent debate failed for %s", ticker)
            _emit(
                event_bus,
                session_id,
                "research.debate",
                {"ticker": ticker, "status": "error", "message": str(exc)},
            )

    threading.Thread(target=_debate_worker, daemon=True, name=f"debate-{ticker}").start()
