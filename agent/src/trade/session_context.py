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
    """Classify widget intent; autonomous US equity avoids bare 'execution' → execute_refresh."""
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
