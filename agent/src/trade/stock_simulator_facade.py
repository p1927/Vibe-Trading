"""vibetrading's one door to stock-market data: the ``stock_simulator`` facade, in-process.

D117: every caller reads through ``StockSimulatorClient``, cold/same-process ones included, via
its ``mode="local"`` path (same route functions the HTTP service runs, no network hop, no token).
Sidecar module per docs/FORK_CONVENTIONS.md: the call sites keep their ``StockHistory``-shaped
calls; this adapter maps them onto the client's JSON-dict shape.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from types import SimpleNamespace
from typing import Any

from src.trade.hub_bridge import ensure_trade_stack_path

# `/history/index_bars` truncates to its most recent 20k rows (1-min bars): ~30 calendar days stay well under.
_BAR_CHUNK_DAYS = 30


def sim_client():
    ensure_trade_stack_path()
    from trade_integrations.stock_simulator.client import StockSimulatorClient

    return StockSimulatorClient(mode="local")


class FacadeHistory:
    """``StockHistory``-shaped read surface over the facade (only what the backtest loader uses)."""

    def __init__(self) -> None:
        self._c = sim_client()

    def recorded_index_days(self, *, symbol: str, exchange: str) -> list[str]:
        return self._c.get_recorded_index_days(symbol=symbol, exchange=exchange)["data"]

    def index_history(self, *, symbol: str, exchange: str, since_ist: datetime, until_ist: datetime) -> list:
        bars: list = []
        lo = since_ist
        while lo < until_ist:
            hi = min(lo + timedelta(days=_BAR_CHUNK_DAYS), until_ist)
            resp = self._c.get_index_history(
                symbol=symbol, exchange=exchange, since_ist=lo.isoformat(), until_ist=hi.isoformat()
            )
            if resp.get("truncated"):  # silent partial history would corrupt the daily resample
                raise RuntimeError(f"index_history {symbol} {lo}..{hi} truncated by the facade; shrink _BAR_CHUNK_DAYS")
            bars.extend(_bar(b) for b in resp["data"])
            lo = hi
        return bars

    def bar_at(self, *, symbol: str, exchange: str, sim_now: datetime):
        row = self._c.get_bar_at(symbol=symbol, exchange=exchange, sim_now=sim_now.isoformat())["data"]
        return _bar(row) if row is not None else None

    def option_chain_at(
        self, *, underlying: str, exchange: str, spot: float, sim_now: datetime,
        expiry_date: str | None = None, strike_count: int = 10, expiry_anchor: date | None = None,
    ) -> dict[str, Any] | None:
        return self._c.get_option_chain_at(
            underlying=underlying, exchange=exchange, spot=spot, sim_now=sim_now.isoformat(),
            expiry_date=expiry_date, strike_count=strike_count,
            expiry_anchor=expiry_anchor.isoformat() if expiry_anchor else None,
        )["data"]


def _bar(row: dict[str, Any]) -> SimpleNamespace:
    ts = row["ts_ist"]
    return SimpleNamespace(**{**row, "ts_ist": datetime.fromisoformat(ts) if isinstance(ts, str) else ts})
