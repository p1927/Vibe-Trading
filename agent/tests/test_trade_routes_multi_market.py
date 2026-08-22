"""Tests for VibeTrading's `/trade/markets/multi_market/*` proxy routes, fronting
`StockSimulatorClient`'s multi-market-replay methods the same way
`test_trade_routes_tick_recording.py` covers the tick-recording proxy: no network,
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


_STATUS_PAYLOAD = {
    "status": "ok",
    "markets": ["IN", "US"],
    "clock": {"start_utc": "2026-08-23T00:00:00+00:00", "sim_now_utc": "2026-08-23T00:00:00+00:00",
              "speed": 1.0, "paused": False},
    "market_status": {
        "IN": {"market": "IN", "session_open": False, "local_time": "2026-08-23T05:30:00+05:30",
               "timezone": "Asia/Kolkata"},
        "US": {"market": "US", "session_open": False, "local_time": "2026-08-22T20:00:00-04:00",
               "timezone": "America/New_York"},
    },
}


def test_arm_returns_503_without_token(monkeypatch) -> None:
    monkeypatch.delenv("SIMULATOR_CONTROL_TOKEN", raising=False)
    res = _client().post("/trade/markets/multi_market/arm", json={"markets": ["IN", "US"]})
    assert res.status_code == 503
    assert "SIMULATOR_CONTROL_TOKEN" in res.json()["detail"]


def test_arm_forwards_markets_start_utc_and_speed() -> None:
    captured: dict[str, Any] = {}

    def fake_request(method, url, json=None, params=None, headers=None, timeout=None):
        captured["method"] = method
        captured["url"] = url
        captured["json"] = json
        return _FakeResponse(200, _STATUS_PAYLOAD)

    with patch("requests.request", side_effect=fake_request):
        res = _client().post(
            "/trade/markets/multi_market/arm",
            json={"markets": ["IN", "US"], "start_utc": "2026-08-23T00:00:00+00:00", "speed": 2.0},
        )

    assert res.status_code == 200
    assert captured["method"] == "POST"
    assert captured["url"].endswith("/multi_market/arm")
    assert captured["json"] == {
        "markets": ["IN", "US"], "speed": 2.0, "start_utc": "2026-08-23T00:00:00+00:00",
    }
    assert res.json()["markets"] == ["IN", "US"]


def test_arm_propagates_unsupported_market_as_400() -> None:
    def fake_request(method, url, json=None, params=None, headers=None, timeout=None):
        return _FakeResponse(400, {"detail": "unsupported market(s) ['XX']; known: ['CN', 'IN', ...]"})

    with patch("requests.request", side_effect=fake_request):
        res = _client().post("/trade/markets/multi_market/arm", json={"markets": ["XX"]})

    assert res.status_code == 400


def test_get_status() -> None:
    def fake_request(method, url, json=None, params=None, headers=None, timeout=None):
        return _FakeResponse(200, _STATUS_PAYLOAD)

    with patch("requests.request", side_effect=fake_request):
        res = _client().get("/trade/markets/multi_market/status")

    assert res.status_code == 200
    assert res.json()["market_status"]["US"]["timezone"] == "America/New_York"


def test_pause_and_resume() -> None:
    def fake_request(method, url, json=None, params=None, headers=None, timeout=None):
        return _FakeResponse(200, {**_STATUS_PAYLOAD, "clock": {**_STATUS_PAYLOAD["clock"], "paused": method == "POST"}})

    with patch("requests.request", side_effect=fake_request):
        res_pause = _client().post("/trade/markets/multi_market/pause")
        res_resume = _client().post("/trade/markets/multi_market/resume")

    assert res_pause.status_code == 200
    assert res_resume.status_code == 200


def test_seek_forwards_time() -> None:
    captured: dict[str, Any] = {}

    def fake_request(method, url, json=None, params=None, headers=None, timeout=None):
        captured["url"] = url
        captured["json"] = json
        return _FakeResponse(200, _STATUS_PAYLOAD)

    with patch("requests.request", side_effect=fake_request):
        res = _client().post("/trade/markets/multi_market/seek", json={"time": "2026-08-21T09:30:00+00:00"})

    assert res.status_code == 200
    assert captured["url"].endswith("/multi_market/seek")
    assert captured["json"] == {"time": "2026-08-21T09:30:00+00:00"}


def test_set_speed_forwards_speed() -> None:
    captured: dict[str, Any] = {}

    def fake_request(method, url, json=None, params=None, headers=None, timeout=None):
        captured["json"] = json
        return _FakeResponse(200, _STATUS_PAYLOAD)

    with patch("requests.request", side_effect=fake_request):
        res = _client().post("/trade/markets/multi_market/speed", json={"speed": 5.0})

    assert res.status_code == 200
    assert captured["json"] == {"speed": 5.0}


def test_stop() -> None:
    def fake_request(method, url, json=None, params=None, headers=None, timeout=None):
        return _FakeResponse(200, {"status": "ok", "message": "multi-market session stopped"})

    with patch("requests.request", side_effect=fake_request):
        res = _client().post("/trade/markets/multi_market/stop")

    assert res.status_code == 200


def test_get_quote_forwards_query_params() -> None:
    captured: dict[str, Any] = {}

    def fake_request(method, url, json=None, params=None, headers=None, timeout=None):
        captured["url"] = url
        captured["params"] = params
        return _FakeResponse(
            200,
            {"status": "ok", "data": {"market": "US", "symbol": "SPX", "exchange": "US_INDEX",
                                       "price": 5000.0, "ts": "2026-08-23T00:00:00+00:00",
                                       "stale": False, "source": "tick_recorder"}},
        )

    with patch("requests.request", side_effect=fake_request):
        res = _client().get(
            "/trade/markets/multi_market/quote",
            params={"market": "US", "symbol": "SPX", "exchange": "US_INDEX"},
        )

    assert res.status_code == 200
    assert captured["url"].endswith("/multi_market/quote")
    assert captured["params"] == {"market": "US", "symbol": "SPX", "exchange": "US_INDEX"}
    assert res.json()["data"]["price"] == 5000.0


def test_get_quote_propagates_no_data_as_400() -> None:
    def fake_request(method, url, json=None, params=None, headers=None, timeout=None):
        return _FakeResponse(400, {"detail": "no tick data for US/SPX/US_INDEX at ... — no tick_recorder job has produced data in this window yet"})

    with patch("requests.request", side_effect=fake_request):
        res = _client().get(
            "/trade/markets/multi_market/quote",
            params={"market": "US", "symbol": "SPX", "exchange": "US_INDEX"},
        )

    assert res.status_code == 400
