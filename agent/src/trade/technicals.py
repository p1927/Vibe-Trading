"""vibetrading's one door to Trade's technical indicators (Trade D326).

RSI has one formula, Trade's ``technical_features.compute_rsi`` (the one behind the stored
``IN/nifty_rsi_14``); the indicator tool, the Shadow Account extractor/scanner and the generated
Shadow signal engine all call it through here instead of carrying their own copy. NIFTY's
technicals are not computed at all: they are the stored values the prediction model serves
(``history_loader.latest_nifty_technicals``, Trade D318). Sidecar module per
docs/FORK_CONVENTIONS.md.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from src.trade.hub_bridge import ensure_trade_stack_path


# Calendar days of daily history that cover Trade's TECHNICAL_WARMUP_SESSIONS (250 sessions, about
# 362 calendar days at 252 sessions/year, plus slack for holidays). Wilder smoothing is seeded from the
# first delta, so a short window gives a seed-dominated RSI (6 points off at 27 bars, 0.007 at 120);
# every daily-RSI caller fetches at least this much history. A test pins it to the Trade constant.
RSI_WARMUP_CALENDAR_DAYS = 380


def compute_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """Trade's Wilder RSI series; NaN before ``period`` observations and on a flat stretch."""
    ensure_trade_stack_path()
    from trade_integrations.dataflows.index_research.technical_features import compute_rsi as _rsi

    return _rsi(close, period)


def stored_nifty_technicals(symbol: str) -> dict[str, Any] | None:
    """``latest_nifty_technicals()`` (``{"date", "close", "factors"}``) when ``symbol`` is a spelling
    of NIFTY (the entity registry owns the spellings), else ``None``."""
    ensure_trade_stack_path()
    from trade_integrations.dataflows.entity_registry import resolve_index

    if resolve_index("IN", symbol) != "NIFTY":
        return None
    from trade_integrations.dataflows.index_research.sources.history_loader import latest_nifty_technicals

    return latest_nifty_technicals()
