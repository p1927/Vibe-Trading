"""End to end: no order reaches any connector except through the one live gate.

``test_trading_service_write_gate`` pins that the connector config can never disagree with the profile. This file
drives ``trading.service`` (the only door the tools and routes use) into the REAL gate, with the real kill-switch
sentinel files, the real audit ledger and the real daily-order lock under an isolated runtime root; only the broker
connector is a recorder. Per case, what is asserted is what the connector heard:

* a live profile with no mandate, an expired mandate, the global kill switch or one broker's kill switch: nothing;
* a kill switch tripped while the gate is pricing the order (after its first check): nothing;
* a read-only profile (including OpenAlgo's live-readonly one) through EVERY write entry point of the service: nothing;
* the control: a live profile with a valid mandate and no halt places exactly one order, marked live and audited.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

import src.live.paths as live_paths
from src.live import audit as live_audit
from src.live import halt as live_halt
from src.live import sdk_order_gate as gate
from src.trading import profiles, service
from tests.test_sdk_order_gate import _mandate

pytestmark = pytest.mark.unit

WRITE_CALLS = {"place_order", "cancel_order", "close_position", "cancel_close_order", "edit_position_stops",
               "start_copy", "close_copy", "copy_start", "copy_close"}
ORDER_PROFILES = [p for p in profiles.BUILTIN_PROFILES if p.transport == "broker_sdk" and not p.readonly]
LIVE = [p for p in ORDER_PROFILES if p.environment == "live"]
READONLY = [p for p in profiles.BUILTIN_PROFILES if p.transport == "broker_sdk" and p.readonly]
ALPACA_LIVE = next(p for p in LIVE if p.connector == "alpaca")


def _ids(items):
    return [p.id for p in items]


class _Connector:
    """A connector that records every call; reads answer with an empty book, writes with an acknowledged order."""

    def __init__(self, real, *, on_read=None):
        self.real = real
        self.calls: list[tuple[str, object]] = []
        self.on_read = on_read

    def build_config(self, profile_config, overrides):
        return self.real.build_config(profile_config, overrides)

    def get_positions(self, config):
        if self.on_read:
            self.on_read()
        return {"status": "ok", "positions": []}

    def get_account_snapshot(self, config):
        return {"status": "ok", "account": {}}

    def get_quote(self, symbol, *, config=None):
        return {"status": "ok", "symbol": symbol, "quote": {"last": 100.0}}

    def get_order_by_client_order_id(self, config, *, client_order_id):
        return {"status": "error", "error": "not found"}

    def __getattr__(self, name):
        if name.startswith("_") or name in {"real", "calls", "on_read"}:
            raise AttributeError(name)

        def call(config=None, *args, **kwargs):
            self.calls.append((name, config))
            return {"status": "ok", "order_id": "OID-E2E", "echo": name}

        return call

    def writes(self):
        return [(name, cfg) for name, cfg in self.calls if name in WRITE_CALLS]


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    """The live runtime (kill-switch sentinels, audit ledger, daily-order lock, pending markers) under tmp_path."""
    monkeypatch.setattr(live_paths, "get_runtime_root", lambda: tmp_path)
    return tmp_path


def _connect(monkeypatch, profile, **kw):
    connector = _Connector(service._sdk_module(profile.connector), **kw)
    monkeypatch.setattr(service, "_sdk_module", lambda name: connector)
    return connector


def _mandate_on_file(monkeypatch, mandate):
    monkeypatch.setattr(gate, "load_mandate", lambda broker: mandate)


def _expired():
    m = _mandate()
    return SimpleNamespace(**{**m.__dict__, "consent": SimpleNamespace(**{**m.consent.__dict__, "expires_at": "2000-01-01T00:00:00+00:00"})})


def _order(profile, **kw):
    return service.place_order("AAPL", profile.id, side="buy", notional=10.0, **kw)


def _audited(runtime) -> list[dict]:
    path = live_audit.audit_ledger_path()
    return [json.loads(line) for line in path.read_text().splitlines()] if path.is_file() else []


@pytest.mark.parametrize("profile", LIVE, ids=_ids(LIVE))
def test_no_mandate_on_file_nothing_reaches_the_connector(monkeypatch, runtime, profile) -> None:
    connector = _connect(monkeypatch, profile)
    _mandate_on_file(monkeypatch, None)
    out = _order(profile)
    assert out["status"] == "blocked" and out["decision"] == "deny", out
    assert connector.writes() == []


@pytest.mark.parametrize("profile", LIVE, ids=_ids(LIVE))
def test_expired_mandate_nothing_reaches_the_connector(monkeypatch, runtime, profile) -> None:
    connector = _connect(monkeypatch, profile)
    _mandate_on_file(monkeypatch, _expired())
    out = _order(profile)
    assert out["status"] == "blocked", out
    assert connector.writes() == []


@pytest.mark.parametrize("profile", LIVE, ids=_ids(LIVE))
def test_global_kill_switch_stops_every_live_profile_even_with_a_valid_mandate(monkeypatch, runtime, profile) -> None:
    connector = _connect(monkeypatch, profile)
    _mandate_on_file(monkeypatch, _mandate())
    live_halt.trip_halt(by="file", reason="e2e global halt")
    out = _order(profile)
    assert out["status"] == "blocked" and "halt" in out["reason"].lower(), out
    assert connector.writes() == []
    assert any(row for row in _audited(runtime)), "a refused live order is still audited"


def test_one_brokers_kill_switch_stops_that_broker_only(monkeypatch, runtime) -> None:
    other = next(p for p in LIVE if p.connector != "alpaca")
    live_halt.trip_halt(by="file", reason="e2e alpaca halt", broker="alpaca")
    connector = _connect(monkeypatch, ALPACA_LIVE)
    _mandate_on_file(monkeypatch, _mandate())
    assert _order(ALPACA_LIVE)["status"] == "blocked"
    assert connector.writes() == []
    assert live_halt.halt_flag_set(other.connector) is False  # the halt did not spread to another broker


def test_a_kill_switch_tripped_while_the_gate_prices_the_order_still_stops_it(monkeypatch, runtime) -> None:
    """The gate checks the halt, then reads the book and balance (seconds), then checks again under its lock."""
    connector = _connect(monkeypatch, ALPACA_LIVE, on_read=lambda: live_halt.trip_halt(by="file", reason="e2e race"))
    _mandate_on_file(monkeypatch, _mandate())
    out = _order(ALPACA_LIVE)
    assert out["status"] == "blocked" and "halt" in out["reason"].lower(), out
    assert connector.writes() == []


def test_control_a_valid_mandate_and_no_halt_places_exactly_one_audited_live_order(monkeypatch, runtime) -> None:
    connector = _connect(monkeypatch, ALPACA_LIVE)
    _mandate_on_file(monkeypatch, _mandate())
    out = _order(ALPACA_LIVE)
    assert out["status"] == "ok" and "live_action" in out, out
    [(name, config)] = connector.writes()
    assert name == "place_order" and getattr(config, "environment", "live") == "live"
    assert len(_audited(runtime)) >= 1


@pytest.mark.parametrize("profile", READONLY, ids=_ids(READONLY))
def test_a_readonly_profile_reaches_no_connector_write_through_any_service_entry_point(monkeypatch, runtime, profile) -> None:
    """OpenAlgo's live-readonly profile reads the real book: not one write call may be made through it."""
    connector = _connect(monkeypatch, profile)
    _mandate_on_file(monkeypatch, _mandate())
    entry_points = [
        lambda: service.place_order("AAPL", profile.id, side="buy", quantity=1),
        lambda: service.cancel_order("1", profile.id, symbol="AAPL"),
        lambda: service.close_position("1", profile.id),
        lambda: service.cancel_close_order("1", profile.id),
        lambda: service.edit_position_stops("1", profile.id, stop_loss=1.0),
        lambda: service.etoro_copy_start(1, 10.0, profile.id, reference_id="r"),
        lambda: service.etoro_copy_close(1, profile.id),
    ]
    for call in entry_points:
        out = call()
        assert out["status"] == "error", out
    assert connector.writes() == []


def test_the_openalgo_live_readonly_profile_exists_and_is_live_and_readonly() -> None:
    """The case this file's readonly test is for must actually be in the registry (a rename would empty it silently)."""
    profile = profiles.profile_by_id("openalgo-live-sdk-readonly")
    assert profile.environment == "live" and profile.readonly and profile in READONLY
