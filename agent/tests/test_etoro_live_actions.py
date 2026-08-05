"""Live action gate tests for eToro-specific writes."""

from __future__ import annotations

from contextlib import nullcontext

import pytest

from src.live import sdk_order_gate as gate
from src.live.enforcement import OrderIntent
from src.live.mandate.model import AssetClass, ConsentMeta, HardCaps, InstrumentType, Mandate, UniverseConstraint
from src.trading import service

pytestmark = pytest.mark.unit


class _FakeEtoroModule:
    def __init__(self):
        self.calls: list[str] = []

    def close_position(self, config, **kwargs):
        self.calls.append("close")
        return {"status": "ok", "position_id": kwargs.get("position_id")}

    def copy_close(self, config, **kwargs):
        self.calls.append("copy_close")
        return {"status": "ok", "mirror_id": kwargs.get("mirror_id")}

    def get_positions(self, config):
        return {"status": "ok", "positions": []}

    def get_account_snapshot(self, config):
        return {"status": "ok", "account": {}}


def _mandate():
    return Mandate(
        schema_version=1,
        hard_caps=HardCaps(
            account_funding_usd=1_000_000.0,
            max_order_notional_usd=1_000_000.0,
            max_total_exposure_usd=1_000_000.0,
            max_leverage=2.0,
            allowed_instruments=(InstrumentType.EQUITY,),
            max_trades_per_day=100,
        ),
        universe=UniverseConstraint(
            asset_classes=(AssetClass.US_EQUITY,),
            min_market_cap_usd=None,
            min_avg_daily_volume_usd=None,
            exclude_symbols=(),
        ),
        consent=ConsentMeta(
            created_at="2026-01-01T00:00:00+00:00",
            consent_token_sha256="deadbeef",
            broker="etoro",
            account_ref="acct-1",
            expires_at="2999-01-01T00:00:00+00:00",
        ),
    )


def _patch_gate(monkeypatch, *, mandate, halted=False):
    monkeypatch.setattr(gate, "load_mandate", lambda broker: mandate)
    monkeypatch.setattr(gate, "halt_flag_set", lambda broker: halted)
    monkeypatch.setattr(gate, "write_live_action", lambda *a, **k: {"audited": True})
    monkeypatch.setattr(gate, "read_daily_count", lambda broker: 0)
    monkeypatch.setattr(gate, "increment_daily_count", lambda broker: 1)
    monkeypatch.setattr(gate, "daily_order_lock", lambda broker: nullcontext())


def test_risk_reducing_close_allowed_when_halted(monkeypatch) -> None:
    module = _FakeEtoroModule()
    _patch_gate(monkeypatch, mandate=_mandate(), halted=True)
    result = gate.execute_live_action(
        broker="etoro",
        connector_module=module,
        config=None,
        remote_tool="close_position",
        risk_reducing=True,
        intent=None,
        execute_fn=lambda: module.close_position(None, position_id="123"),
        audit_request={"position_id": "123"},
    )
    assert result["status"] == "ok"
    assert module.calls == ["close"]


def test_risk_increasing_edit_requires_mandate_when_halted(monkeypatch) -> None:
    module = _FakeEtoroModule()
    _patch_gate(monkeypatch, mandate=_mandate(), halted=True)
    intent = OrderIntent(
        symbol="POS:1",
        side="sell",
        notional_usd=0.0,
        quantity=None,
        instrument_type=InstrumentType.EQUITY,
        asset_class=AssetClass.US_EQUITY,
    )
    result = gate.execute_live_action(
        broker="etoro",
        connector_module=module,
        config=None,
        remote_tool="edit_position_stops",
        risk_reducing=False,
        intent=intent,
        execute_fn=lambda: {"status": "ok"},
        audit_request={"position_id": "1"},
    )
    assert result["status"] == "blocked"


def test_service_close_position_paper_bypasses_gate(monkeypatch) -> None:
    module = _FakeEtoroModule()
    module.build_config = lambda profile_config, overrides: None  # type: ignore[method-assign]
    monkeypatch.setattr(service, "_sdk_module", lambda connector: module)
    result = service.close_position("99", "etoro-paper-trade")
    assert result["status"] == "ok"
    assert module.calls == ["close"]
