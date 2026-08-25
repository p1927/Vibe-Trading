"""TestClient coverage for `advisory_routes.py` (`/board/advisory/*`) —
2026-08-25-advisory-board-live-prediction-approve-reject-ui's Board 1 (Advisory) layer.

`test_router_is_mounted_on_the_app` mirrors `test_board_routes.py`'s own regression test for
the "router defined but never `include_router`'d" bug — worth checking again every time a new
router is added rather than assuming it won't recur.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import api_server
import trade_integrations.context.hub as hub_context
from trade_integrations.trade_widgets import store as widget_store


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    tmp = Path(tempfile.mkdtemp(prefix="advisory_routes_test_"))
    # `prediction_ledger.py` used to do `from ... import get_hub_dir` (a direct name import),
    # so the single `hub_context` patch below silently missed it — fixed at the source
    # (`prediction_ledger.py` now does `from trade_integrations.context import hub as
    # hub_context`, matching `autonomous_agents/store.py`'s pattern), so one patch is enough.
    # See .claude/backlog item 2026-08-25-prediction-ledger-get-hub-dir-not-monkeypatch-isolated.
    monkeypatch.setattr(hub_context, "get_hub_dir", lambda: tmp)
    monkeypatch.setattr(api_server, "_API_KEY", "")
    monkeypatch.delenv("AUTONOMOUS_AGENT_TRADING_WATCHLIST", raising=False)
    return TestClient(api_server.app, client=("127.0.0.1", 50000))


def test_router_is_mounted_on_the_app(client: TestClient) -> None:
    response = client.get("/board/advisory/candidates")

    assert response.status_code != 404, (
        "GET /board/advisory/candidates returned 404 — advisory_router is not mounted on the "
        "app. Check api_server.py includes `from src.api.advisory_routes import "
        "advisory_router` + `app.include_router(advisory_router)`."
    )
    assert response.status_code == 200


def test_candidates_defaults_to_nifty_watchlist_when_unset(client: TestClient) -> None:
    response = client.get("/board/advisory/candidates")

    assert response.status_code == 200
    body = response.json()
    assert list(body.keys()) == ["NIFTY"]


def test_candidates_empty_for_ticker_with_no_research_doc(client: TestClient) -> None:
    response = client.get("/board/advisory/candidates")

    assert response.status_code == 200
    entry = response.json()["NIFTY"]
    assert entry["candidates"] == []
    assert entry["confidence"]["ticker"] == "NIFTY"
    assert entry["confidence"]["confidence"] is None
    assert entry["confidence"]["is_stale"] is True


def test_candidates_uses_configured_watchlist(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTONOMOUS_AGENT_TRADING_WATCHLIST", "NIFTY,BANKNIFTY")

    response = client.get("/board/advisory/candidates")

    assert response.status_code == 200
    assert sorted(response.json().keys()) == ["BANKNIFTY", "NIFTY"]


def test_approve_builds_and_persists_a_loadable_widget(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    tmp_widgets = Path(tempfile.mkdtemp(prefix="advisory_widgets_test_"))
    monkeypatch.setattr(widget_store, "trade_widget_dir", lambda: tmp_widgets)

    recommended_orders = [{"side": "BUY", "symbol": "NIFTY31JUL25C24500", "quantity": 50}]
    bull_call_orders = [{"side": "BUY", "symbol": "NIFTY31JUL25C24700", "quantity": 50}]
    canned_widget = {
        "type": "trade_plan.widget",
        "widget_id": "tp_NIFTY_abcdef123456",
        "underlying": "NIFTY",
        "implementation_steps": [
            {"action": "execute_basket", "payload": {"orders": recommended_orders}},
        ],
        "strategy_variants": {
            "bull_call_spread": {
                "implementation_steps": [
                    {"action": "execute_basket", "payload": {"orders": bull_call_orders}},
                ],
            },
        },
    }
    monkeypatch.setattr(
        "trade_integrations.dataflows.options_research.widget_payload.build_options_trade_widget",
        lambda ticker, refresh=False: dict(canned_widget),
    )

    response = client.post("/board/advisory/approve", json={"ticker": "NIFTY"})

    assert response.status_code == 200
    body = response.json()
    assert body["widget_id"] == "tp_NIFTY_abcdef123456"
    assert body["widget"]["underlying"] == "NIFTY"
    assert body["orders"] == recommended_orders

    loaded = widget_store.load_trade_widget("tp_NIFTY_abcdef123456")
    assert loaded is not None
    assert loaded["underlying"] == "NIFTY"


def test_approve_resolves_orders_for_a_named_strategy_variant(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    tmp_widgets = Path(tempfile.mkdtemp(prefix="advisory_widgets_test_"))
    monkeypatch.setattr(widget_store, "trade_widget_dir", lambda: tmp_widgets)

    recommended_orders = [{"side": "BUY", "symbol": "NIFTY31JUL25C24500", "quantity": 50}]
    bull_call_orders = [{"side": "BUY", "symbol": "NIFTY31JUL25C24700", "quantity": 50}]
    canned_widget = {
        "type": "trade_plan.widget",
        "widget_id": "tp_NIFTY_abcdef123456",
        "underlying": "NIFTY",
        "implementation_steps": [
            {"action": "execute_basket", "payload": {"orders": recommended_orders}},
        ],
        "strategy_variants": {
            "bull_call_spread": {
                "implementation_steps": [
                    {"action": "execute_basket", "payload": {"orders": bull_call_orders}},
                ],
            },
        },
    }
    monkeypatch.setattr(
        "trade_integrations.dataflows.options_research.widget_payload.build_options_trade_widget",
        lambda ticker, refresh=False: dict(canned_widget),
    )

    response = client.post(
        "/board/advisory/approve", json={"ticker": "NIFTY", "strategy_name": "Bull Call Spread"}
    )

    assert response.status_code == 200
    assert response.json()["orders"] == bull_call_orders
