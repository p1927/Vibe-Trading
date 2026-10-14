"""Robinhood order-intent extractor (SPEC.md Mandate Enforcement §4).

Maps Robinhood's ``place_equity_order`` tool kwargs to the normalized
:class:`~src.live.enforcement.OrderIntent`. Pinned against the frozen Robinhood
catalog: the only order-placing WRITE tool is ``place_equity_order``
(``cancel_equity_order`` is a WRITE but not an *order placement* — it carries no
notional/quantity to enforce, so it is not an order-intent tool here).

``place_equity_order`` is sized by ``symbol`` + ``side`` plus exactly one (or,
defended against, both) of ``notional_usd`` / ``quantity`` (``dollar_amount``
accepted as a notional alias). The extractor maps these concrete fields and
returns ``None`` (→ DENY) on anything missing or ambiguous — it never guesses,
so the gate defaults to the safe state. Unknown extra keys are ignored. When
both a notional and a quantity are present the extractor surfaces BOTH; the gate
reconciles them to the larger enforced notional (closing the notional+quantity
bypass).
"""

from __future__ import annotations

import math

from src.live.enforcement import OrderIntent
from src.live.mandate.model import InstrumentType

#: Remote tool names this extractor recognizes as order placements. Frozen to
#: the canonical catalog: ``place_equity_order`` is the sole order-placing WRITE
#: tool (``cancel_equity_order`` carries no order intent to size).
_ORDER_TOOLS = frozenset({"place_equity_order"})

#: Accepted side spellings → normalized ``"buy"`` / ``"sell"``.
_SIDE_ALIASES = {
    "buy": "buy",
    "b": "buy",
    "long": "buy",
    "sell": "sell",
    "s": "sell",
    "short": "sell",
}

#: Broker instrument-type spellings → :class:`InstrumentType`.
_INSTRUMENT_ALIASES = {
    "equity": InstrumentType.EQUITY,
    "stock": InstrumentType.EQUITY,
    "stocks": InstrumentType.EQUITY,
    "etf": InstrumentType.ETF,
    "option": InstrumentType.OPTION,
    "options": InstrumentType.OPTION,
    "crypto": InstrumentType.CRYPTO,
    "cryptocurrency": InstrumentType.CRYPTO,
}

#: Order-size keys for the notional path (``dollar_amount`` is the Robinhood
#: dollar-based order field; ``notional_usd`` is the normalized name).
_NOTIONAL_KEYS = ("notional_usd", "notional", "dollar_amount", "amount")

#: Order-size keys for the share/contract/coin quantity path.
_QUANTITY_KEYS = ("quantity", "qty", "shares", "units")

#: Price keys of a limit/stop order. A quantity-sized buy is capped at the LARGEST one present (a limit buy
#: fills anywhere up to its limit), so the notional cap is not sized at the quote alone.
_LIMIT_PRICE_KEYS = ("limit_price", "price", "limit", "stop_price")


def extract_order_intent(remote_name: str, kwargs: dict) -> OrderIntent | None:
    """Parse Robinhood ``place_equity_order`` kwargs into a normalized :class:`OrderIntent`.

    Returns ``None`` (→ DENY) whenever the tool is not a recognized order tool,
    a required field is absent, or a field is ambiguous/invalid. Never guesses;
    unknown extra keys are ignored.

    Args:
        remote_name: The broker's un-prefixed remote tool name (e.g.
            ``"place_equity_order"``).
        kwargs: Raw tool-call arguments the agent passed to the order tool.

    Returns:
        A normalized :class:`OrderIntent`, or ``None`` when the order cannot be
        unambiguously parsed.
    """
    if remote_name not in _ORDER_TOOLS:
        return None
    if not isinstance(kwargs, dict):
        return None

    symbol = _extract_symbol(kwargs)
    if symbol is None:
        return None

    side = _extract_side(kwargs)
    if side is None:
        return None

    instrument = _extract_instrument(kwargs)
    if instrument is None:
        return None

    notional, quantity = _extract_size(kwargs)
    # Need at least one of notional / quantity to size the order. Both present is
    # allowed and surfaced — the gate reconciles to the larger enforced notional.
    if notional is None and quantity is None:
        return None

    limit_price = _limit_price(kwargs)
    if limit_price is _INVALID:
        return None

    return OrderIntent(
        symbol=symbol,
        side=side,
        notional_usd=notional,
        quantity=quantity,
        instrument_type=instrument,
        limit_price=limit_price,
    )


def _extract_symbol(kwargs: dict) -> str | None:
    """Return the normalized upper-case symbol, or ``None`` if absent."""
    for key in ("symbol", "ticker", "instrument"):
        value = kwargs.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip().upper()
    return None


def _extract_side(kwargs: dict) -> str | None:
    """Return normalized ``"buy"`` / ``"sell"``, or ``None`` if ambiguous."""
    for key in ("side", "action", "direction"):
        value = kwargs.get(key)
        if isinstance(value, str):
            normalized = _SIDE_ALIASES.get(value.strip().lower())
            if normalized is not None:
                return normalized
    return None


def _extract_instrument(kwargs: dict) -> InstrumentType | None:
    """Return the mapped :class:`InstrumentType`, or ``None`` if absent/unknown.

    An absent/unknown instrument is ambiguous → DENY (fail-closed), never a
    silent default.
    """
    for key in ("instrument_type", "asset_class", "type", "instrument_class"):
        value = kwargs.get(key)
        if isinstance(value, str):
            mapped = _INSTRUMENT_ALIASES.get(value.strip().lower())
            if mapped is not None:
                return mapped
    return None


def _extract_size(kwargs: dict) -> tuple[float | None, float | None]:
    """Return ``(notional_usd, quantity)``, each parsed or ``None``.

    Both may be returned non-``None`` (a notional+quantity order); the gate
    reconciles them. An invalid value for a present key yields ``None`` for that
    path (→ fail-closed downstream if nothing else sizes the order).
    """
    notional = _first_positive_float(kwargs, _NOTIONAL_KEYS)
    quantity = _first_positive_float(kwargs, _QUANTITY_KEYS)
    return notional, quantity


_INVALID = object()


def _limit_price(kwargs: dict) -> "float | None | object":
    """Return the largest positive limit/stop price among the present keys, ``None`` if absent, ``_INVALID`` if malformed.

    ``None``/empty values count as absent (models send nulls for optional fields); any other malformed value
    denies, like the size keys.
    """
    present = [key for key in _LIMIT_PRICE_KEYS if kwargs.get(key) not in (None, "")]
    if not present:
        return None
    price = _first_positive_float(kwargs, tuple(present))
    return _INVALID if price is None else price


def _first_positive_float(kwargs: dict, keys: tuple[str, ...]) -> float | None:
    """Return the LARGEST positive value among the present size keys, else ``None``.

    The broker may honour any of several aliases; reading only the first present one let
    ``{"notional_usd": 1, "dollar_amount": 100000}`` be sized at 1. An invalid present key denies.
    """
    values = []
    for key in keys:
        if key in kwargs:
            try:
                value = float(kwargs[key])
            except (TypeError, ValueError):
                return None
            if not math.isfinite(value) or value <= 0:
                return None
            values.append(value)
    return max(values) if values else None
