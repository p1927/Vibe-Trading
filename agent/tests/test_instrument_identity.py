"""Fork-only: the locked-identity guard defers to Trade's entity registry (Trade D28)."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.agent import instrument_identity
from src.agent.grounding import GroundingLedger


def _authorize(tmp_path: Path, user_message: str, symbols: list[str]):
    ledger = GroundingLedger(run_dir=tmp_path, user_message=user_message)
    return ledger, ledger.authorize_tool_call(
        "get_fundamentals",
        {"symbols": symbols},
        batch_authorized_symbols=ledger.authorized_symbols,
        call_id="consumer",
    )


def test_nifty_is_accepted_against_a_locked_nsei(tmp_path: Path) -> None:
    """The observed failure: ``^NSEI`` locked, ``NIFTY`` rejected, then stored as ``NIFTY``."""
    ledger, authorization = _authorize(tmp_path, "paper trade ^NSEI", ["NIFTY"])

    assert ledger.authorized_symbols == {"^NSEI"}
    assert authorization.allowed is True


def test_a_different_index_is_still_rejected(tmp_path: Path) -> None:
    _, authorization = _authorize(tmp_path, "paper trade ^BSESN", ["NIFTY"])

    assert authorization.allowed is False
    assert authorization.error_code == "identity_mismatch"


def test_without_the_trade_registry_the_guard_keeps_its_own_rules(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(instrument_identity, "_registry_same_instrument", lambda: None)

    _, authorization = _authorize(tmp_path, "paper trade ^NSEI", ["NIFTY"])

    assert authorization.allowed is False
    assert authorization.error_code == "identity_mismatch"


def test_registry_answer_must_be_unique() -> None:
    assert instrument_identity.registry_equivalent_symbol("NIFTY", {"^NSEI", "NIFTY50"}) is None
    assert instrument_identity.registry_equivalent_symbol("NIFTY", {"^NSEI", "AAPL.US"}) == "^NSEI"
