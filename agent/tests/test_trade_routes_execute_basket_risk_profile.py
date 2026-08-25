"""Tests for `execute_basket` writing module 5's max_loss/max_profit to OpenAlgo's
StrategyRiskProfile ledger — see
.claude/backlog/items/2026-08-26-selector-not-writing-strategy-risk-profile.md.

No network: same mocked-rest-client style as test_trade_routes_execute_basket_outcome.py.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from src.api.trade_routes import ExecuteBasketRequest, execute_basket


def _selector_widget(*, max_loss=-2291.25, max_profit=4208.75) -> dict:
    return {
        "underlying": "NIFTY",
        "recommended": {
            "name": "bear_put_spread",
            "max_loss": max_loss,
            "max_profit": max_profit,
        },
        "autonomous_agent_id": None,
        "implementation_steps": [],
    }


@pytest.fixture(autouse=True)
def _paper_mode_config(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("OPENALGO_API_KEY", "test-openalgo-key")
    monkeypatch.setenv("OPENALGO_PAPER_MODE", "true")
    yield


def _run(widget: dict, *, strategy: str = "bear_put_spread"):
    request = ExecuteBasketRequest(
        widget_id="tp_NIFTY_abc123def456",
        orders=[{"symbol": "NIFTY", "action": "BUY", "quantity": 50}],
        strategy=strategy,
    )
    calls: list[tuple[str, dict]] = []

    def fake_post(endpoint, payload, timeout=45):
        calls.append((endpoint, payload))
        return {"status": "success", "results": [{"orderid": "1", "status": "success"}]}

    fake_rest_client = SimpleNamespace(post=fake_post)

    with (
        patch("src.api.trade_routes.load_trade_widget", return_value=widget),
        patch(
            "trade_integrations.execution.context_verify.ensure_paper_execution_ready",
            return_value=SimpleNamespace(analyze_mode=True),
        ),
        patch("trade_integrations.openalgo.rest_client.get_rest_client", return_value=fake_rest_client),
        patch(
            "trade_integrations.monitor.execution_ledger.record_execution_from_widget",
            return_value={"execution_id": "ex_NIFTY_freshexecid"},
        ),
        patch("trade_integrations.autonomous_agents.outcome_ledger.append_outcome"),
        patch("trade_integrations.monitor.execution_ledger.reconcile_underlying"),
        patch("src.trade.hub_bridge.ensure_trade_stack_path"),
    ):
        response = execute_basket(request, _auth=None)
        return response, calls


@pytest.mark.unit
def test_writes_risk_profile_after_order_placed():
    response, calls = _run(_selector_widget())

    assert response.status == "success"
    riskprofile_calls = [c for c in calls if c[0] == "riskprofile"]
    assert len(riskprofile_calls) == 1
    _, payload = riskprofile_calls[0]
    assert payload["strategy"] == "bear_put_spread"
    assert payload["max_risk"] == pytest.approx(2291.25)
    assert payload["max_profit"] == pytest.approx(4208.75)


@pytest.mark.unit
def test_skips_risk_profile_when_widget_has_no_max_loss():
    widget = _selector_widget()
    widget["recommended"] = {"name": "manual_leg"}
    response, calls = _run(widget)

    assert response.status == "success"
    assert [c for c in calls if c[0] == "riskprofile"] == []


@pytest.mark.unit
def test_riskprofile_write_failure_does_not_fail_execution():
    request = ExecuteBasketRequest(
        widget_id="tp_NIFTY_abc123def456",
        orders=[{"symbol": "NIFTY", "action": "BUY", "quantity": 50}],
        strategy="bear_put_spread",
    )

    def fake_post(endpoint, payload, timeout=45):
        if endpoint == "riskprofile":
            raise RuntimeError("boom")
        return {"status": "success", "results": [{"orderid": "1", "status": "success"}]}

    fake_rest_client = SimpleNamespace(post=fake_post)

    with (
        patch("src.api.trade_routes.load_trade_widget", return_value=_selector_widget()),
        patch(
            "trade_integrations.execution.context_verify.ensure_paper_execution_ready",
            return_value=SimpleNamespace(analyze_mode=True),
        ),
        patch("trade_integrations.openalgo.rest_client.get_rest_client", return_value=fake_rest_client),
        patch(
            "trade_integrations.monitor.execution_ledger.record_execution_from_widget",
            return_value={"execution_id": "ex_NIFTY_freshexecid"},
        ),
        patch("trade_integrations.autonomous_agents.outcome_ledger.append_outcome"),
        patch("trade_integrations.monitor.execution_ledger.reconcile_underlying"),
        patch("src.trade.hub_bridge.ensure_trade_stack_path"),
    ):
        response = execute_basket(request, _auth=None)

    assert response.status == "success"
