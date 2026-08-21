"""Fork-owned bridge: India ticker inference shared across backtest modules.

``correlation.py`` and ``benchmark.py`` both need to recognize India equity
codes (``.NS`` / ``.BO`` suffixes and NSE/BSE symbol conventions) as part of
their respective ``infer_market`` / ``_infer_market`` resolution chains. That
inference itself lives in the trade monorepo
(``trade_integrations.data_router.callers.infer_equity_market``), reached via
``src.trade.hub_bridge.ensure_trade_stack_path()``. Kept in one place per
docs/FORK_CONVENTIONS.md ("the same logic written twice") — both upstream
files import :func:`is_india_equity` rather than each carrying their own copy
of the try/except bridging dance.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def is_india_equity(code: str) -> bool:
    """Delegate India ticker-suffix inference to the fork-owned sidecar.

    Deferred and defensive: a standalone Vibe-Trading checkout not co-located
    with the trade monorepo (and without ``TRADE_STACK_ROOT`` set) simply
    reports no match here, falling through to the rest of the caller's market
    inference, rather than crashing.
    """
    try:
        from src.trade.hub_bridge import ensure_trade_stack_path

        ensure_trade_stack_path()
        from trade_integrations.data_router.callers import infer_equity_market

        return infer_equity_market(code) == "india_equity"
    except Exception as exc:  # noqa: BLE001 — optional cross-repo dependency
        logger.debug("trade_integrations bridge unavailable: %s", exc)
        return False
