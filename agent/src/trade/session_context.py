"""Session-scoped trade context: prefetch ticker, asset type, memory filtering."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from src.trade.symbol_detect import extract_primary_ticker, infer_asset_type

if TYPE_CHECKING:
    from src.memory.persistent import MemoryEntry

_IN_MARKET_MARKERS = frozenset(
    {
        "NIFTY",
        "NIFTY50",
        "BANKNIFTY",
        "FINNIFTY",
        "MIDCPNIFTY",
        "SENSEX",
        "INDIAVIX",
        "LONG_STRADDLE",
        "21JUL26",
    }
)

_US_BACKEND_MARKERS = frozenset({"ALPACA", "ALPACA-PAPER"})

_INJECTION_TITLE_RE = re.compile(r"prompt[- ]injection", re.I)

_E2E_MEMORY_MARKERS = (
    "handoff cycle",
    "cached context",
    "synthetic alert",
    "idempotent reads",
    "verification reads",
    "e2e phase",
    "integration test",
    "commit from cached",
)


def session_execution_market(session_config: dict[str, Any] | None) -> str:
    """Return ``US``, ``IN``, or empty when unknown."""
    cfg = session_config or {}
    market = str(cfg.get("execution_market") or "").strip().upper()
    return market if market in {"US", "IN"} else ""


def session_symbols(session_config: dict[str, Any] | None) -> list[str]:
    cfg = session_config or {}
    return [str(s).strip().upper() for s in (cfg.get("symbols") or []) if str(s).strip()]


def is_autonomous_agent_session(session_config: dict[str, Any] | None) -> bool:
    return str((session_config or {}).get("session_kind") or "") == "autonomous_agent"


def is_autonomous_us_equity_session(session_config: dict[str, Any] | None) -> bool:
    cfg = session_config or {}
    if not is_autonomous_agent_session(cfg):
        return False
    if session_execution_market(cfg) != "US":
        return False
    profile = str(cfg.get("execution_profile") or "")
    if "equity" in profile:
        return True
    if cfg.get("options_advisor_autonomous"):
        return False
    return True


def resolve_prefetch_ticker(
    session_config: dict[str, Any] | None,
    content: str,
) -> str | None:
    """Resolve hub prefetch ticker — session symbols win for autonomous agents."""
    cfg = session_config or {}
    symbols = session_symbols(cfg)

    pipeline_ticker = str(cfg.get("pipeline_ticker") or "").strip().upper()
    if pipeline_ticker:
        return pipeline_ticker

    session_kind = str(cfg.get("session_kind") or "").strip()
    if session_kind == "news_scenario_advisor":
        return pipeline_ticker or "NIFTY"

    if is_autonomous_agent_session(cfg) and symbols:
        return symbols[0]

    market = session_execution_market(cfg)
    if symbols and market == "US":
        msg_ticker = extract_primary_ticker(content)
        if msg_ticker and msg_ticker.upper() in symbols:
            return msg_ticker.upper()
        if cfg.get("autonomous"):
            return symbols[0]

    return extract_primary_ticker(content)


def infer_prefetch_asset_type(
    session_config: dict[str, Any] | None,
    ticker: str,
    content: str,
) -> str:
    """Infer hub asset type using session market before message keyword heuristics."""
    cfg = session_config or {}
    key = ticker.strip().upper()
    market = session_execution_market(cfg)

    if is_autonomous_us_equity_session(cfg):
        return "stock"

    if market == "US":
        try:
            from trade_integrations.dataflows.company_research.market import detect_market

            if detect_market(key).value == "US":
                return "stock"
        except Exception:
            return "stock"

    return infer_asset_type(content, ticker)


def classify_prefetch_widget_intent(
    session_config: dict[str, Any] | None,
    content: str,
) -> str:
    """Classify widget intent; autonomous agents use persisted intent.capabilities first."""
    cfg = session_config or {}
    if is_autonomous_agent_session(cfg):
        mc = cfg.get("mandate_config") if isinstance(cfg.get("mandate_config"), dict) else {}
        agent_mode = str(cfg.get("agent_mode") or mc.get("agent_mode") or "").lower()
        raw_intent = mc.get("intent") if isinstance(mc.get("intent"), dict) else {}
        if agent_mode == "observe" or str(raw_intent.get("engagement") or "").lower() == "observe":
            return "none"
        caps = raw_intent.get("capabilities") if isinstance(raw_intent.get("capabilities"), dict) else {}
        if not caps and raw_intent:
            from trade_integrations.autonomous_agents.intent_merge import derive_capabilities
            from trade_integrations.autonomous_agents.intent_schema import AgentIntent

            caps = derive_capabilities(AgentIntent.from_dict(raw_intent))
        if raw_intent or agent_mode:
            if not caps.get("widgets"):
                return "none"
            if caps.get("payoff"):
                return "options_strategy"
            if caps.get("index_outlook") and not caps.get("payoff"):
                return "index_outlook"
            if caps.get("charges"):
                return "stock_trade"
            return "none"
        if is_autonomous_us_equity_session(cfg):
            from src.trade.widget_intent import WidgetIntent, classify_widget_intent

            intent: WidgetIntent = classify_widget_intent(content)
            if intent == "execute_refresh":
                return "stock_trade"
            return intent
        return "none"

    from src.trade.widget_intent import WidgetIntent, classify_widget_intent

    intent: WidgetIntent = classify_widget_intent(content)
    if is_autonomous_us_equity_session(session_config) and intent == "execute_refresh":
        return "stock_trade"
    return intent


def memory_matches_session(
    entry: MemoryEntry,
    session_config: dict[str, Any] | None,
) -> bool:
    """Filter auto-recall so cross-market injection notes do not bleed into other sessions."""
    cfg = session_config or {}
    market = session_execution_market(cfg)
    blob_lower = f"{entry.title} {entry.description} {entry.body}".lower()

    if is_autonomous_agent_session(cfg) and not cfg.get("e2e_integration_test"):
        if any(marker in blob_lower for marker in _E2E_MEMORY_MARKERS):
            return False
        if _INJECTION_TITLE_RE.search(entry.title.lower()):
            return False

    if not market:
        return True

    symbols = session_symbols(cfg)
    blob = f"{entry.title} {entry.description} {entry.body}".upper()
    title_lower = entry.title.lower()

    if market == "US":
        if any(marker in blob for marker in _IN_MARKET_MARKERS):
            if _INJECTION_TITLE_RE.search(title_lower) or "prompt injection" in blob.lower():
                return False
            if symbols and not any(sym in blob for sym in symbols):
                return False
        return True

    if market == "IN":
        if any(marker in blob for marker in _US_BACKEND_MARKERS):
            if _INJECTION_TITLE_RE.search(title_lower):
                return False
            if symbols and not any(sym in blob for sym in symbols):
                return False
        return True

    return True
