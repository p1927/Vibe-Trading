"""Tests for `/trade/hub/constituents/panel`, which fronts
`StockSimulatorClient().get_constituents_history()` — migrated off direct
`StockHistory().load_constituents_history()` per
.claude/backlog/items/2026-08-23-india-dedicated-methods-retirement.md, Tier 4. Same
no-network, `requests.request`-stubbed pattern as `test_trade_routes_markets.py`.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import api_server


def _client() -> TestClient:
    return TestClient(api_server.app, client=("127.0.0.1", 50000))


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict[str, Any]) -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = str(payload)

    def json(self) -> dict[str, Any]:
        return self._payload


@pytest.fixture(autouse=True)
def _token(monkeypatch):
    monkeypatch.setenv("SIMULATOR_CONTROL_TOKEN", "test-shared-secret")
    monkeypatch.setenv("STOCK_SIMULATOR_URL", "http://127.0.0.1:8902")
    yield


def test_hub_constituents_panel_forwards_start_end_and_returns_rows() -> None:
    rows = [
        {"date": "2024-05-01", "symbol": "RELIANCE", "close": 1316.0},
        {"date": "2024-05-01", "symbol": "TCS", "close": 3800.0},
    ]
    captured: dict[str, Any] = {}

    def fake_request(method, url, json=None, params=None, headers=None, timeout=None):
        captured["method"] = method
        captured["url"] = url
        captured["params"] = params
        return _FakeResponse(200, {"status": "ok", "data": rows})

    with patch("requests.request", side_effect=fake_request):
        res = _client().get("/trade/hub/constituents/panel", params={"start": "2024-05-01", "end": "2024-05-02"})

    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "ok"
    assert body["rows"] == rows
    assert captured["method"] == "GET"
    assert captured["url"].endswith("/history/constituents")
    assert captured["params"] == {"start": "2024-05-01", "end": "2024-05-02"}


def test_hub_constituents_panel_respects_limit() -> None:
    rows = [{"date": "2024-05-01", "symbol": f"SYM{i}", "close": float(i)} for i in range(5)]

    def fake_request(method, url, json=None, params=None, headers=None, timeout=None):
        return _FakeResponse(200, {"status": "ok", "data": rows})

    with patch("requests.request", side_effect=fake_request):
        res = _client().get("/trade/hub/constituents/panel", params={"limit": 2})

    assert res.status_code == 200
    assert len(res.json()["rows"]) == 2


def test_hub_constituents_panel_degrades_to_error_status_on_client_failure() -> None:
    def fake_request(method, url, json=None, params=None, headers=None, timeout=None):
        return _FakeResponse(503, {"detail": "stock_simulator unreachable"})

    with patch("requests.request", side_effect=fake_request):
        res = _client().get("/trade/hub/constituents/panel")

    assert res.status_code == 200  # the route itself always 200s, error surfaces in the body
    body = res.json()
    assert body["status"] == "error"
    assert body["rows"] == []
    assert "stock_simulator unreachable" in body["error"]
