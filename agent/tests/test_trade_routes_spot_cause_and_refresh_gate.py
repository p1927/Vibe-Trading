"""Two read-side honesty fixes on the prediction / simulator pages:

* `GET /trade/hub/market-data/spot` with no quote names the cause the simulator's /health knows
  (capture waiting for credentials, or the simulator down) instead of a bare message the tile showed as Loading.
* `GET /trade/index-prediction/refresh-gate` is the POST's own skip rule, readable without writing, so the page
  can skip the POST when it would no-op.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api import security, trade_refresh_routes, trade_routes
from trade_integrations.stock_simulator.client import StockSimulatorClient, StockSimulatorClientError


@pytest.fixture
def client(monkeypatch):
    app = FastAPI()
    app.include_router(trade_routes.trade_router)
    app.dependency_overrides[security.require_local_or_auth] = lambda: None
    from trade_integrations.dataflows import stock_history_bridge

    monkeypatch.setattr(stock_history_bridge, "get_live_spot_quote", lambda **kw: None)
    return TestClient(app)


def _spot(client) -> dict:
    res = client.get("/trade/hub/market-data/spot?symbol=NIFTY")
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "error"
    return body


@pytest.mark.unit
def test_spot_error_names_a_capture_waiting_for_credentials(client, monkeypatch):
    monkeypatch.setattr(StockSimulatorClient, "health_check", lambda self: {"capture_waiting_for_credentials": ["IN"]})
    error = _spot(client)["error"]
    assert "no spot quote available" in error and "IN capture is waiting for credentials" in error


@pytest.mark.unit
def test_spot_error_names_an_unreachable_simulator(client, monkeypatch):
    def _down(self):
        raise StockSimulatorClientError("connection refused")

    monkeypatch.setattr(StockSimulatorClient, "health_check", _down)
    assert "stock_simulator unreachable: connection refused" in _spot(client)["error"]


@pytest.mark.unit
def test_spot_error_stays_plain_when_the_simulator_reports_no_cause(client, monkeypatch):
    monkeypatch.setattr(StockSimulatorClient, "health_check", lambda self: {"capture_waiting_for_credentials": []})
    assert _spot(client)["error"] == "no spot quote available (Timescale empty and OpenAlgo unreachable)"


@pytest.mark.unit
@pytest.mark.parametrize("reason", [None, "market_closed", "replay_armed", "simulator_unavailable"])
def test_refresh_gate_reports_the_same_rule_the_post_applies(client, monkeypatch, reason):
    monkeypatch.setattr(trade_refresh_routes, "_light_refresh_skip_reason", lambda key: reason)
    gate = client.get("/trade/index-prediction/refresh-gate?ticker=NIFTY").json()
    assert gate == {"ticker": "NIFTY", "skip_reason": reason}
    if reason:
        post = client.post("/trade/index-prediction/refresh", json={"ticker": "NIFTY"}).json()
        assert post["status"] == "skipped" and post["reason"] == reason
