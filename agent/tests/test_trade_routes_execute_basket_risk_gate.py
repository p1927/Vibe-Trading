"""`/trade/execute-basket` is gated (D453): no order reaches the broker unless it passes the one risk gate.

The gate itself is real here; only its inputs (halt state, committed capital, daily loss, margin,
lot size, market session) and the broker POST are faked. The broker POST is the thing counted:
every refusal test asserts it was never called.
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import HTTPException

from src.api.trade_routes import ExecuteBasketRequest, execute_basket

WIDGET_ID = "tp_NIFTY_abc123def456"
ORDER = {"symbol": "NIFTY25OCT25000CE", "exchange": "NFO", "action": "BUY", "quantity": 75, "product": "NRML"}


def _widget(agent_id: str | None = None) -> dict:
    return {
        "widget_id": WIDGET_ID,
        "underlying": "NIFTY",
        "recommended": {"name": "long_call", "legs": [{"symbol": ORDER["symbol"], "quantity": 75}]},
        "autonomous_agent_id": agent_id,
        "implementation_steps": [],
    }


class _Env:
    """Mutable fakes for the gate's inputs; each test sets what it needs."""

    def __init__(self) -> None:
        self.halted = False
        self.committed = 0.0
        self.open_positions = 0
        self.daily_loss = 0.0
        self.margin: float | None = 5_000.0
        self.margin_error: Exception | None = None
        self.lot = 75
        self.session_block: str | None = None
        self.agent_record: dict | None = None
        self.bridge_agent = False
        self.posts: list[tuple[str, dict]] = []
        self.ledger_agent_ids: list = []
        self.analyze = True
        self.now = datetime(2025, 10, 10, 5, 0, tzinfo=timezone.utc)


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch):
    e = _Env()
    monkeypatch.setenv("OPENALGO_API_KEY", "test-openalgo-key")
    monkeypatch.setenv("OPENALGO_PAPER_MODE", "true")
    for name in (
        "MANUAL_OPERATOR_BUDGET_INR",
        "MANUAL_OPERATOR_MAX_DAILY_LOSS_INR",
        "MANUAL_OPERATOR_MAX_LOTS",
        "MANUAL_OPERATOR_MAX_OPEN_POSITIONS",
    ):
        monkeypatch.delenv(name, raising=False)

    from nautilus_openalgo_bridge.openalgo_client import BridgeOpenAlgoClient
    from trade_integrations.risk import capital, daily_pnl, gate

    monkeypatch.setattr("nautilus_openalgo_bridge.risk_state.is_trading_halted", lambda agent_id: e.halted)
    monkeypatch.setattr("nautilus_openalgo_bridge.risk_state.halt_reason", lambda agent_id: "kill switch")
    monkeypatch.setattr("nautilus_openalgo_bridge.risk_state.risk_poll_failing", lambda agent_id: None)
    monkeypatch.setattr(capital, "committed_capital_inr", lambda agent_id: e.committed)
    monkeypatch.setattr(capital, "open_position_count", lambda agent_id: e.open_positions)
    monkeypatch.setattr(daily_pnl, "daily_running_loss_inr", lambda agent_id: e.daily_loss)
    monkeypatch.setattr(
        "trade_integrations.autonomous_agents.replay_clock.current_utc", lambda market="IN", **_: e.now
    )
    monkeypatch.setattr(gate, "assert_research_fresh", lambda widget_id, *, market: None)
    monkeypatch.setattr(
        "nautilus_openalgo_bridge.market_hours.market_session_block_reason", lambda market, now=None: e.session_block
    )
    monkeypatch.setattr(
        "nautilus_openalgo_bridge.market_hours.agent_session_block_reason", lambda agent_id, now=None: e.session_block
    )

    def _margin(self, positions):
        if e.margin_error:
            raise e.margin_error
        return e.margin

    monkeypatch.setattr(BridgeOpenAlgoClient, "calculate_margin", _margin)
    monkeypatch.setattr(BridgeOpenAlgoClient, "lot_size", lambda self, symbol, exchange: e.lot)
    monkeypatch.setattr("trade_integrations.autonomous_agents.store.get_agent", lambda agent_id: e.agent_record)
    monkeypatch.setattr(
        "trade_integrations.execution.enforce.is_bridge_autonomous_agent", lambda agent_id: e.bridge_agent
    )
    return e


def _run(env: _Env, *, widget: dict | None, orders: list[dict] | None, widget_id: str | None = WIDGET_ID):
    def _post(endpoint, payload, timeout=45):
        env.posts.append((endpoint, payload))
        return {"status": "success", "results": [{"orderid": "1", "status": "success"}]}

    def _record(w, results, *, execution_mode, agent_id=None, **kw):
        env.ledger_agent_ids.append(agent_id)
        return {"execution_id": "ex_1"}

    with (
        patch("src.api.trade_execution_routes.load_trade_widget", return_value=widget),
        patch("trade_integrations.execution.context_verify.ensure_paper_execution_ready",
              return_value=SimpleNamespace(analyze_mode=env.analyze)),
        patch("trade_integrations.openalgo.market_context.fetch_market_context",
              return_value=SimpleNamespace(analyze_mode=env.analyze)),
        patch("trade_integrations.openalgo.rest_client.get_rest_client", return_value=SimpleNamespace(post=_post)),
        patch("trade_integrations.monitor.execution_ledger.record_execution_from_widget", side_effect=_record),
        patch("trade_integrations.autonomous_agents.outcome_ledger.append_outcome"),
        patch("trade_integrations.autonomous_agents.shadow_strategy.record_shadow_alignment"),
        patch("trade_integrations.monitor.execution_ledger.reconcile_underlying"),
    ):
        request = ExecuteBasketRequest(widget_id=widget_id, orders=orders or [])
        return execute_basket(request, _auth=None)


def _basket_posts(env: _Env) -> list:
    return [p for p in env.posts if p[0] == "basketorder"]


def _refused(env: _Env, status: int, *, widget="default", orders="default", widget_id: str | None = WIDGET_ID, code=None):
    widget = _widget() if widget == "default" else widget
    orders = [ORDER] if orders == "default" else orders
    with pytest.raises(HTTPException) as exc:
        _run(env, widget=widget, orders=orders, widget_id=widget_id)
    assert exc.value.status_code == status, exc.value.detail
    if code:
        assert code in str(exc.value.detail)
    assert _basket_posts(env) == [], "an order reached the broker past a refusal"


@pytest.mark.unit
def test_permitted_manual_order_is_placed_and_booked_under_the_manual_operator(env):
    response = _run(env, widget=_widget(), orders=[ORDER])
    assert response.status == "success" and len(_basket_posts(env)) == 1
    assert env.ledger_agent_ids == ["manual_operator"]


@pytest.mark.unit
def test_no_widget_id_is_refused(env):
    _refused(env, 400, widget=None, widget_id=None)


@pytest.mark.unit
def test_unknown_widget_with_free_form_orders_is_refused(env):
    _refused(env, 404, widget=None)


@pytest.mark.unit
def test_bridge_agent_widget_is_refused(env):
    env.bridge_agent = True
    _refused(env, 403, widget=_widget("ag_1"))


@pytest.mark.unit
def test_foreign_agent_widget_without_a_record_is_refused(env):
    _refused(env, 403, widget=_widget("ag_ghost"))


@pytest.mark.unit
def test_foreign_agent_widget_is_judged_by_that_agents_own_mandate(env):
    env.agent_record = {"id": "ag_2", "status": "running", "execution_market": "IN", "constraints": {"max_lots": 0}}
    env.margin = 50_000.0  # over that agent's default 20k budget, below nothing else
    _refused(env, 403, widget=_widget("ag_2"), code="budget_exceeded")


@pytest.mark.unit
def test_foreign_agent_widget_within_its_mandate_is_placed_under_that_agent(env):
    env.agent_record = {"id": "ag_2", "status": "running", "execution_market": "IN", "constraints": {}}
    _run(env, widget=_widget("ag_2"), orders=[ORDER])
    assert len(_basket_posts(env)) == 1 and env.ledger_agent_ids == ["ag_2"]


@pytest.mark.unit
def test_over_lot_cap_is_refused(env):
    _refused(env, 403, orders=[{**ORDER, "quantity": 75 * 3}], code="max_lots_exceeded")


@pytest.mark.unit
def test_non_whole_lot_quantity_is_refused(env):
    _refused(env, 403, orders=[{**ORDER, "quantity": 80}], code="quantity_not_lot_multiple")


@pytest.mark.unit
def test_over_budget_is_refused(env):
    env.margin = 25_000.0
    _refused(env, 403, code="budget_exceeded")


@pytest.mark.unit
def test_committed_capital_counts_against_the_budget(env):
    env.committed = 18_000.0
    env.margin = 5_000.0
    _refused(env, 403, code="budget_exceeded")


@pytest.mark.unit
def test_budget_comes_from_config(env, monkeypatch):
    env.margin = 25_000.0
    monkeypatch.setenv("MANUAL_OPERATOR_BUDGET_INR", "30000")
    assert _run(env, widget=_widget(), orders=[ORDER]).status == "success"


@pytest.mark.unit
def test_halted_is_refused(env):
    env.halted = True
    _refused(env, 403, code="trading_halted")


@pytest.mark.unit
def test_daily_loss_reached_is_refused(env):
    env.daily_loss = 2_000.0
    _refused(env, 403, code="max_daily_loss_exceeded")


@pytest.mark.unit
def test_max_open_positions_is_refused(env):
    env.open_positions = 2
    _refused(env, 403, code="max_positions")


@pytest.mark.unit
def test_market_closed_is_refused(env):
    env.session_block = "outside_market_hours"
    _refused(env, 403, code="outside_market_hours")


@pytest.mark.unit
@pytest.mark.parametrize("error", [RuntimeError("margin down"), None])
def test_unreadable_margin_is_refused(env, error):
    env.margin_error, env.margin = error, None
    _refused(env, 503)


@pytest.mark.unit
def test_unreadable_halt_state_is_refused(env, monkeypatch):
    from nautilus_openalgo_bridge.risk_state import HaltStateUnavailable

    def _down(agent_id):
        raise HaltStateUnavailable("redis down")

    monkeypatch.setattr("nautilus_openalgo_bridge.risk_state.is_trading_halted", _down)
    _refused(env, 403, code="halt_state_unavailable")


@pytest.mark.unit
@pytest.mark.parametrize("sandbox", [True, False])
def test_sandbox_and_live_both_pass_the_gate(env, monkeypatch, sandbox):
    env.analyze = sandbox
    if not sandbox:
        monkeypatch.setenv("OPENALGO_PAPER_MODE", "false")
    response = _run(env, widget=_widget(), orders=[ORDER])
    assert response.execution_mode == ("paper" if sandbox else "live")
    assert len(_basket_posts(env)) == 1


@pytest.mark.unit
@pytest.mark.parametrize("sandbox", [True, False])
def test_over_limit_is_refused_in_both_modes(env, monkeypatch, sandbox):
    env.analyze = sandbox
    env.halted = True
    if not sandbox:
        monkeypatch.setenv("OPENALGO_PAPER_MODE", "false")
    _refused(env, 403, code="trading_halted")


@pytest.mark.unit
def test_live_order_with_paper_lock_on_is_still_refused(env):
    env.analyze = False  # the paper lock env stays "true"
    _refused(env, 403)


@pytest.mark.unit
def test_expired_contract_is_refused(env):
    env.now = datetime(2026, 1, 5, 5, 0, tzinfo=timezone.utc)
    _refused(env, 403, code="contract_expired")


@pytest.mark.unit
@pytest.mark.parametrize("quantity", [0, -75, "abc", None, float("nan")])
def test_unreadable_or_non_positive_quantity_is_refused(env, quantity):
    _refused(env, 400, orders=[{**ORDER, "quantity": quantity}])


@pytest.mark.unit
def test_one_bad_leg_in_a_basket_refuses_the_whole_basket(env):
    _refused(env, 400, orders=[ORDER, {**ORDER, "symbol": "", "quantity": 75}])
