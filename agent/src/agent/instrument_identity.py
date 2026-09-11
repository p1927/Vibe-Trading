"""Fork-only: ask Trade's entity registry whether two spellings name one instrument.

Trade's entity registry is the single owner of instrument spellings (Trade
``docs/DECISIONS.md`` D30): ``NIFTY``, ``NIFTY50`` and ``^NSEI`` are one index,
and ``NIFTY`` is the stored canonical name. The locked-identity guard in
``grounding.py`` consults this module before rejecting a consumer symbol, so it
no longer demands ``^NSEI`` for an instrument Trade will store as ``NIFTY``.
This module carries no alias table of its own — that would be a second
spelling authority, which D30 forbids.

Sidecar per Trade ``docs/FORK_CONVENTIONS.md``: ``grounding.py`` (an upstream
file) holds one import and one call into it.

Run standalone (vibetrading outside the Trade monorepo) there is no registry:
``ensure_trade_stack_path`` raises ``RuntimeError`` and every lookup answers
``None``, which leaves the guard's own exact and venue rules as the only
matchers — the pre-D30 behaviour. Once the Trade stack is found, an import
failure inside it is a real bug and propagates.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Callable, Iterable

SameInstrument = Callable[[str, str], bool]


@lru_cache(maxsize=1)
def _registry_same_instrument() -> SameInstrument | None:
    from src.trade.hub_bridge import ensure_trade_stack_path

    try:
        ensure_trade_stack_path()
    except RuntimeError:
        return None
    from trade_integrations.dataflows.entity_registry import same_instrument

    return same_instrument


def registry_equivalent_symbol(requested: str, authorized: Iterable[str]) -> str | None:
    """Return the one locked symbol Trade's registry says ``requested`` names.

    Args:
        requested: Normalized consumer symbol (e.g. ``NIFTY``).
        authorized: Normalized locked symbols (e.g. ``{"^NSEI"}``).

    Returns:
        The unique locked symbol that is the same instrument, or ``None`` when
        none or several are, or when no registry is available.
    """
    same = _registry_same_instrument()
    if same is None:
        return None
    matches = [symbol for symbol in authorized if same(requested, symbol)]
    return matches[0] if len(matches) == 1 else None
