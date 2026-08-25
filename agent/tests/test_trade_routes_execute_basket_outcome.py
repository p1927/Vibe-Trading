"""Tests for `execute_basket`'s manual outcome_ledger tagging.

Before this, a manual order placed via the chat trade-plan widget (no autonomous
agent involved) never wrote an ENTER row to
`trade_integrations.autonomous_agents.outcome_ledger` at all — see
2026-08-25-manual-recommendation-to-order-path-audit. These tests cover the fix:
a genuinely human-placed widget order now gets tagged `intent_source="manual_ui"`,
while a widget already attributed to an agent is left untouched (a known,
pre-existing gap for non-bridge agents, not newly introduced here).

No network: OpenAlgo's REST client and the execution-ledger/outcome-ledger side
effects are all mocked, matching `test_trade_routes_markets.py`'s no-network style.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

import api_server
from api.trade_routes import ExecuteBasketRequest, execute_basket


def _widget(*, agent_id: str | None = None) -> dict:
    return {
        "underlying": "NIFTY",
        "recommended": {"name": "long_call"},
        "autonomous_agent_id": agent_id,
        "implementation_steps": [],
    }


@pytest.fixture(autouse=True)
def _paper_mode_config(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("OPENALGO_API_KEY", "test-openalgo-key")
    monkeypatch.setenv("OPENALGO_PAPER_MODE", "true")
    yield


def _run_execute_basket(widget: dict, *, orders: list[dict] | None = None):
    request = ExecuteBasketRequest(widget_id="tp_NIFTY_abc123def456", orders=orders or [])

    fake_rest_client = SimpleNamespace(
        post=lambda endpoint, payload, timeout=45: {
            "status": "success",
            "results": [{"orderid": "1", "status": "success"}],
        }
    )

    with (
        patch("api.trade_routes.load_trade_widget", return_value=widget),
        patch(
            "trade_integrations.execution.context_verify.ensure_paper_execution_ready",
            return_value=SimpleNamespace(analyze_mode=True),
        ),
        patch("trade_integrations.openalgo.rest_client.get_rest_client", return_value=fake_rest_client),
        patch("trade_integrations.monitor.execution_ledger.record_execution_from_widget"),
        patch("trade_integrations.autonomous_agents.outcome_ledger.append_outcome") as mock_append,
        patch("src.trade.hub_bridge.ensure_trade_stack_path"),
    ):
        if not orders:
            # execute_basket only re-derives orders from the widget's
            # implementation_steps when body.orders is empty; give it one so
            # the "no orders to execute" guard doesn't short-circuit before
            # reaching the outcome-ledger tagging logic under test.
            request = ExecuteBasketRequest(
                widget_id="tp_NIFTY_abc123def456",
                orders=[{"symbol": "NIFTY", "action": "BUY", "quantity": 50}],
            )
        response = execute_basket(request, _auth=None)
        return response, mock_append


@pytest.mark.unit
def test_manual_widget_order_gets_tagged_manual_ui():
    response, mock_append = _run_execute_basket(_widget(agent_id=None))

    assert response.status == "success"
    mock_append.assert_called_once()
    _, kwargs = mock_append.call_args
    assert kwargs["symbol"] == "NIFTY"
    assert kwargs["strategy"] == "long_call"
    assert kwargs["action"] == "ENTER"
    assert kwargs["intent_source"] == "manual_ui"
    assert kwargs["widget_id"] == "tp_NIFTY_abc123def456"


@pytest.mark.unit
def test_agent_attributed_widget_is_left_untouched():
    """A widget already carrying an agent_id is not the manual path this fix
    covers — must not be mistagged as manual_ui."""
    response, mock_append = _run_execute_basket(_widget(agent_id="agent-1"))

    assert response.status == "success"
    mock_append.assert_not_called()


@pytest.mark.unit
def test_widget_with_no_underlying_is_not_tagged():
    widget = _widget(agent_id=None)
    widget["underlying"] = None
    response, mock_append = _run_execute_basket(widget)

    assert response.status == "success"
    mock_append.assert_not_called()
