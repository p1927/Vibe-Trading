"""vibe-api's release tier-root guard (backlog 2026-09-16-vibe-api-openalgo-assert-tier-roots)."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.trade import tier_roots_guard
from src.trade.tier_roots_guard import assert_release_tier_roots


def test_dev_profile_is_a_noop(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("STACK_PROFILE", "dev")
    monkeypatch.setenv("TRADE_STACK_ROOT", str(tmp_path))  # wrong on purpose; dev never checks
    assert_release_tier_roots()


def test_release_profile_with_foreign_root_refuses_to_boot(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from trade_integrations.stack_env_sync import TierRootMisrouted

    monkeypatch.setenv("STACK_PROFILE", "release")
    monkeypatch.setenv("TRADE_STACK_ROOT", str(tmp_path))
    monkeypatch.setenv("TRADE_STACK_HUB_DIR", str(tmp_path / "reports" / "hub"))
    with pytest.raises(TierRootMisrouted):
        assert_release_tier_roots()


def test_release_profile_with_root_containing_this_checkout_passes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = Path(tier_roots_guard.__file__).resolve().parents[4]  # the checkout holding vibetrading/
    monkeypatch.setenv("STACK_PROFILE", "release")
    monkeypatch.setenv("TRADE_STACK_ROOT", str(root))
    monkeypatch.setenv("TRADE_STACK_HUB_DIR", str(root / "reports" / "hub"))
    monkeypatch.setenv("NSE_REPLAY_DATA_ROOT", str(root / "reports" / "hub" / "nse_replay"))
    assert_release_tier_roots()
