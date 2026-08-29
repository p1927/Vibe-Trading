"""Bridge Vibe agent sessions to trade-stack hub research and TradingAgents debate."""

from __future__ import annotations

import logging
import re
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from src.config.accessor import get_env_config
from src.trade.symbol_detect import (
    detect_finalize_intent,
)

if TYPE_CHECKING:
    from src.session.events import EventBus

logger = logging.getLogger(__name__)

# A human operator's own follow-up chat message asking the agent to retry a
# previously-failed action -- distinct from `is_autonomous_scheduler_turn`'s
# literal `# Autonomous agent turn` marker text, which only the scheduler
# itself injects. Without this, prefetch_autonomous_context() below returns ""
# for every plain user chat turn (retry included), so the LLM has no fresh
# tool-state snapshot to check against and just repeats whatever it last said
# in that session's own chat history -- confirmed live: telling a real agent
# "the backend is fixed, retry" produced the identical stale tool-failure
# claim with zero fresh tool calls made (tool_trail: []), 6+ turns straight.
# See .claude/backlog/items/2026-08-27-autonomous-agent-stale-tool-failure-no-reverify.md.
_OPERATOR_RETRY_RE = re.compile(r"\bretry\b|\btry again\b", re.IGNORECASE)


def _is_operator_retry_message(content: str) -> bool:
    return bool(_OPERATOR_RETRY_RE.search(content or ""))


# Fixing the fresh-context gap above (_is_operator_retry_message) turned out
# not to be sufficient on its own: a retry turn can still have a stale
# "backend is broken" conclusion available from *conversation history* (its
# own earlier turn in the same session) even when this turn's own injected
# [agent_context] block is completely clean. Confirmed live: an agent with
# such history in tool_trail: [] (zero tool calls) restated a fabricated
# NameError verbatim despite a clean prefetch block, while a brand-new
# session with no history and the identical retry wording called real tools
# and reported a genuine result. The model leans on *any* available prior
# conclusion -- memory, history, or otherwise -- instead of re-verifying, on
# turns explicitly asking it to. This instruction is the general
# countermeasure: it doesn't try to detect what in history is stale (a
# content classifier tuned for terse memory titles false-positives heavily
# on ordinary tool-error chatter in a long session), it just makes retry
# turns unconditionally demand a fresh check.
# See .claude/backlog/items/2026-08-29-autonomous-agent-retry-fix-not-live-effective.md.
_OPERATOR_RETRY_REVERIFY_INSTRUCTION = (
    "\n\n[operator-retry instruction] This turn was flagged as a retry request. "
    "If a prior turn in this conversation, or a recalled memory, already concluded "
    "that a tool or backend is broken, that conclusion may be stale -- it is never "
    "automatically re-verified. Call the relevant tool(s) again this turn before "
    "restating any such conclusion. Only report a failure if a tool call you "
    "actually make this turn fails; do not repeat a past failure claim without a "
    "fresh call backing it."
)

session_widget_emitted: dict[str, dict[str, float]] = {}
WIDGET_EMIT_DEDUP_SECONDS = 10 * 60


def trade_repo_root() -> Path | None:
    """Locate the trade monorepo root when co-located with vibetrading."""
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "integrations" / "trade_integrations").is_dir():
            return parent
    env = get_env_config().trade.trade_stack_root.strip()
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
    if get_env_config().trade.trade_integrations_skip_apply != "1":
        import trade_integrations  # noqa: F401
    return root


def _options_auto_widget_enabled() -> bool:
    val = get_env_config().trade.options_auto_widget_on_prefetch.strip().lower()
    return val not in ("0", "false", "no", "off")


def _index_auto_widget_enabled() -> bool:
    val = get_env_config().trade.index_auto_widget_on_prefetch.strip().lower()
    return val not in ("0", "false", "no", "off")


def _widget_dedup_key(ticker: str, widget_kind: str) -> str:
    return f"{ticker.strip().upper()}:{widget_kind.strip().lower()}"


def _should_emit_widget(session_id: str, ticker: str, now: float, *, widget_kind: str = "options") -> bool:
    if not session_id:
        return False
    key = _widget_dedup_key(ticker, widget_kind)
    session_emits = session_widget_emitted.get(session_id, {})
    last = session_emits.get(key)
    if last is not None and (now - last) < WIDGET_EMIT_DEDUP_SECONDS:
        return False
    return True


def _record_widget_emitted(
    session_id: str,
    ticker: str,
    now: float,
    *,
    widget_kind: str = "options",
) -> None:
    if not session_id:
        return
    key = _widget_dedup_key(ticker, widget_kind)
    session_widget_emitted.setdefault(session_id, {})[key] = now


def _annotate_widget_intent(widget: dict[str, Any], widget_intent: str) -> dict[str, Any]:
    from trade_integrations.trade_widgets.presentability import apply_widget_metadata

    return apply_widget_metadata(widget, widget_intent)


def _maybe_emit_options_widget(
    event_bus: EventBus | None,
    session_id: str,
    ticker: str,
    *,
    widget_intent: str = "options_strategy",
    session_config: dict[str, Any] | None = None,
) -> None:
    now = time.time()
    if not _should_emit_widget(session_id, ticker, now, widget_kind="options"):
        return
    try:
        ensure_trade_stack_path()
        from trade_integrations.autonomous_agents.intent_capabilities import (
            attach_capabilities_metadata,
            prefetch_widget_intent_allowed,
            resolve_capabilities,
        )
        from trade_integrations.dataflows.options_research.widget_payload import (
            build_options_trade_widget,
        )
        from trade_integrations.trade_widgets.presentability import is_widget_presentable
        from trade_integrations.trade_widgets.store import persist_trade_widget

        caps = resolve_capabilities(session_config=session_config)
        if not prefetch_widget_intent_allowed(widget_intent, caps):
            logger.debug(
                "Skip options widget emit: capabilities block intent=%s session=%s",
                widget_intent,
                session_id,
            )
            return
        refresh = widget_intent == "execute_refresh"
        widget = build_options_trade_widget(ticker, refresh=refresh, widget_intent=widget_intent)
        _annotate_widget_intent(widget, widget_intent)
        if not is_widget_presentable(widget, widget_intent, session_config=session_config):
            logger.debug(
                "Skip options widget emit: presentability failed intent=%s ticker=%s session=%s",
                widget_intent,
                ticker,
                session_id,
            )
            return
        attach_capabilities_metadata(widget, session_config=session_config)
        persist_trade_widget(widget)
        _emit(event_bus, session_id, "trade_plan.widget", widget)
        _record_widget_emitted(session_id, ticker, now, widget_kind="options")
    except Exception:
        logger.exception("Failed to auto-emit options widget for %s", ticker)


def _maybe_emit_index_widget(
    event_bus: EventBus | None,
    session_id: str,
    ticker: str,
    *,
    widget_intent: str = "index_outlook",
    session_config: dict[str, Any] | None = None,
) -> None:
    now = time.time()
    if not _should_emit_widget(session_id, ticker, now, widget_kind="index"):
        return
    try:
        ensure_trade_stack_path()
        from trade_integrations.autonomous_agents.intent_capabilities import (
            attach_capabilities_metadata,
            prefetch_widget_intent_allowed,
            resolve_capabilities,
        )
        from trade_integrations.dataflows.index_research.widget_payload import (
            build_index_trade_widget,
        )
        from trade_integrations.trade_widgets.presentability import is_widget_presentable
        from trade_integrations.trade_widgets.store import persist_trade_widget

        caps = resolve_capabilities(session_config=session_config)
        if not prefetch_widget_intent_allowed(widget_intent, caps):
            logger.debug(
                "Skip index widget emit: capabilities block intent=%s session=%s",
                widget_intent,
                session_id,
            )
            return
        refresh = widget_intent == "execute_refresh"
        widget = build_index_trade_widget(ticker, refresh=refresh, widget_intent=widget_intent)
        _annotate_widget_intent(widget, widget_intent)
        if not is_widget_presentable(widget, widget_intent, session_config=session_config):
            logger.debug(
                "Skip index widget emit: presentability failed intent=%s ticker=%s session=%s",
                widget_intent,
                ticker,
                session_id,
            )
            return
        attach_capabilities_metadata(widget, session_config=session_config)
        persist_trade_widget(widget)
        _emit(event_bus, session_id, "trade_plan.widget", widget)
        _record_widget_emitted(session_id, ticker, now, widget_kind="index")
    except Exception:
        logger.exception("Failed to auto-emit index widget for %s", ticker)


def _staleness_report_to_dict(report: Any) -> dict[str, Any]:
    as_of = getattr(report, "as_of", None)
    return {
        "status": report.status,
        "reasons": list(report.reasons or []),
        "suggested_action": report.suggested_action,
        "plan_spot": report.plan_spot,
        "live_spot": report.live_spot,
        "spot_drift_pct": report.spot_drift_pct,
        "age_minutes": report.age_minutes,
        "as_of": as_of.isoformat() if hasattr(as_of, "isoformat") else (str(as_of) if as_of else None),
    }


def _maybe_evaluate_plan_staleness(
    artifact: dict[str, Any],
    ticker: str,
    asset_type: str,
    event_bus: EventBus | None,
    session_id: str,
) -> None:
    if asset_type != "options" or not artifact:
        return
    try:
        from trade_integrations.monitor.service import MonitorService

        if not MonitorService.is_enabled():
            return
        report = MonitorService().evaluate_ticker(ticker)
        if not report:
            return
        artifact["staleness"] = _staleness_report_to_dict(report)
        if report.status != "fresh":
            key = ticker.strip().upper()
            _emit(
                event_bus,
                session_id,
                "plan.stale",
                {
                    "ticker": key,
                    "status": report.status,
                    "reasons": report.reasons,
                    "suggested_action": report.suggested_action,
                },
            )
    except Exception:
        logger.exception("Plan staleness evaluation failed for %s", ticker)


def _emit(event_bus: EventBus | None, session_id: str, event_type: str, data: dict[str, Any]) -> None:
    if event_bus is None or not session_id:
        return
    try:
        event_bus.emit(session_id, event_type, data)
        _emit_provenance(event_bus, session_id, event_type, data)
        if event_type == "trade_plan.widget" and isinstance(data, dict):
            from src.trade.plan_widget_hook import notify_trade_plan_widget

            notify_trade_plan_widget(session_id, data)
    except Exception:
        logger.exception("Failed to emit %s for session %s", event_type, session_id)


def _emit_provenance(
    event_bus: EventBus,
    session_id: str,
    event_type: str,
    data: dict[str, Any],
) -> None:
    if event_type not in {"research.artifact", "research.debate"}:
        return
    try:
        from src.provenance.hook import record_from_event

        source = record_from_event(session_id, event_type, data)
        if source:
            event_bus.emit(session_id, "provenance.source", {"source": source.to_dict()})
    except Exception:
        logger.exception("Failed to record provenance for %s", event_type)


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
    if asset_type == "index":
        return prefetch_index_hub_plan(key)
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


def prefetch_index_hub_plan(ticker: str) -> dict[str, Any] | None:
    """Load or generate structured index research for the side-panel artifact."""
    ensure_trade_stack_path()
    from trade_integrations.context.hub import (
        is_index_research_cache_fresh,
        load_index_research_json,
    )
    from trade_integrations.tools.index_research_tools import (
        fetch_index_research_report,
        is_index_research_eligible,
    )

    key = ticker.strip().upper()
    if not is_index_research_eligible(key):
        return None

    use_cache = is_index_research_cache_fresh(key)
    fetch_index_research_report(key, use_cache=use_cache)
    doc = load_index_research_json(key)
    if doc is None:
        return None
    payload = _index_doc_to_panel(doc)
    payload["asset_type"] = "index"
    return payload


def _index_doc_to_panel(doc) -> dict[str, Any]:
    pred = doc.prediction or {}
    factor_exp = doc.factor_explanation or {}
    contributors = factor_exp.get("contributors") or []
    errors = []
    for stage in doc.stages or []:
        if getattr(stage, "status", None) == "error":
            errors.extend(getattr(stage, "errors", None) or [])

    warnings: list[str] = list(getattr(doc, "data_warnings", None) or [])
    if getattr(doc, "spot_error", None):
        warnings.append(
            f"Live spot unavailable — re-login INDmoney in OpenAlgo ({doc.spot_error})"
        )
    if not pred.get("view"):
        warnings.append("Index prediction incomplete — refresh index research.")
    if not contributors:
        warnings.append("Factor attribution not available yet — run index research refresh.")

    pipeline_log = list(doc.pipeline_log or [])
    if not pipeline_log and doc.stages:
        for stage in doc.stages:
            status = getattr(stage, "status", "ok")
            vendor = getattr(stage, "vendor", "") or getattr(stage, "stage", "stage")
            msg = f"{getattr(stage, 'stage', 'stage')}: {status}"
            if status == "error" and getattr(stage, "errors", None):
                msg = f"{msg} — {'; '.join(stage.errors[:2])}"
            pipeline_log.append(
                {
                    "stage": str(getattr(stage, "stage", "stage")),
                    "message": msg,
                    "level": "error" if status == "error" else "warn" if status == "partial" else "info",
                    "at": getattr(stage, "fetched_at", doc.as_of).isoformat()
                    if hasattr(getattr(stage, "fetched_at", None), "isoformat")
                    else str(doc.as_of),
                    "detail": {"vendor": vendor, "status": status},
                }
            )
    if not pipeline_log and pred.get("view"):
        pipeline_log.append(
            {
                "stage": "cached",
                "message": f"Loaded hub artifact from {doc.as_of}",
                "level": "info",
                "at": doc.as_of.isoformat() if hasattr(doc.as_of, "isoformat") else str(doc.as_of),
                "detail": {},
            }
        )

    status = "ready" if pred.get("view") and contributors else (
        "partial" if pred.get("view") or contributors else "incomplete"
    )

    return {
        "ticker": doc.ticker,
        "underlying": doc.ticker,
        "asset_type": "index",
        "as_of": doc.as_of.isoformat() if hasattr(doc.as_of, "isoformat") else str(doc.as_of),
        "constituents_as_of": getattr(doc, "constituents_as_of", None),
        "horizon": doc.horizon or {},
        "spot": doc.spot,
        "spot_source": getattr(doc, "spot_source", None),
        "spot_error": getattr(doc, "spot_error", None),
        "prediction": pred,
        "regime": doc.regime or {},
        "scenarios": doc.scenarios or [],
        "factor_explanation": factor_exp,
        "factor_sensitivity": doc.factor_sensitivity or [],
        "event_impact_curves": doc.event_impact_curves or [],
        "upcoming_events": doc.upcoming_events or [],
        "global_factors": doc.global_factors or [],
        "sector_breadth": doc.sector_breadth or {},
        "constituent_signals": doc.constituent_signals or [],
        "top_factors": contributors[:8],
        "accuracy": doc.accuracy or {},
        "plan_status": status,
        "data_warnings": warnings,
        "stage_errors": errors[:3],
        "pipeline_log": pipeline_log,
    }


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
    if "bear" in view:
        return "sell"
    if "bull" in view:
        return "buy"
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
    if asset_type == "index":
        from trade_integrations.context.hub import load_index_research_json

        doc = load_index_research_json(key)
        return _index_doc_to_panel(doc) if doc else None
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
    ensure_trade_stack_path()
    from trade_integrations.bridge.agent_debate import is_debate_running as _is_running

    return _is_running(ticker)


def run_agent_debate_sync(ticker: str, *, asset_type: str = "stock") -> dict[str, Any]:
    ensure_trade_stack_path()
    from trade_integrations.bridge.agent_debate import run_agent_debate_locked
    from trade_integrations.bridge.hub_context import infer_debate_asset_type

    key = ticker.strip().upper()
    resolved_asset = infer_debate_asset_type(key, asset_type)
    return run_agent_debate_locked(key, asset_type=resolved_asset)


def prefetch_research_for_message(
    session_id: str,
    content: str,
    event_bus: EventBus | None = None,
    session_config: dict[str, Any] | None = None,
) -> str:
    """Prefetch hub plan, emit SSE artifact, return agent context block (may be empty)."""
    try:
        ensure_trade_stack_path()
    except RuntimeError:
        logger.debug("Trade stack path unavailable for research prefetch")
        return ""

    from src.trade.session_context import (
        classify_prefetch_widget_intent,
        infer_prefetch_asset_type,
        resolve_prefetch_ticker,
    )

    ticker = resolve_prefetch_ticker(session_config, content)
    if not ticker:
        return ""

    widget_intent = classify_prefetch_widget_intent(session_config, content)
    asset_type = infer_prefetch_asset_type(session_config, ticker, content)
    artifact: dict[str, Any] | None = None
    index_artifact: dict[str, Any] | None = None

    from src.session.news_scenario_profile import is_news_scenario_session

    news_scenario_mode = is_news_scenario_session(session_config)
    try:
        if news_scenario_mode:
            from trade_integrations.dataflows.index_research.pipeline_snapshot import (
                load_pipeline_doc_from_hub,
                normalize_as_of,
            )

            pipeline_as_of = str((session_config or {}).get("pipeline_as_of") or "")
            doc = load_pipeline_doc_from_hub(ticker)
            if doc and normalize_as_of(doc.as_of) == normalize_as_of(pipeline_as_of):
                index_artifact = _index_doc_to_panel(doc)
                index_artifact["asset_type"] = "index"
        else:
            artifact = prefetch_hub_plan(ticker, asset_type)
        if artifact:
            _maybe_evaluate_plan_staleness(artifact, ticker, asset_type, event_bus, session_id)
            _emit(
                event_bus,
                session_id,
                "research.artifact",
                {"ticker": ticker, "asset_type": asset_type, "artifact": artifact},
            )
            from trade_integrations.bridge.hub_context import has_strategy_options_to_present

            if (
                asset_type == "options"
                and artifact.get("plan_status") in ("ready", "partial")
                and has_strategy_options_to_present(artifact)
                and _options_auto_widget_enabled()
                and widget_intent in ("options_strategy", "execute_refresh")
            ):
                _maybe_emit_options_widget(
                    event_bus,
                    session_id,
                    ticker,
                    widget_intent=widget_intent,
                    session_config=session_config,
                )

        from trade_integrations.tools.index_research_tools import is_index_research_eligible

        if is_index_research_eligible(ticker) and not news_scenario_mode:
            index_artifact = prefetch_index_hub_plan(ticker)
            if index_artifact:
                _emit(
                    event_bus,
                    session_id,
                    "research.artifact",
                    {"ticker": ticker, "asset_type": "index", "artifact": index_artifact},
                )
                if (
                    index_artifact.get("plan_status") in ("ready", "partial")
                    and _index_auto_widget_enabled()
                    and widget_intent == "index_outlook"
                ):
                    _maybe_emit_index_widget(
                        event_bus,
                        session_id,
                        ticker,
                        widget_intent=widget_intent,
                        session_config=session_config,
                    )
        elif news_scenario_mode and index_artifact:
            _emit(
                event_bus,
                session_id,
                "research.artifact",
                {"ticker": ticker, "asset_type": "index", "artifact": index_artifact},
            )
    except Exception:
        logger.exception("Hub prefetch failed for %s", ticker)

    from trade_integrations.bridge.hub_context import format_research_context_for_agent

    debate_artifact = load_debate_artifact(ticker)
    context = format_research_context_for_agent(
        artifact,
        index_artifact=index_artifact,
        debate_artifact=debate_artifact,
        widget_intent=widget_intent,
        session_config=session_config,
    )
    if detect_finalize_intent(content):
        _maybe_start_debate(session_id, ticker, asset_type, event_bus)
    return context


def prefetch_autonomous_context(
    session_id: str,
    content: str,
    session_config: dict[str, Any] | None = None,
) -> str:
    """Inject expanded agent learning/progress for autonomous scheduler turns, and for
    a human operator's own retry follow-up in an active autonomous-agent session (see
    _is_operator_retry_message above) -- otherwise a "the backend is fixed, retry"
    message gets no fresh tool-state snapshot and the LLM just repeats its last stale
    conclusion from chat history instead of re-checking.
    """
    try:
        ensure_trade_stack_path()
    except RuntimeError:
        return ""
    from src.trade.autonomous_decision_guard import is_autonomous_scheduler_turn
    from src.trade.session_context import is_autonomous_agent_session

    cfg = dict(session_config or {})
    if not is_autonomous_agent_session(cfg):
        return ""
    if not is_autonomous_scheduler_turn(content) and not _is_operator_retry_message(content):
        return ""
    agent_id = str(cfg.get("autonomous_agent_id") or "").strip()
    if not agent_id:
        return ""
    try:
        from trade_integrations.autonomous_agents.context_prefetch import (
            format_autonomous_context_for_prefetch,
            infer_turn_kind_from_prompt,
        )
        from trade_integrations.autonomous_agents.store import get_agent

        agent = get_agent(agent_id)
        if not agent:
            return ""
        turn_kind = infer_turn_kind_from_prompt(content)
        if turn_kind == "research" and not is_autonomous_scheduler_turn(content):
            # An operator retry with no explicit turn-kind marker defaults to
            # "research", which format_strategy_progress_for_prompt skips entirely
            # (it only renders for strategy_revision/post_execution) -- meaning
            # "research" would leave out exactly the fresh, tool-backed
            # portfolio_greeks/live_pop/etc. snapshot a retry needs to actually
            # re-check prior tool failures against, not just repeat them from memory.
            turn_kind = "strategy_revision"
        context_str = format_autonomous_context_for_prefetch(agent=agent, turn_kind=turn_kind)
        if _is_operator_retry_message(content):
            context_str += _OPERATOR_RETRY_REVERIFY_INSTRUCTION
        return context_str
    except Exception:
        logger.exception("Autonomous context prefetch failed for %s", agent_id)
        return ""


def _maybe_start_debate(
    session_id: str,
    ticker: str,
    asset_type: str,
    event_bus: EventBus | None,
) -> None:
    cached = load_debate_artifact(ticker)
    try:
        ensure_trade_stack_path()
        from trade_integrations.context.hub import is_agent_debate_cache_fresh
    except RuntimeError:
        return

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
        except ValueError as exc:
            logger.warning("Agent debate skipped for %s: %s", ticker, exc)
            _emit(
                event_bus,
                session_id,
                "research.debate",
                {"ticker": ticker, "status": "error", "message": str(exc)},
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


def handle_user_message_research(
    session_id: str,
    content: str,
    event_bus: EventBus | None = None,
) -> None:
    """Prefetch hub plan on symbol mention; run TradingAgents debate on finalize intent."""
    prefetch_research_for_message(session_id, content, event_bus)
