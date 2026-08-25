"""Tests for `execute_basket`'s outcome_ledger tagging.

Before this, a manual order placed via the chat trade-plan widget (no autonomous
agent involved) never wrote an ENTER row to
`trade_integrations.autonomous_agents.outcome_ledger` at all — see
2026-08-25-manual-recommendation-to-order-path-audit. A genuinely human-placed
widget order is tagged `intent_source="manual_ui"`.

A widget already attributed to a (non-bridge; bridge agents are 403'd earlier in
this route) agent's `agent_id` is tagged `intent_source="vibe_basket"` with that
`agent_id` instead of being left completely untagged — see
2026-08-25-non-bridge-agent-widget-execute-basket-untagged.

No network: OpenAlgo's REST client and the execution-ledger/outcome-ledger side
effects are all mocked, matching `test_trade_routes_markets.py`'s no-network style.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

import api_server
from src.api.trade_routes import ExecuteBasketRequest, execute_basket


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
        patch("trade_integrations.autonomous_agents.outcome_ledger.append_outcome") as mock_append,
        patch("trade_integrations.monitor.execution_ledger.reconcile_underlying") as mock_reconcile,
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
        return response, mock_append, mock_reconcile


@pytest.mark.unit
def test_manual_widget_order_gets_tagged_manual_ui():
    response, mock_append, _ = _run_execute_basket(_widget(agent_id=None))

    assert response.status == "success"
    mock_append.assert_called_once()
    _, kwargs = mock_append.call_args
    assert kwargs["symbol"] == "NIFTY"
    assert kwargs["strategy"] == "long_call"
    assert kwargs["action"] == "ENTER"
    assert kwargs["intent_source"] == "manual_ui"
    assert kwargs["widget_id"] == "tp_NIFTY_abc123def456"


@pytest.mark.unit
def test_agent_attributed_widget_gets_tagged_vibe_basket():
    """A widget carrying a (non-bridge) agent_id is not the manual path — must
    not be mistagged as manual_ui — but must still get an ENTER row, tagged
    the same as the dedicated MCP execution path's agent-placed ENTERs, not
    left completely untagged."""
    response, mock_append, _ = _run_execute_basket(_widget(agent_id="agent-1"))

    assert response.status == "success"
    mock_append.assert_called_once()
    _, kwargs = mock_append.call_args
    assert kwargs["symbol"] == "NIFTY"
    assert kwargs["strategy"] == "long_call"
    assert kwargs["action"] == "ENTER"
    assert kwargs["intent_source"] == "vibe_basket"
    assert kwargs["agent_id"] == "agent-1"
    assert kwargs["widget_id"] == "tp_NIFTY_abc123def456"


@pytest.mark.unit
def test_widget_with_no_underlying_is_not_tagged():
    widget = _widget(agent_id=None)
    widget["underlying"] = None
    response, mock_append, _ = _run_execute_basket(widget)

    assert response.status == "success"
    mock_append.assert_not_called()


@pytest.mark.unit
class TestReconciliationAfterExecute:
    """2026-08-25-manual-widget-close-reconciliation-depends-on-agent-tick:
    `execute_basket` now reconciles the traded underlying's open ledger
    entries itself, so a manually-executed widget's close is no longer stuck
    waiting for an autonomous agent's own status check or review tick (which
    may never fire if zero agents are running)."""

    def test_reconciles_the_traded_underlying_excluding_the_fresh_entry(self):
        response, _, mock_reconcile = _run_execute_basket(_widget(agent_id=None))

        assert response.status == "success"
        mock_reconcile.assert_called_once_with("NIFTY", exclude_execution_id="ex_NIFTY_freshexecid")

    def test_still_reconciles_for_an_agent_attributed_widget(self):
        """Reconciliation isn't gated on the manual/agent distinction — it's
        about the underlying's real position state, which matters either way."""
        response, _, mock_reconcile = _run_execute_basket(_widget(agent_id="agent-1"))

        assert response.status == "success"
        mock_reconcile.assert_called_once_with("NIFTY", exclude_execution_id="ex_NIFTY_freshexecid")

    def test_skips_reconciliation_when_widget_has_no_underlying(self):
        widget = _widget(agent_id=None)
        widget["underlying"] = None
        response, _, mock_reconcile = _run_execute_basket(widget)

        assert response.status == "success"
        mock_reconcile.assert_not_called()
