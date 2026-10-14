"""Central input guards in trading.service: ids never reach URL paths, numbers are finite,
account/credential/host overrides are pinned (connector review, 2026-10-14)."""

from __future__ import annotations

import pytest

from src.live.enforcement import OrderIntent  # noqa: F401  (import check)
from src.live.mandate.model import InstrumentType
from src.trading import profiles, service
from src.trading.connectors.robinhood.extractor import extract_order_intent

pytestmark = pytest.mark.unit

PAPER = [p for p in profiles.BUILTIN_PROFILES if p.transport == "broker_sdk" and not p.readonly
         and p.environment == "paper"]


class _Rec:
    def __init__(self, real):
        self.real, self.calls = real, []

    def build_config(self, profile_config, overrides):
        self.calls.append(("build", dict(overrides)))
        return self.real.build_config(profile_config, overrides)

    def place_order(self, config, **kw):
        self.calls.append(("place", kw))
        return {"status": "ok"}

    def cancel_order(self, config, order_id, symbol=None):
        self.calls.append(("cancel", order_id))
        return {"status": "ok"}


@pytest.mark.parametrize("profile", PAPER, ids=lambda p: p.id)
@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -1.0, 0, "x"])
def test_non_finite_or_non_positive_numbers_never_reach_a_connector(monkeypatch, profile, bad) -> None:
    rec = _Rec(service._sdk_module(profile.connector))
    monkeypatch.setattr(service, "_sdk_module", lambda c: rec)
    for field in ("quantity", "notional", "limit_price"):
        out = service.place_order("AAPL", profile.id, side="buy", order_type="limit", **{field: bad})
        assert out["status"] == "error", (profile.id, field, bad)
    assert not [c for c in rec.calls if c[0] == "place"]


@pytest.mark.parametrize("profile", PAPER, ids=lambda p: p.id)
@pytest.mark.parametrize("bad", ["../../orders/1", "a/b", "..", "1 2", "", "x" * 80])
def test_ids_that_could_walk_a_url_path_are_refused(monkeypatch, profile, bad) -> None:
    rec = _Rec(service._sdk_module(profile.connector))
    monkeypatch.setattr(service, "_sdk_module", lambda c: rec)
    assert service.cancel_order(bad, profile.id)["status"] == "error"
    assert not [c for c in rec.calls if c[0] == "cancel"]


def test_etoro_path_ids_are_refused() -> None:
    etoro = next(p for p in profiles.BUILTIN_PROFILES if p.connector == "etoro")
    for fn in (service.close_position, service.cancel_close_order, service.edit_position_stops):
        assert fn("../../positions/1", etoro.id)["status"] == "error"


def test_plain_ids_still_pass(monkeypatch) -> None:
    profile = PAPER[0]
    rec = _Rec(service._sdk_module(profile.connector))
    monkeypatch.setattr(service, "_sdk_module", lambda c: rec)
    assert service.cancel_order("123e4567-e89b-12d3-a456-426614174000", profile.id)["status"] == "ok"


@pytest.mark.parametrize("connector,key", [("tiger", "account"), ("tiger", "tiger_id"), ("mt5", "login"),
                                           ("mt5", "password"), ("mt5", "server"), ("mt5", "terminal_path"),
                                           ("futu", "port")])
def test_pinned_override_keys_are_dropped(connector, key) -> None:
    profile = next(p for p in profiles.BUILTIN_PROFILES if p.connector == connector and not p.readonly)
    seen = {}

    def build(profile_config, overrides):
        seen.update(overrides)
        return type("C", (), {"environment": profile.environment})()

    service._sdk_config(profile, type("M", (), {"build_config": staticmethod(build)}), {key: "evil", "symbol": "ok"})
    assert key not in seen and seen.get("symbol") == "ok"


def test_futu_host_override_is_loopback_only() -> None:
    profile = next(p for p in profiles.BUILTIN_PROFILES if p.connector == "futu" and not p.readonly)
    for host, kept in (("evil.example", False), ("127.0.0.1", True), ("localhost", True)):
        seen = {}

        def build(profile_config, overrides):
            seen.update(overrides)
            return type("C", (), {"environment": profile.environment})()

        service._sdk_config(profile, type("M", (), {"build_config": staticmethod(build)}), {"host": host})
        assert ("host" in seen) is kept, host


def test_robinhood_size_keys_disagreeing_are_sized_at_the_largest() -> None:
    intent = extract_order_intent(
        "place_equity_order",
        {"symbol": "AAPL", "side": "buy", "instrument_type": "equity", "notional_usd": 1, "dollar_amount": 100000},
    )
    assert intent is not None and intent.notional_usd == 100000.0
    bad = extract_order_intent(
        "place_equity_order",
        {"symbol": "AAPL", "side": "buy", "instrument_type": "equity", "notional_usd": 1, "dollar_amount": "nan"},
    )
    assert bad is None  # an invalid alias denies the notional path; with no quantity the order is denied
    assert InstrumentType.EQUITY
