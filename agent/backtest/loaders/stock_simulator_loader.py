"""India Market Protocol: feed recorded stock_simulator/stock_history data
into the backtest layer as the default (not fallback) ``india_equity`` source.

``stock_simulator``/``stock_history`` (in the sibling ``integrations/`` package
of this monorepo) records real NSE tick + option-chain data — including IV and
greeks as of commit ``8275719e`` — via scheduled INDmoney recording sessions.
This loader wraps ``trade_integrations.stock_history.api.StockHistory``
directly (not the ``stock_history_bridge`` HTTP-proxy module, whose
option-chain functions require a live, armed replay-clock singleton — the
wrong fit for an on-demand backtest fetch) so vibetrading's own research
surfaces (Correlation Matrix, Alpha Zoo, Options Lab) can read the same
recorded data OpenAlgo's simulator UI does.

Coverage policy — full requested range or nothing: the recorded dataset only
covers days actually recorded (a handful so far, growing over time). Because
``DataLoaderProtocol.fetch()`` returns one frame per symbol for the *whole*
requested range with no partial-range fallback mechanism upstream, silently
returning a partial frame would glue a few real days onto an otherwise-empty
multi-month backtest with no signal that most of the range is missing. This
loader instead omits a symbol entirely when its recorded-day coverage doesn't
span every requested trading day, so the registry's fallback chain
(``yahoo``/``yfinance``/``india_broker``) fills the full range instead. As
recording accumulates, more ranges naturally qualify here without any code
change.

Interval scope: ``"1D"`` only, resampled from the recorded 1-minute bars.
Every real production caller of ``resolve_loader``/``fetch`` for an equity
market passes ``"1D"`` (or nothing); an unsupported interval is rejected
explicitly rather than silently coerced, matching ``india_broker_loader``'s
convention.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, time as _time
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

import pandas as pd

from backtest.loaders.base import validate_date_range
from backtest.loaders.registry import register

logger = logging.getLogger(__name__)

_OUTPUT_COLUMNS = ["open", "high", "low", "close", "volume"]
_SUPPORTED_INTERVALS = {"1D", "1d"}

# Known India index tickers -> (bare symbol, exchange). Anything else is
# treated as an individual equity via the .NS/.BO suffix convention.
_INDEX_SYMBOLS: dict[str, tuple[str, str]] = {
    "NIFTY": ("NIFTY", "NSE_INDEX"),
    "NIFTY50": ("NIFTY", "NSE_INDEX"),
    "^NSEI": ("NIFTY", "NSE_INDEX"),
    "BANKNIFTY": ("BANKNIFTY", "NSE_INDEX"),
    "SENSEX": ("SENSEX", "BSE_INDEX"),
    "^BSESN": ("SENSEX", "BSE_INDEX"),
}


def _resolve_symbol(code: str) -> tuple[str, str]:
    """Map a project ticker to (bare_symbol, exchange) for StockHistory calls.

    Known index tickers map to their NSE_INDEX/BSE_INDEX exchange; everything
    else follows the .NS/.BO suffix convention used by ``india_broker_loader``
    (``.BO`` -> BSE, else NSE), since ``StockHistory.index_history`` already
    serves arbitrary NSE equity symbols, not just indices.
    """
    cleaned = code.strip()
    upper = cleaned.upper()
    if upper in _INDEX_SYMBOLS:
        return _INDEX_SYMBOLS[upper]
    if upper.endswith((".NS", ".BO")):
        base = cleaned[:-3]
        exchange = "BSE" if upper.endswith(".BO") else "NSE"
        return base, exchange
    return cleaned, "NSE"


def _ensure_stock_history():
    """Import and construct ``StockHistory``, or return ``None`` on any failure.

    Deferred and defensive: a standalone Vibe-Trading checkout not co-located
    with the trade monorepo (and without ``TRADE_STACK_ROOT`` set) simply
    reports this source unavailable, never crashes the loader registry.
    """
    try:
        from src.trade.hub_bridge import ensure_trade_stack_path

        ensure_trade_stack_path()
        from trade_integrations.stock_history.api import StockHistory

        return StockHistory()
    except Exception as exc:  # noqa: BLE001 — optional cross-repo dependency
        logger.debug("stock_simulator bridge unavailable: %s", exc)
        return None


def _bars_to_daily_frame(bars: list, start_date: str, end_date: str) -> Optional[pd.DataFrame]:
    """Resample recorded ``IndexBar`` rows (1-min) into a daily OHLCV frame.

    Grouped by each bar's ``trading_day`` — open of the first bar, high/low
    across the day, close of the last bar, volume summed. Returns ``None`` if
    there are no bars at all.
    """
    if not bars:
        return None
    frame = pd.DataFrame(
        {
            "trading_day": [b.trading_day for b in bars],
            "open": [b.open for b in bars],
            "high": [b.high for b in bars],
            "low": [b.low for b in bars],
            "close": [b.close for b in bars],
            "volume": [b.volume for b in bars],
            "ts_ist": [b.ts_ist for b in bars],
        }
    ).sort_values("ts_ist")

    daily = frame.groupby("trading_day", sort=True).agg(
        open=("open", "first"),
        high=("high", "max"),
        low=("low", "min"),
        close=("close", "last"),
        volume=("volume", "sum"),
    )
    daily.index = pd.to_datetime(daily.index)
    daily.index.name = "trade_date"

    lower = pd.Timestamp(start_date).normalize()
    upper = pd.Timestamp(end_date).normalize() + pd.Timedelta(days=1)
    daily = daily[(daily.index >= lower) & (daily.index < upper)]
    daily = daily.dropna(subset=["open", "high", "low", "close"]).sort_index()
    return daily[_OUTPUT_COLUMNS].astype(float) if not daily.empty else None


def _requested_trading_days(start_date: str, end_date: str) -> set[str]:
    """Business-day set for the requested range (no market-holiday calendar —
    a simple, self-contained baseline for the coverage completeness check)."""
    days = pd.bdate_range(pd.Timestamp(start_date).normalize(), pd.Timestamp(end_date).normalize())
    return {d.strftime("%Y-%m-%d") for d in days}


def _has_full_coverage(sh: Any, symbol: str, exchange: str, start_date: str, end_date: str) -> bool:
    requested = _requested_trading_days(start_date, end_date)
    if not requested:
        return False
    recorded = set(sh.recorded_index_days(symbol=symbol, exchange=exchange))
    return requested.issubset(recorded)


@register
class DataLoader:
    """Recorded stock_simulator/stock_history adapter for ``india_equity``."""

    name = "stock_simulator"
    markets = {"india_equity"}
    requires_auth = False
    volume_units = {"india_equity": "shares"}

    def is_available(self) -> bool:
        return _ensure_stock_history() is not None

    def fetch(
        self,
        codes: List[str],
        start_date: str,
        end_date: str,
        *,
        interval: str = "1D",
        fields: Optional[List[str]] = None,
    ) -> Dict[str, pd.DataFrame]:
        """Fetch recorded OHLCV history for ``codes``, full-range-or-omit."""
        del fields
        if not codes:
            return {}
        validate_date_range(start_date, end_date)

        if str(interval).strip() not in _SUPPORTED_INTERVALS:
            logger.warning(
                "stock_simulator only supports 1D; rejecting interval=%r", interval
            )
            return {}

        sh = _ensure_stock_history()
        if sh is None:
            return {}

        since_ist = pd.Timestamp(start_date).normalize().to_pydatetime()
        until_ist = (pd.Timestamp(end_date).normalize() + pd.Timedelta(days=1)).to_pydatetime()

        result: Dict[str, pd.DataFrame] = {}
        for code in codes:
            try:
                symbol, exchange = _resolve_symbol(code)
                if not _has_full_coverage(sh, symbol, exchange, start_date, end_date):
                    continue
                bars = sh.index_history(
                    symbol=symbol, exchange=exchange, since_ist=since_ist, until_ist=until_ist,
                )
                frame = _bars_to_daily_frame(bars, start_date, end_date)
            except Exception as exc:  # noqa: BLE001 — one bad symbol never aborts
                logger.warning("stock_simulator bridge failed for %s: %s", code, exc)
                continue
            if frame is not None and not frame.empty:
                result[code] = frame
        return result


def fetch_option_chain(
    *,
    underlying: str,
    exchange: str,
    spot: float,
    sim_now,
    expiry_date: str | None = None,
    strike_count: int = 10,
    expiry_anchor: date | None = None,
) -> dict[str, Any] | None:
    """Recorded/synthesized India option chain, reshaped to the
    ``normalize_option_chain_response``/``dataflows.options_research`` contract.

    ``StockHistory.option_chain_at`` returns a flat per-strike shape
    (``ce_ltp``/``pe_ltp``/``ce_oi``/... as sibling keys, plus a ``0.0``
    fallback for any missing greek/IV column); this translates each strike
    into the nested ``{"strike", "ce": {...}, "pe": {...}}`` shape with
    OpenAlgo-style leg keys (``ltp``, ``oi``, ``iv``, ``delta``, ``gamma``,
    ``theta``, ``vega``) that ``dataflows/options_research/`` and
    ``openalgo/market_data.py::normalize_option_chain_response`` already
    expect, and computes ``atm_strike``/``pcr`` since the source shape has
    neither. Not wired into any loader registry — call directly (Phase 4).
    """
    sh = _ensure_stock_history()
    if sh is None:
        return None
    raw = sh.option_chain_at(
        underlying=underlying, exchange=exchange, spot=spot, sim_now=sim_now,
        expiry_date=expiry_date, strike_count=strike_count, expiry_anchor=expiry_anchor,
    )
    if not raw or not raw.get("chain"):
        return None

    def _leg(row: dict, prefix: str) -> dict[str, Any]:
        return {
            "ltp": row.get(f"{prefix}_ltp"),
            "oi": row.get(f"{prefix}_oi"),
            "volume": row.get(f"{prefix}_volume"),
            "iv": row.get(f"{prefix}_iv"),
            "delta": row.get(f"{prefix}_delta"),
            "gamma": row.get(f"{prefix}_gamma"),
            "theta": row.get(f"{prefix}_theta"),
            "vega": row.get(f"{prefix}_vega"),
        }

    strikes = [
        {"strike": row.get("strike"), "ce": _leg(row, "ce"), "pe": _leg(row, "pe")}
        for row in raw["chain"]
    ]
    total_call_oi = int(raw.get("total_call_oi") or 0)
    total_put_oi = int(raw.get("total_put_oi") or 0)
    atm_strike = min(
        (s["strike"] for s in strikes if s["strike"] is not None),
        key=lambda k: abs(k - spot),
        default=None,
    )
    return {
        "underlying": raw.get("underlying", underlying),
        "underlying_ltp": raw.get("underlying_ltp", spot),
        "expiry_date": raw.get("expiry_date"),
        "atm_strike": atm_strike,
        "chain": strikes,
        "pcr": round(total_put_oi / total_call_oi, 4) if total_call_oi else None,
        "total_call_oi": total_call_oi,
        "total_put_oi": total_put_oi,
        "source": raw.get("source", "stock_simulator"),
    }


_IST = ZoneInfo("Asia/Kolkata")


def fetch_latest_option_chain(
    *,
    underlying: str,
    exchange: str,
    expiry_date: str | None = None,
    strike_count: int = 10,
) -> dict[str, Any] | None:
    """Option chain as of the most recently recorded session for ``underlying``.

    ``fetch_option_chain`` needs an explicit ``spot``/``sim_now``, which suits a
    backtest caller that already knows where it is in a replay — but Options
    Lab's chain browser just wants "whatever's most recent". This resolves the
    latest day in ``recorded_index_days`` and its closing spot bar, then
    delegates. Returns ``None`` (never a stale or fabricated chain) if nothing
    has been recorded yet for this underlying/exchange.

    When ``expiry_date`` isn't given, the "nearest expiry" default is anchored on the real
    India trading date, not on ``latest_day`` — the recorded archive can lag behind today by a
    session or more, and picking "nearest expiry >= latest_day" against a stale ``latest_day``
    can resolve an expiry that has since passed by the time it's actually served (see
    .claude/backlog/items/2026-08-27-india-chain-default-expiry-resolves-past-date.md). The
    expiry file this resolves to still carries data for every trading day up to and including
    ``latest_day`` (each weekly expiry file records that whole week, not just its own expiry),
    so anchoring expiry selection on real "today" doesn't lose any coverage.
    """
    sh = _ensure_stock_history()
    if sh is None:
        return None
    days = sh.recorded_index_days(symbol=underlying, exchange=exchange)
    if not days:
        return None
    latest_day = max(days)
    day_end = datetime.combine(
        datetime.strptime(latest_day, "%Y-%m-%d").date(), _time(15, 30), tzinfo=_IST
    )
    bar = sh.bar_at(symbol=underlying, exchange=exchange, sim_now=day_end)
    if bar is None:
        return None
    expiry_anchor = None
    if not expiry_date:
        from trade_integrations.dataflows.company_research.market import india_trading_date

        expiry_anchor = india_trading_date()
    result = fetch_option_chain(
        underlying=underlying, exchange=exchange, spot=bar.close, sim_now=day_end,
        expiry_date=expiry_date, strike_count=strike_count, expiry_anchor=expiry_anchor,
    )
    if result is not None:
        result["as_of"] = latest_day
    return result
