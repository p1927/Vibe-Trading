"""The ONE server-side source of mandate ceilings (CEO decision, 2026-10-14).

Ceilings used to be a model argument of ``propose_mandate_profiles`` and ``commit_mandate``
re-validated against that same model-supplied snapshot, so a model could raise its own cap, and
every mandate defaulted to ``us_equity``/USD. They now come from Trade's own risk config
(:mod:`trade_integrations.risk.limits` / ``autonomous_agents.defaults``: budget, daily-loss cap,
lot cap) and Trade's market default (India: NSE, INR). The model may only REQUEST values at or
below these (:func:`check_request`, loud rejection); ``commit_mandate`` re-reads this module and
never trusts the proposal's stored snapshot.

The persisted mandate field names keep their historical ``_usd`` suffix; the numbers are in the
ceiling's ``currency`` (INR for the default India market).
"""

from __future__ import annotations

import math
from typing import Any, Mapping

from trade_integrations.risk import limits

DEFAULT_MARKET = "IN"
DEFAULT_CURRENCY = "INR"
#: Highest daily trade count any proposal profile template offers.
MAX_TRADES_PER_DAY = 10
#: Instrument classes Trade executes in India (equity cash + NIFTY options); cash-only leverage.
INSTRUMENT_CEILING = ("equity", "option")
ASSET_CLASS_CEILING = ("in_equity",)
DEFAULT_UNIVERSE = ("RELIANCE", "HDFCBANK", "TCS", "INFY")

#: Keys a model may request; numeric ones must be at/below the server value.
_NUMERIC = (
    "account_funding_usd",
    "max_order_notional_usd",
    "max_total_exposure_usd",
    "max_trades_per_day",
    "max_daily_loss_inr",
    "max_lots",
)
_ALIASES = {
    "max_order_usd": "max_order_notional_usd",
    "daily_trade_cap": "max_trades_per_day",
    "max_leverage": "leverage",
    "instruments": "allowed_instruments",
}
_LISTS = ("allowed_instruments", "asset_classes")
_PASSTHROUGH = ("universe", "ceilings_ref", "market", "currency")


class CeilingError(ValueError):
    """A requested ceiling exceeds (or is not comparable to) the server ceiling."""


def order_capable_connectors() -> frozenset[str]:
    """Connector keys with at least one non-readonly profile that can place orders."""
    from src.trading.profiles import list_profiles

    return frozenset(
        p.connector
        for p in list_profiles()
        if not p.readonly and any(c.startswith("orders.place") for c in p.capabilities)
    )


def server_ceilings(broker: str) -> dict[str, Any]:
    """Return the server ceilings for ``broker`` (an order-capable connector key).

    Raises:
        CeilingError: ``broker`` has no order-capable profile.
    """
    key = str(broker or "").strip().lower()
    if key not in order_capable_connectors():
        raise CeilingError(f"no order-capable trading profile for broker {broker!r}")
    budget = limits.budget_inr({})
    ceilings: dict[str, Any] = {
        "market": DEFAULT_MARKET,
        "currency": DEFAULT_CURRENCY,
        "account_funding_usd": budget,
        "max_order_notional_usd": budget,
        "max_total_exposure_usd": budget,
        "max_trades_per_day": MAX_TRADES_PER_DAY,
        "max_daily_loss_inr": limits.max_daily_loss_inr({}),
        "leverage": "none",
        "allowed_instruments": list(INSTRUMENT_CEILING),
        "asset_classes": list(ASSET_CLASS_CEILING),
        "universe": list(DEFAULT_UNIVERSE),
    }
    lots = limits.max_lots({})
    if lots is not None:
        ceilings["max_lots"] = lots
    return ceilings


def tighten(ceilings: dict[str, Any], broker_side: Mapping[str, Any] | None) -> dict[str, Any]:
    """Return ``ceilings`` lowered by any broker-reported numeric ceiling (never raised)."""
    out = dict(ceilings)
    for key, value in (broker_side or {}).items():
        if key in out and _real(value) and _real(out[key]) and value > 0:
            out[key] = min(out[key], value)
    return out


def _real(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def check_request(requested: Mapping[str, Any], server: Mapping[str, Any]) -> dict[str, Any]:
    """Validate a model-requested ceiling snapshot against ``server``; return the effective one.

    Every numeric value must be finite, non-negative and at or below the server value; lists must
    be subsets; leverage must not exceed the server's. Unknown keys are rejected, not ignored.

    Raises:
        CeilingError: on the first violation (the tool turns it into an error envelope).
    """
    effective = dict(server)
    for raw_key, value in requested.items():
        key = _ALIASES.get(raw_key, raw_key)
        if key in _PASSTHROUGH:
            if key in ("market", "currency") and value != server.get(key):
                raise CeilingError(f"{raw_key}={value!r} differs from the server {key} {server.get(key)!r}")
            if key in ("universe", "ceilings_ref"):
                effective[key] = value
            continue
        if key in _NUMERIC:
            if not _real(value) or value < 0:
                raise CeilingError(f"{raw_key}={value!r} must be a finite non-negative number")
            cap = server.get(key)
            if cap is None or value > cap:
                raise CeilingError(f"{raw_key}={value} exceeds the server ceiling {cap!r}")
            effective[key] = value
        elif key in _LISTS:
            if not isinstance(value, list) or not all(item in server[key] for item in value):
                raise CeilingError(f"{raw_key}={value!r} is not within the server ceiling {server[key]!r}")
            effective[key] = list(value)
        elif key == "leverage":
            if value not in (None, "none", 1, 1.0):
                raise CeilingError(f"{raw_key}={value!r} exceeds the server ceiling (cash-only)")
        else:
            raise CeilingError(f"unknown ceiling key {raw_key!r}")
    return effective
