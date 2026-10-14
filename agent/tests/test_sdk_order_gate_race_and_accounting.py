"""Two gaps in the direct-SDK gate found by the R2 completion read (2026-10-14).

1. ``execute_live_order`` checked the halt flag and expiry before it priced the order and read
   positions, but not again under the lock it places the order under (``execute_live_action`` does).
2. ``execute_live_action`` let a failed daily-count write escape after the broker had accepted the
   action, so the caller retried it.
"""

from __future__ import annotations

from contextlib import nullcontext

from src.live import sdk_order_gate as gate
from src.live.daily_count import DailyCountError
from tests.test_sdk_order_gate import _FakeConnector, _intent, _mandate, _patch_gate


def _place(connector):
    return gate.execute_live_order(
        broker="alpaca-test",
        connector_module=connector,
        config=object(),
        intent=_intent(),
        place_kwargs={"symbol": "AAPL", "side": "buy", "notional": 500.0},
    )


def test_halt_tripped_while_the_order_is_being_priced_still_stops_it(monkeypatch):
    _patch_gate(monkeypatch, mandate=_mandate())
    reads = iter([False, True])  # clear at the first check, tripped by the time the lock is held
    monkeypatch.setattr(gate, "halt_flag_set", lambda broker: next(reads))
    connector = _FakeConnector()
    result = _place(connector)
    assert result["status"] == "blocked" and "halted" in result["reason"]
    assert connector.placed == []


def test_mandate_expiring_during_pricing_still_stops_the_order(monkeypatch):
    _patch_gate(monkeypatch, mandate=_mandate())
    answers = iter([False, True])  # unexpired at the first check, expired under the lock
    monkeypatch.setattr(gate, "_is_expired", lambda mandate: next(answers))
    connector = _FakeConnector()
    result = _place(connector)
    assert result["status"] == "blocked" and result["requires_reauthorization"] is True
    assert connector.placed == []


def test_unchanged_state_still_places(monkeypatch):
    _patch_gate(monkeypatch, mandate=_mandate())
    connector = _FakeConnector()
    assert _place(connector)["status"] == "ok" and len(connector.placed) == 1


def test_count_write_failure_after_a_broker_accept_does_not_raise(monkeypatch):
    _patch_gate(monkeypatch, mandate=_mandate())
    monkeypatch.setattr(gate, "daily_order_lock", lambda broker: nullcontext())

    def broken(broker, action_id=None):
        raise DailyCountError("disk full")

    monkeypatch.setattr(gate, "increment_daily_count", broken)
    result = gate.execute_live_action(
        broker="alpaca-test",
        connector_module=_FakeConnector(),
        config=object(),
        remote_tool="modify_order",
        risk_reducing=False,
        intent=_intent(),
        execute_fn=lambda: {"status": "ok", "order_id": "X1"},
    )
    assert result["status"] == "ok" and result["reason_code"] == "daily_action_accounting_failed"
