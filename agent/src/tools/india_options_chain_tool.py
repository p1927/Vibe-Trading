"""Read-only India options-chain tool, source-selectable across four connectors.

Mirrors ``options_chain_tool.py``'s envelope shape (``calls``/``puts`` rows of
``contract_symbol, strike, last_price, bid, ask, volume, open_interest,
implied_volatility, in_the_money, expiration``) so the existing
``OptionsChainTable`` frontend component renders either market unmodified.
Kept as a separate tool/class from ``OptionsChainTool`` rather than a market
branch inside it, since the two pull from entirely different vendors with no
shared code path (Yahoo HTTP client vs. cross-repo ``stock_history``/OpenAlgo
bridges) and the US tool is also exposed to the chat agent under a US-only
description that should not change.

Sources — each is an explicit, user-selected choice with NO automatic
cross-source fallback. If the selected source has no data, the tool returns
an error envelope; it never silently degrades to a different vendor or
quality tier:

- ``stock_simulator`` (default): goes straight through ``stock_history``
  API — ``StockHistory.option_chain_at``, most recently recorded session.
  ``stock_history`` is this project's own public distribution layer for
  India data (it already wraps INDmoney/nselib internally on the recorder
  side); "stock_simulator" as a *source choice* means exactly this, not a
  separate live vendor bridge. No bid/ask in this tier's source fields
  (only LTP/OI/volume/greeks); ``bid``/``ask`` are left ``null`` here rather
  than approximated, so the UI renders "–" instead of a fabricated spread.
- ``indmoney`` / ``openalgo``: live chain pinned to one named broker,
  bypassing ``stock_history`` entirely — for a caller that specifically
  wants a live quote instead of the recorded archive. Both carry real
  top-of-book bid/ask. No fallback between the two, or to ``stock_simulator``,
  if the pinned vendor has nothing.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from src.agent.tools import BaseTool

_DEFAULT_STRIKE_COUNT = 10
_LIVE_VENDOR_SOURCES = ("indmoney", "openalgo")
_VALID_SOURCES = ("stock_simulator",) + _LIVE_VENDOR_SOURCES


class IndiaOptionsChainTool(BaseTool):
    """Fetch an NSE/BSE options chain (calls + puts), source-selectable."""

    name = "get_india_options_chain"
    description = (
        "Fetch the India-listed options chain (calls and puts) for one "
        "underlying, e.g. NIFTY, BANKNIFTY, or an NSE stock like RELIANCE.NS: "
        "per-strike LTP, bid/ask, volume, open interest, implied volatility, "
        "greeks, and the in-the-money flag. Source-selectable, no automatic "
        "fallback between sources — 'stock_simulator' (default: recorded/"
        "replay archive via stock_history API, no bid/ask), or 'indmoney'/"
        "'openalgo' (live, pinned to one broker). "
        'Example: get_india_options_chain(ticker="NIFTY").'
    )
    parameters = {
        "type": "object",
        "properties": {
            "ticker": {
                "type": "string",
                "description": (
                    "India underlying symbol: an index name (NIFTY, "
                    "BANKNIFTY, SENSEX) or an NSE/BSE equity with its "
                    "exchange suffix, e.g. 'RELIANCE.NS'. Required."
                ),
            },
            "expiry_date": {
                "type": "string",
                "description": "Optional expiry as YYYY-MM-DD. Omit for the nearest expiry.",
            },
            "source": {
                "type": "string",
                "enum": list(_VALID_SOURCES),
                "description": (
                    "Which connector serves the chain, no automatic fallback "
                    "between them: 'stock_simulator' (default, via "
                    "stock_history API) or 'indmoney'/'openalgo' (live, "
                    "pinned to one broker)."
                ),
            },
        },
        "required": ["ticker"],
    }

    def execute(self, **kwargs: Any) -> str:
        """Return a JSON-string envelope with the calls/puts chain.

        Returns:
            On success: ``{"ok": true, "market": "india_equity", "source":
            str, "data": {...}}`` in the same shape as ``OptionsChainTool``.
            On failure: ``{"ok": false, "error": str}`` — never a silent
            fallback to a different source.
        """
        ticker = str(kwargs.get("ticker") or "").strip()
        if not ticker:
            return _error("ticker is required")

        expiry_date = kwargs.get("expiry_date")
        if expiry_date is not None:
            expiry_date = str(expiry_date).strip() or None

        source = str(kwargs.get("source") or "stock_simulator").strip().lower()
        if source not in _VALID_SOURCES:
            return _error(f"source must be one of {_VALID_SOURCES}")

        try:
            underlying, exchange = _resolve_india_symbol(ticker)
        except Exception as exc:  # noqa: BLE001 — surface as error envelope
            return _error(f"could not resolve India symbol {ticker!r}: {exc}")

        try:
            chain = _fetch_chain(source, underlying, exchange, expiry_date)
        except Exception as exc:  # noqa: BLE001 — surface as error envelope
            return _error(f"{source} chain request failed: {exc}")

        if chain is None:
            return _error(
                f"no {source} option chain available for {underlying} "
                f"({'nearest expiry' if expiry_date is None else expiry_date})"
            )

        # Best-effort — a listing failure shouldn't fail a chain that
        # already loaded successfully; the dropdown just falls back to the
        # single expiry the chain itself carries.
        try:
            expiries = _fetch_expiries(source, underlying, exchange)
        except Exception:  # noqa: BLE001
            expiries = []

        return _success(ticker, source, chain, expiries, underlying=underlying)


def _resolve_india_symbol(ticker: str) -> tuple[str, str]:
    """Map a user-typed India ticker to ``(underlying, exchange)``.

    Reuses ``stock_simulator_loader``'s symbol table (index names + .NS/.BO
    convention) so every source agrees on the same underlying/exchange pair.
    """
    from backtest.loaders.stock_simulator_loader import _resolve_symbol

    return _resolve_symbol(ticker)


def _fetch_chain(
    source: str, underlying: str, exchange: str, expiry_date: Optional[str]
) -> Optional[Dict[str, Any]]:
    """Dispatch to exactly one connector — never tries a second on failure."""
    if source == "stock_simulator":
        from backtest.loaders.stock_simulator_loader import fetch_latest_option_chain

        return fetch_latest_option_chain(
            underlying=underlying,
            exchange=exchange,
            expiry_date=expiry_date,
            strike_count=_DEFAULT_STRIKE_COUNT,
        )

    if source == "indmoney":
        from trade_integrations.dataflows.stock_history_bridge._market_data import (
            _get_live_option_chain_from_indmoney,
        )

        result = _get_live_option_chain_from_indmoney(
            underlying=underlying, exchange=exchange,
            strike_count=_DEFAULT_STRIKE_COUNT, expiry_date=expiry_date,
        )
    else:  # "openalgo" — pinned, live broker only
        from trade_integrations.dataflows.stock_history_bridge._market_data import (
            _normalize_openalgo_chain,
        )
        from trade_integrations.dataflows.stock_history_bridge._openalgo_client import (
            OpenAlgoClient,
            OpenAlgoError,
        )

        try:
            body = OpenAlgoClient().get_option_chain(
                symbol=underlying, exchange=exchange,
                expiry_date=expiry_date, strike_count=_DEFAULT_STRIKE_COUNT,
            )
        except OpenAlgoError:
            return None
        result = _normalize_openalgo_chain(body)

    return result if result and result.get("strikes") else None


def _options_exchange(underlying: str, exchange: str) -> str:
    """NFO/BFO for OpenAlgo's expiry endpoint, derived from the same
    underlying/exchange metadata the recorded universe uses where known,
    else the standard NSE->NFO / BSE->BFO convention."""
    from trade_integrations.stock_simulator.master_contract import UNDERLYING_META

    meta = UNDERLYING_META.get(underlying.upper())
    if meta:
        return meta["options_exchange"]
    return "BFO" if exchange.upper().startswith("BSE") else "NFO"


def _fetch_expiries(source: str, underlying: str, exchange: str) -> List[str]:
    """All available expiry dates (``YYYY-MM-DD``, ascending) for the
    dropdown — best-effort, separate from the single expiry a chain fetch
    resolves on its own. Not every source can list every expiry equally
    cheaply/reliably, so each has its own path rather than one shared call."""
    if source == "stock_simulator":
        from trade_integrations.stock_history.api import StockHistory

        # The archive can hold years of expiries (every weekly/monthly ever
        # recorded) — real data, but unusable as a flat dropdown list. Keep
        # only the most recent ones, same intent as list_expiries' max_count
        # for the live vendor sources.
        all_recorded = sorted(
            StockHistory().recorded_option_expiries(symbol=underlying, exchange=exchange)
        )
        return all_recorded[-24:]

    if source == "indmoney":
        from trade_integrations.stock_simulator.recorder.ind_client import IndClient

        return IndClient().list_expiries(underlying, max_count=6)

    from trade_integrations.openalgo.market_data import fetch_option_expiry_dates

    return list(
        fetch_option_expiry_dates(
            underlying, _options_exchange(underlying, exchange), instrument_type="options"
        )
    )


def _expiry_epoch(expiry_date: Optional[str]) -> Optional[int]:
    """Convert a ``YYYY-MM-DD`` expiry into epoch seconds (midnight UTC).

    Kept numeric so ``OptionsChainTable``'s expiration dropdown (which does
    ``Number(value)``) works unmodified for India rows, same as Yahoo's.
    """
    if not expiry_date:
        return None
    try:
        dt = datetime.strptime(str(expiry_date)[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
        return int(dt.timestamp())
    except ValueError:
        return None


# India IV is reported in percentage points (e.g. 11.91 meaning 11.91%) by
# every source here — INDmoney, OpenAlgo, and the recorded archive alike, all
# passing the recorder's raw INDmoney value straight through. Yahoo's
# `impliedVolatility` — the convention `implied_volatility` follows
# everywhere it's rendered (OptionsChainTable's `fmtIv` does `v * 100`) — is
# a fraction (0.1191). Normalize once here so every market renders the same
# way downstream.
def _normalize_iv(iv: Any) -> Optional[float]:
    return iv / 100 if isinstance(iv, (int, float)) else None


def _leg_row_live(
    strike: float, leg: Dict[str, Any], option_type: str,
    underlying_ltp: Optional[float], expiration: Optional[int],
) -> Dict[str, Any]:
    """Leg row from a live source (``stock_simulator``/``indmoney``/``openalgo``) —
    these carry real top-of-book bid/ask (``top_bid_price``/``top_ask_price``)."""
    itm = None
    if underlying_ltp is not None:
        itm = strike < underlying_ltp if option_type == "call" else strike > underlying_ltp
    return {
        "contract_symbol": f"{option_type[0].upper()}{strike:g}",
        "strike": strike,
        "last_price": leg.get("last_price"),
        "bid": leg.get("top_bid_price"),
        "ask": leg.get("top_ask_price"),
        "volume": leg.get("volume"),
        "open_interest": leg.get("oi"),
        "implied_volatility": _normalize_iv(leg.get("iv")),
        "in_the_money": bool(itm),
        "expiration": expiration,
    }


def _leg_row_history(
    strike: float, leg: Dict[str, Any], option_type: str,
    underlying_ltp: Optional[float], expiration: Optional[int],
) -> Dict[str, Any]:
    """Leg row from ``stock_simulator`` (via ``stock_history`` API, recorded
    archive) — no bid/ask field in the source data, left ``null`` rather
    than approximated from LTP."""
    itm = None
    if underlying_ltp is not None:
        itm = strike < underlying_ltp if option_type == "call" else strike > underlying_ltp
    return {
        "contract_symbol": f"{option_type[0].upper()}{strike:g}",
        "strike": strike,
        "last_price": leg.get("ltp"),
        "bid": None,
        "ask": None,
        "volume": leg.get("volume"),
        "open_interest": leg.get("oi"),
        "implied_volatility": _normalize_iv(leg.get("iv")),
        "in_the_money": bool(itm),
        "expiration": expiration,
    }


def _lot_size_for(underlying: str) -> int:
    """Exchange lot size for the contract multiplier — ``UNDERLYING_META`` for
    the known indexes (NIFTY=25, BANKNIFTY=15, SENSEX=10), else ``1`` for
    individual equities (cash-market convention, same fallback
    ``master_contract._equity_row`` already uses)."""
    from trade_integrations.stock_simulator.master_contract import UNDERLYING_META

    meta = UNDERLYING_META.get(underlying.upper())
    return meta["lotsize"] if meta else 1


def _success(
    ticker: str,
    source: str,
    chain: Dict[str, Any],
    expiries: List[str] | None = None,
    *,
    underlying: str,
) -> str:
    underlying_ltp = chain.get("underlying_ltp")
    expiration = _expiry_epoch(chain.get("expiry_date"))
    calls: List[Dict[str, Any]] = []
    puts: List[Dict[str, Any]] = []

    if source in _LIVE_VENDOR_SOURCES:
        rows, leg_row = chain.get("strikes") or [], _leg_row_live
    else:  # "stock_simulator" — recorded/replay archive shape
        rows, leg_row = chain.get("chain") or [], _leg_row_history

    for row in rows:
        strike = row.get("strike")
        if strike is None:
            continue
        ce, pe = row.get("ce") or {}, row.get("pe") or {}
        calls.append(leg_row(strike, ce, "call", underlying_ltp, expiration))
        puts.append(leg_row(strike, pe, "put", underlying_ltp, expiration))

    all_expiries = sorted({e for e in (expiries or []) if e})
    if chain.get("expiry_date") and chain["expiry_date"] not in all_expiries:
        all_expiries.append(chain["expiry_date"])
        all_expiries.sort()
    expirations = [e for e in (_expiry_epoch(d) for d in all_expiries) if e is not None]
    if not expirations and expiration is not None:
        expirations = [expiration]

    data = {
        "ticker": ticker,
        "expiration": expiration,
        "expirations": expirations,
        "underlying_ltp": underlying_ltp,
        "lot_size": _lot_size_for(underlying),
        "calls_count": len(calls),
        "puts_count": len(puts),
        "calls": calls,
        "puts": puts,
    }
    return json.dumps(
        {"ok": True, "market": "india_equity", "source": source, "data": data},
        ensure_ascii=False,
    )


def _error(message: str) -> str:
    return json.dumps({"ok": False, "error": message}, ensure_ascii=False)


# The only underlyings this codebase actually knows lot sizes/options-exchange
# metadata for (`master_contract.UNDERLYING_META`) — the same set every India
# source here resolves without a suffix. Individual equities are picked via
# free-text `.NS`/`.BO` entry; only `stock_simulator` (stock_history's local
# archive) can cheaply enumerate which ones have recorded chain data (a
# metadata-only parquet listing — the live vendor sources would need an
# ~80k-row scrip-master download per request).
_KNOWN_INDEXES = ("NIFTY", "BANKNIFTY", "SENSEX")


def list_india_underlyings(source: str) -> Dict[str, Any]:
    """Available underlyings for the picker: known indexes always, plus
    recorded equities when ``source == "stock_simulator"`` (``None`` — not
    an empty list — for the live vendor sources, so the UI can tell "not
    offered here" apart from "genuinely nothing recorded")."""
    equities: Optional[List[str]] = None
    if source == "stock_simulator":
        from trade_integrations.stock_history.api import StockHistory

        equities = sorted(StockHistory().recorded_equities())
    return {
        "indexes": list(_KNOWN_INDEXES),
        "equities": equities,
        "lot_sizes": {name: _lot_size_for(name) for name in _KNOWN_INDEXES},
    }
