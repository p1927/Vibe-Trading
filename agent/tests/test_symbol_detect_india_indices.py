"""Fork-only: symbol_detect.py must ask Trade's entity registry for the India
index set (Trade D30) instead of hand-keeping its own ``_IN_INDICES`` literal.

See .claude/backlog/items/2026-09-17-vibetrading-symbol-detect-fold.md.
"""

from __future__ import annotations

import ast
from pathlib import Path

from src.trade import symbol_detect


def test_no_hand_kept_india_index_frozenset_literal() -> None:
    """Catches a re-introduced ``_IN_INDICES``-style hand-kept literal: no module-level
    frozenset of India index strings may exist in this file — it must come from the
    ``instrument_identity`` sidecar (which asks Trade's entity registry) instead."""
    source = Path(symbol_detect.__file__).read_text()
    tree = ast.parse(source)

    india_markers = {"NIFTY", "BANKNIFTY", "SENSEX"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
            call = node.value
            is_frozenset_call = isinstance(call.func, ast.Name) and call.func.id == "frozenset"
            if not is_frozenset_call:
                continue
            literal_strings = {
                n.value
                for n in ast.walk(call)
                if isinstance(n, ast.Constant) and isinstance(n.value, str)
            }
            assert not (india_markers & literal_strings), (
                "symbol_detect.py must not hand-keep its own India-index frozenset literal "
                "(D30) - route through instrument_identity.india_index_tickers() instead"
            )


def test_extract_primary_ticker_uses_registry_index_set(monkeypatch) -> None:
    """extract_primary_ticker() recognizes an index only via the sidecar's set, proving
    the detection path is wired to instrument_identity rather than a local literal."""
    monkeypatch.setattr(
        symbol_detect, "_india_index_tickers", lambda: frozenset({"FAKEINDEX"})
    )

    assert symbol_detect.extract_primary_ticker("what about FAKEINDEX today") == "FAKEINDEX"
    assert symbol_detect.infer_asset_type("", "FAKEINDEX") == "options"


def test_listing_error_inside_trade_propagates(monkeypatch) -> None:
    """Inside Trade a listing failure is a bug: it raises instead of returning every
    capitalised word as a ticker (Trade D36; 2026-09-24-vibe-symbol-sector-loaders-swallow-errors)."""
    import pytest

    def broken(_symbol: str) -> bool:
        raise KeyError("registry row missing 'exchange'")

    monkeypatch.setattr(
        "trade_integrations.dataflows.company_research.india_symbols.is_india_listed_symbol", broken
    )
    with pytest.raises(KeyError):
        symbol_detect._filter_india_listed(["RELIANCE", "WHAT"])


def test_standalone_without_trade_keeps_candidates(monkeypatch) -> None:
    """Standalone vibe-trading has no listing, so every candidate is kept."""
    monkeypatch.setattr("src.trade.hub_bridge.trade_repo_root", lambda: None)
    assert symbol_detect._filter_india_listed(["RELIANCE", "WHAT"]) == ["RELIANCE", "WHAT"]
