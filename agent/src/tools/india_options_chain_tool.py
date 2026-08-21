"""Read-only India options-chain tool, source-switchable between the recorded
stock_simulator dataset and live OpenAlgo broker data.

Mirrors ``options_chain_tool.py``'s envelope shape (``calls``/``puts`` rows of
``contract_symbol, strike, last_price, bid, ask, volume, open_interest,
implied_volatility, in_the_money, expiration``) so the existing
``OptionsChainTable`` frontend component renders either market unmodified.
Kept as a separate tool/class from ``OptionsChainTool`` rather than a market
branch inside it, since the two pull from entirely different vendors with no
shared code path (Yahoo HTTP client vs. cross-repo ``stock_history``/OpenAlgo
bridges) and the US tool is also exposed to the chat agent under a US-only
description that should not change.

India chain vendors have no bid/ask quotes in the fields we read (only
LTP/OI/volume/greeks) — ``bid``/``ask`` are left ``null`` rather than
approximated from LTP, so the UI renders "–" instead of a fabricated spread.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from src.agent.tools import BaseTool

_DEFAULT_STRIKE_COUNT = 10
_VALID_SOURCES = ("stock_simulator", "openalgo")


class IndiaOptionsChainTool(BaseTool):
    """Fetch an NSE/BSE options chain (calls + puts), source-selectable."""

    name = "get_india_options_chain"
    description = (
        "Fetch the India-listed options chain (calls and puts) for one "
        "underlying, e.g. NIFTY, BANKNIFTY, or an NSE stock like RELIANCE.NS: "
        "per-strike LTP, volume, open interest, implied volatility, greeks, "
        "and the in-the-money flag. Source-selectable — 'stock_simulator' "
        "(recorded/replay, default) or 'openalgo' (live broker feed). "
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
                    "Which connector serves the chain: 'stock_simulator' "
                    "(recorded/replay data, default) or 'openalgo' (live "
                    "broker feed)."
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
            On failure: ``{"ok": false, "error": str}``.
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

        return _success(ticker, source, chain)


def _resolve_india_symbol(ticker: str) -> tuple[str, str]:
    """Map a user-typed India ticker to ``(underlying, exchange)``.

    Reuses ``stock_simulator_loader``'s symbol table (index names + .NS/.BO
    convention) so both sources agree on the same underlying/exchange pair.
    """
    from backtest.loaders.stock_simulator_loader import _resolve_symbol

    return _resolve_symbol(ticker)


def _fetch_chain(
    source: str, underlying: str, exchange: str, expiry_date: Optional[str]
) -> Optional[Dict[str, Any]]:
    if source == "stock_simulator":
        from backtest.loaders.stock_simulator_loader import fetch_latest_option_chain

        return fetch_latest_option_chain(
            underlying=underlying,
            exchange=exchange,
            expiry_date=expiry_date,
            strike_count=_DEFAULT_STRIKE_COUNT,
        )

    from trade_integrations.openalgo import market_data as openalgo_market_data

    result = openalgo_market_data.fetch_option_chain(
        underlying, exchange, expiry_date=expiry_date, strike_count=_DEFAULT_STRIKE_COUNT
    )
    return result if result and result.get("chain") else None


def _expiry_epoch(expiry_date: Optional[str]) -> Optional[int]:
    """Convert a ``YYYY-MM-DD`` expiry into epoch seconds (midnight UTC).

    Kept numeric so ``OptionsChainTable``'s expiration dropdown (which does
    ``Number(value)``) works unmodified for India rows, same as Yahoo's.
    """
    if not expiry_date:
        return None
    try:
        dt = datetime.strptime(expiry_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        return int(dt.timestamp())
    except ValueError:
        return None


def _leg_row(strike: float, leg: Dict[str, Any], option_type: str, underlying_ltp: Optional[float], expiration: Optional[int]) -> Dict[str, Any]:
    itm = None
    if underlying_ltp is not None:
        itm = strike < underlying_ltp if option_type == "call" else strike > underlying_ltp
    # India sources (INDmoney/OpenAlgo) report IV in percentage points
    # (e.g. 11.91 meaning 11.91%); Yahoo's `impliedVolatility` — the
    # convention `implied_volatility` follows everywhere it's rendered
    # (OptionsChainTable's `fmtIv` does `v * 100`) — is a fraction (0.1191).
    # Normalize here so both markets render the same way downstream.
    iv = leg.get("iv")
    implied_volatility = iv / 100 if isinstance(iv, (int, float)) else None
    return {
        "contract_symbol": f"{option_type[0].upper()}{strike:g}",
        "strike": strike,
        "last_price": leg.get("ltp"),
        "bid": None,
        "ask": None,
        "volume": leg.get("volume"),
        "open_interest": leg.get("oi"),
        "implied_volatility": implied_volatility,
        "in_the_money": bool(itm),
        "expiration": expiration,
    }


def _success(ticker: str, source: str, chain: Dict[str, Any]) -> str:
    underlying_ltp = chain.get("underlying_ltp")
    expiration = _expiry_epoch(chain.get("expiry_date"))
    calls: List[Dict[str, Any]] = []
    puts: List[Dict[str, Any]] = []
    for row in chain.get("chain") or []:
        strike = row.get("strike")
        if strike is None:
            continue
        ce, pe = row.get("ce") or {}, row.get("pe") or {}
        calls.append(_leg_row(strike, ce, "call", underlying_ltp, expiration))
        puts.append(_leg_row(strike, pe, "put", underlying_ltp, expiration))

    data = {
        "ticker": ticker,
        "expiration": expiration,
        "expirations": [expiration] if expiration is not None else [],
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
