"""Classify user message intent for trade-plan widget emission."""

from __future__ import annotations

import re
from typing import Literal

from src.trade.symbol_detect import detect_finalize_intent, detect_options_intent

WidgetIntent = Literal[
    "none",
    "index_outlook",
    "options_strategy",
    "stock_trade",
    "execute_refresh",
]

_BROWSE_HINT = re.compile(
    r"\b(chain|OI|open\s+interest|browse|expiries|expiry\s+list|list\s+strikes|"
    r"available\s+strikes|show\s+chain)\b",
    re.I,
)
_INDEX_OUTLOOK_HINT = re.compile(
    r"\b(where\s+(is|are)|headed|direction|outlook|factors?|macro|scenario|"
    r"index\s+view|market\s+view|doing\s+today|doing\s+now)\b",
    re.I,
)
_STOCK_TRADE_HINT = re.compile(
    r"\b(buy|sell|hold|accumulate|reduce|shares|equity|stock\s+position)\b",
    re.I,
)
_STRATEGY_HINT = re.compile(
    r"\b(strategy|iron\s+condor|straddle|strangle|spread|recommend\s+trade|"
    r"which\s+option|trade\s+plan)\b",
    re.I,
)


def detect_browse_intent(text: str) -> bool:
    return bool(_BROWSE_HINT.search(text or ""))


def detect_index_outlook_intent(text: str) -> bool:
    return bool(_INDEX_OUTLOOK_HINT.search(text or ""))


def detect_stock_trade_intent(text: str) -> bool:
    t = text or ""
    return bool(_STOCK_TRADE_HINT.search(t)) and not detect_options_intent(t)


def classify_widget_intent(text: str) -> WidgetIntent:
    t = text or ""
    if detect_finalize_intent(t):
        return "execute_refresh"
    if detect_browse_intent(t):
        return "none"
    if detect_options_intent(t) or _STRATEGY_HINT.search(t):
        return "options_strategy"
    if detect_stock_trade_intent(t):
        return "stock_trade"
    if detect_index_outlook_intent(t):
        return "index_outlook"
    return "none"
