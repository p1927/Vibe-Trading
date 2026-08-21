"""NSE sectoral-index (Nifty Bank, Nifty IT, ...) universes for the Alpha Bench tool.

Fork-only extension registering India NSE sector indices as benchable Alpha
Zoo universes, on top of ``alpha_bench_tool.py``'s neutral registry mechanism
(``UniverseSpec`` / ``UNIVERSE_REGISTRY``) and its pre-existing
csi300/sp500/btc-usdt/nifty50 entries. Kept in a fork-owned sidecar per
docs/FORK_CONVENTIONS.md, Shape 1 ("new, self-contained behavior") — this is
~100 lines of India-specific logic that would otherwise sit inline in an
upstream-shared file. ``alpha_bench_tool.py`` carries one import plus one call
to :func:`register_nse_sector_universes`.
"""

from __future__ import annotations

import functools
import logging
from typing import Any

import pandas as pd

logger = logging.getLogger(__name__)

# NSE sectoral indices, benchable via the same shape as nifty50: universe id ->
# the `bulk_history_persist._NIFTYINDICES_CSV` method_name key that resolves its
# constituent CSV. Verified live against niftyindices.com/IndexConstituent/ on
# 2026-08-21 (see load_nse_sector_index_constituents). NIFTY FINANCIAL SERVICES
# and NIFTY PRIVATE BANK use irregular file-name slugs (ind_niftyfinancelist.csv,
# ind_nifty_privatebanklist.csv) that don't follow the other indices' plain
# ``ind_nifty<sector>list.csv`` pattern.
_NSE_SECTOR_INDICES: dict[str, str] = {
    "niftyauto": "niftyauto_equity_list",
    "niftybank": "niftybank_equity_list",
    "niftyfinancialservices": "niftyfinancialservices_equity_list",
    "niftyfmcg": "niftyfmcg_equity_list",
    "niftyit": "niftyit_equity_list",
    "niftymedia": "niftymedia_equity_list",
    "niftymetal": "niftymetal_equity_list",
    "niftypharma": "niftypharma_equity_list",
    "niftypsubank": "niftypsubank_equity_list",
    "niftyprivatebank": "niftyprivatebank_equity_list",
    "niftyrealty": "niftyrealty_equity_list",
    "niftyhealthcare": "niftyhealthcare_equity_list",
    "niftyconsumerdurables": "niftyconsumerdurables_equity_list",
    "niftyoilgas": "niftyoilgas_equity_list",
}


def _load_nse_sector_panel(
    index_id: str, method_name: str, start: str, end: str
) -> dict[str, pd.DataFrame]:
    """Generic NSE sectoral-index panel: niftyindices.com constituents + per-symbol OHLCV.

    Mirrors ``_load_nifty50_panel``'s shape, parameterized by index. There is
    no hand-picked fallback roster here (unlike nifty50) — these indices don't
    have a hardcoded survivor list, so a fetch failure degrades to an empty
    panel and ``_load_universe_panel`` raises rather than silently benching a
    fabricated basket.
    """
    from trade_integrations.dataflows.index_research.constituents import (
        load_nse_sector_index_constituents,
    )

    try:
        rows = load_nse_sector_index_constituents(method_name)
    except Exception as exc:  # noqa: BLE001
        logger.warning("%s constituent load failed: %s", index_id, exc)
        rows = []
    symbols = [row.symbol.upper().strip() for row in rows if row.symbol]

    from trade_integrations.dataflows.index_research.alpha_bridge.india_ohlcv import (
        load_symbol_ohlcv,
    )

    fetched: dict[str, pd.DataFrame] = {}
    for code in symbols:
        try:
            frame = load_symbol_ohlcv(code, start_date=start, end_date=end)
        except Exception as exc:  # noqa: BLE001
            logger.debug("%s fetch failed for %s: %s", index_id, code, exc)
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

    # Local import: avoids a module-load-time cycle with alpha_bench_tool,
    # which imports register_nse_sector_universes from this module.
    from src.tools.alpha_bench_tool import _wide_from_fetched

    panel = _wide_from_fetched(fetched, include_amount=False)
    if all(k in panel for k in ("open", "high", "low", "close")):
        panel["vwap"] = (panel["open"] + panel["high"] + panel["low"] + panel["close"]) / 4.0

    panel["_meta"] = {
        "universe": index_id,
        "survivorship_bias": True,
        "constituent_source": "niftyindices_constituents",
        "constituent_count": len(fetched),
    }
    return panel


def register_nse_sector_universes(universe_registry: dict[str, Any], universe_spec_cls: Any) -> None:
    """Register every NSE sectoral-index universe into ``universe_registry``.

    ``universe_spec_cls`` is ``alpha_bench_tool.UniverseSpec``, passed in by
    the caller rather than imported here, to keep this module free of any
    module-load-time dependency back onto ``alpha_bench_tool``.
    """
    universe_registry.update(
        {
            index_id: universe_spec_cls(
                id=index_id,
                market="equity_in",
                panel_loader=functools.partial(_load_nse_sector_panel, index_id, method_name),
                constituent_source="niftyindices_constituents",
                survivorship_bias=True,
            )
            for index_id, method_name in _NSE_SECTOR_INDICES.items()
        }
    )
