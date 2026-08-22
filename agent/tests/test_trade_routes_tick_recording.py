"""Tests for VibeTrading's `/trade/markets/recording/*` proxy routes, fronting
`StockSimulatorClient.start_tick_recording`/`stop_tick_recording`/`list_tick_recordings` the same
way `test_trade_routes_markets.py` covers the read-only `/trade/markets/*` routes: no network,
`requests.request` stubbed.
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


def test_start_recording_returns_503_without_token(monkeypatch) -> None:
    monkeypatch.delenv("SIMULATOR_CONTROL_TOKEN", raising=False)
    res = _client().post("/trade/markets/recording/start", json={"kind": "fx", "interval_seconds": 30})
    assert res.status_code == 503
    assert "SIMULATOR_CONTROL_TOKEN" in res.json()["detail"]


def test_start_recording_forwards_fx_kind_and_interval() -> None:
    captured: dict[str, Any] = {}

    def fake_request(method, url, json=None, params=None, headers=None, timeout=None):
        captured["method"] = method
        captured["url"] = url
        captured["json"] = json
        return _FakeResponse(
            200,
            {"status": "ok", "job": {"job_id": "abc123", "kind": "fx", "country": None,
                                      "symbols": ["usd_inr"], "interval_seconds": 30.0,
                                      "started_at": "2026-08-23T00:00:00+00:00",
                                      "polls": 0, "errors": 0, "last_error": None}},
        )

    with patch("requests.request", side_effect=fake_request):
        res = _client().post(
            "/trade/markets/recording/start",
            json={"kind": "fx", "symbols": ["usd_inr"], "interval_seconds": 30},
        )

    assert res.status_code == 200
    assert captured["method"] == "POST"
    assert captured["url"].endswith("/tick_recording/start")
    assert captured["json"] == {"kind": "fx", "interval_seconds": 30, "symbols": ["usd_inr"]}
    assert res.json()["job"]["job_id"] == "abc123"


def test_start_recording_forwards_index_kind_and_country() -> None:
    captured: dict[str, Any] = {}

    def fake_request(method, url, json=None, params=None, headers=None, timeout=None):
        captured["json"] = json
        return _FakeResponse(200, {"status": "ok", "job": {"job_id": "xyz789"}})

    with patch("requests.request", side_effect=fake_request):
        res = _client().post(
            "/trade/markets/recording/start",
            json={"kind": "index", "country": "US", "interval_seconds": 30},
        )

    assert res.status_code == 200
    assert captured["json"] == {"kind": "index", "interval_seconds": 30, "country": "US"}


def test_start_recording_propagates_validation_error_as_400() -> None:
    def fake_request(method, url, json=None, params=None, headers=None, timeout=None):
        return _FakeResponse(400, {"detail": "interval_seconds must be >= 5.0"})

    with patch("requests.request", side_effect=fake_request):
        res = _client().post("/trade/markets/recording/start", json={"kind": "fx", "interval_seconds": 1})

    assert res.status_code == 400


def test_stop_recording_forwards_job_id() -> None:
    captured: dict[str, Any] = {}

    def fake_request(method, url, json=None, params=None, headers=None, timeout=None):
        captured["method"] = method
        captured["url"] = url
        return _FakeResponse(200, {"status": "ok", "job_id": "abc123"})

    with patch("requests.request", side_effect=fake_request):
        res = _client().post("/trade/markets/recording/abc123/stop")

    assert res.status_code == 200
    assert captured["method"] == "POST"
    assert captured["url"].endswith("/tick_recording/abc123/stop")


def test_stop_recording_propagates_unknown_job_as_404() -> None:
    def fake_request(method, url, json=None, params=None, headers=None, timeout=None):
        return _FakeResponse(404, {"detail": "no active tick-recording job 'nope'"})

    with patch("requests.request", side_effect=fake_request):
        res = _client().post("/trade/markets/recording/nope/stop")

    assert res.status_code == 404


def test_list_active_recordings() -> None:
    def fake_request(method, url, json=None, params=None, headers=None, timeout=None):
        return _FakeResponse(200, {"status": "ok", "jobs": [{"job_id": "abc123", "kind": "fx"}]})

    with patch("requests.request", side_effect=fake_request):
        res = _client().get("/trade/markets/recording/active")

    assert res.status_code == 200
    assert res.json()["jobs"][0]["job_id"] == "abc123"
