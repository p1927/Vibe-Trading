"""Safety net: inject trade widget when agent answers with strategy prose only."""

from __future__ import annotations

import logging
import os
import re
from typing import Any

logger = logging.getLogger(__name__)

_WIDGET_TOOLS = frozenset(
    {
        "get_options_trade_widget",
        "mcp_openalgo_get_options_trade_widget",
        "get_stock_trade_widget",
        "mcp_openalgo_get_stock_trade_widget",
        "get_index_trade_widget",
        "mcp_openalgo_get_index_trade_widget",
    }
)

_STRATEGY_KEYWORDS = re.compile(
    r"\b(iron\s+condor|straddle|strangle|bull\s+call|bear\s+put|"
    r"covered\s+call|calendar|butterfly|debit\s+spread|credit\s+spread|"
    r"\bCE\b|\bPE\b|strike|expiry|max\s+loss|breakeven)\b",
    re.IGNORECASE,
)

_TICKER_RE = re.compile(
    r"\b(NIFTY(?:50)?|BANKNIFTY|FINNIFTY|MIDCPNIFTY|SENSEX|"
    r"RELIANCE|TCS|INFY|HDFCBANK|ICICIBANK|SBIN|ITC|"
    r"[A-Z]{2,12})\b"
)


def _guard_enabled() -> bool:
    raw = os.getenv("OPTIONS_WIDGET_GUARD_ENABLED", "true").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def needs_widget_guard(
    user_message: str,
    assistant_text: str,
    tools_called: set[str] | list[str],
) -> bool:
    """Return True when agent likely presented options strategy without widget tool."""
    if not _guard_enabled():
        return False
    if not assistant_text or not _STRATEGY_KEYWORDS.search(assistant_text):
        return False
    called = {t.strip() for t in tools_called if t}
    if called & _WIDGET_TOOLS:
        return False
    combined = f"{user_message}\n{assistant_text}".upper()
    if not any(k in combined for k in ("OPTION", "NIFTY", "BANKNIFTY", "CE", "PE", "STRIKE", "STRATEGY")):
        return False
    return True


def _extract_ticker(user_message: str, assistant_text: str) -> str | None:
    try:
        from src.trade.symbol_detect import extract_primary_ticker

        for text in (user_message, assistant_text):
            ticker = extract_primary_ticker(text)
            if ticker:
                return ticker
    except Exception:
        pass
    for text in (user_message, assistant_text):
        match = _TICKER_RE.search(text.upper())
        if match:
            return match.group(1)
    return None


def maybe_inject_widget(
    session_id: str,
    event_bus: Any,
    *,
    user_message: str,
    assistant_text: str,
    tools_called: set[str] | list[str],
) -> bool:
    """Build and emit trade_plan.widget if guard triggers. Returns True if emitted."""
    if not needs_widget_guard(user_message, assistant_text, tools_called):
        return False

    ticker = _extract_ticker(user_message, assistant_text)
    if not ticker:
        return False

    try:
        from trade_integrations.dataflows.options_research.market import is_options_research_eligible
        from trade_integrations.dataflows.options_research.widget_payload import build_options_trade_widget

        if not is_options_research_eligible(ticker):
            return False

        widget = build_options_trade_widget(ticker, refresh=False)
        if event_bus is not None and session_id:
            event_bus.emit(session_id, "trade_plan.widget", widget)
        logger.info("Widget guard injected trade_plan.widget for %s session=%s", ticker, session_id)
        return True
    except Exception:
        logger.exception("Widget guard failed for ticker=%s", ticker)
        return False
