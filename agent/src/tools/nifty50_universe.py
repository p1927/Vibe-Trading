"""Nifty 50 universe (India equity) for the Alpha Bench tool.

Fork-only extension registering the Nifty 50 as a benchable Alpha Zoo
universe, on top of ``alpha_bench_tool.py``'s neutral registry mechanism
(``UniverseSpec`` / ``UNIVERSE_REGISTRY``). Kept in a fork-owned sidecar per
docs/FORK_CONVENTIONS.md, Shape 1 ("new, self-contained behavior") — mirrors
``nse_sector_universes.py``'s pattern for the same reason. ``alpha_bench_tool.py``
carries one import plus one call to :func:`register_nifty50_universe`.
"""

from __future__ import annotations

import logging
from typing import Any

import pandas as pd

logger = logging.getLogger(__name__)

# Hand-picked Nifty 50 representatives when nselib constituent list is unavailable.
_NIFTY50_FALLBACK_CODES = [
    "RELIANCE", "TCS", "HDFCBANK", "ICICIBANK", "INFY", "ITC", "SBIN", "BHARTIARTL",
    "KOTAKBANK", "LT", "HINDUNILVR", "AXISBANK", "BAJFINANCE", "ASIANPAINT", "MARUTI",
    "SUNPHARMA", "TITAN", "ULTRACEMCO", "NESTLEIND", "WIPRO", "HCLTECH", "POWERGRID",
    "NTPC", "TATAMOTORS", "M&M", "ADANIENT", "JSWSTEEL", "TATASTEEL", "COALINDIA",
    "ONGC", "GRASIM", "TECHM", "HDFCLIFE", "SBILIFE", "BAJAJFINSV", "INDUSINDBK",
    "CIPLA", "DRREDDY", "APOLLOHOSP", "EICHERMOT", "HEROMOTOCO", "BRITANNIA",
    "DIVISLAB", "TATACONSUM", "BPCL", "HINDALCO", "ADANIPORTS", "LTIM", "BEL",
]


def _load_nifty50_stock_simulator(code: str, start: str, end: str) -> "pd.DataFrame | None":
    """Recorded OHLCV for one NIFTY 50 constituent via the same
    stock_simulator loader the OHLCV registry chain uses (``FALLBACK_CHAINS
    ["india_equity"]`` — stock_simulator is tried first there too), so this
    bench panel and every other India-equity consumer agree on one recorded
    source instead of each hitting stock_history a different way.

    Same full-range-or-omit coverage policy as that loader (see its own
    docstring): returns ``None`` — not a partial frame — unless every
    requested trading day is actually recorded, letting the caller fall
    through to the existing hub+yfinance path exactly as it already does for
    a source-unavailable/no-data result.
    """
    try:
        from backtest.loaders.stock_simulator_loader import DataLoader as _StockSimLoader

        result = _StockSimLoader().fetch([f"{code}.NS"], start, end)
    except Exception as exc:  # noqa: BLE001 — degrade to the existing fallback
        logger.debug("nifty50 stock_simulator fetch failed for %s: %s", code, exc)
        return None
    frame = result.get(f"{code}.NS")
    if frame is None or frame.empty:
        return None
    return frame.reset_index().rename(columns={"trade_date": "date"})


def _load_nifty50_panel(start: str, end: str) -> dict[str, pd.DataFrame]:
    """Nifty 50 panel: recorded stock_simulator data first (per constituent,
    full-range-or-omit), OpenAlgo/yfinance history for whatever it can't
    fully cover yet."""
    from src.tools.alpha_bench_tool import _wide_from_fetched

    symbols: list[str] = []
    constituent_source = "nifty50_constituents"
    try:
        from trade_integrations.dataflows.index_research.constituents import (
            load_nifty50_constituents,
        )

        rows = load_nifty50_constituents()
        symbols = [row.symbol.upper().strip() for row in rows if row.symbol]
    except Exception as exc:  # noqa: BLE001
        logger.warning("nifty50 constituent load failed (%s); using fallback", exc)

    if not symbols:
        symbols = list(_NIFTY50_FALLBACK_CODES)
        constituent_source = "hand-picked fallback"
        logger.warning("nifty50: using %d-name fallback (degraded run)", len(symbols))

    from trade_integrations.dataflows.index_research.alpha_bridge.india_ohlcv import (
        load_symbol_ohlcv,
    )

    fetched: dict[str, pd.DataFrame] = {}
    stock_simulator_hits = 0
    for code in symbols:
        frame = _load_nifty50_stock_simulator(code, start, end)
        if frame is not None:
            stock_simulator_hits += 1
        else:
            try:
                frame = load_symbol_ohlcv(code, start_date=start, end_date=end)
            except Exception as exc:  # noqa: BLE001
                logger.debug("nifty50 fetch failed for %s: %s", code, exc)
                continue
        if frame is None or frame.empty or "close" not in frame.columns:
            continue
        df = frame.copy()
        df["date"] = pd.to_datetime(df["date"])
        df = df.set_index("date").sort_index()
        mask = (df.index >= pd.Timestamp(start)) & (df.index <= pd.Timestamp(end))
        df = df.loc[mask]
        if df.empty:
            continue
        for col in ("open", "high", "low", "close", "volume"):
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
        keep = [c for c in ("open", "high", "low", "close", "volume") if c in df.columns]
        trimmed = df[keep].dropna(subset=["close"])
        if not trimmed.empty:
            fetched[code] = trimmed

    panel = _wide_from_fetched(fetched, include_amount=False)
    if all(k in panel for k in ("open", "high", "low", "close")):
        panel["vwap"] = (panel["open"] + panel["high"] + panel["low"] + panel["close"]) / 4.0

    panel["_meta"] = {
        "universe": "nifty50",
        "survivorship_bias": True,
        "constituent_source": constituent_source,
        "constituent_count": len(fetched),
        "stock_simulator_hits": stock_simulator_hits,
    }
    return panel


def register_nifty50_universe(universe_registry: dict[str, Any], universe_spec_cls: Any) -> None:
    """Register the ``nifty50`` universe into ``alpha_bench_tool.py``'s registry."""
    universe_registry["nifty50"] = universe_spec_cls(
        id="nifty50",
        market="equity_in",
        panel_loader=_load_nifty50_panel,
        constituent_source="nifty50_constituents",
        survivorship_bias=True,
    )
