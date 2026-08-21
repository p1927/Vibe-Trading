"""Tests for VibeTrading's replay-control routes, now thin clients of the
standalone stock_simulator service instead of proxying through OpenAlgo.

Covers the start_replay call's end_date forwarding (range replay), the
fail-closed 503 when SIMULATOR_CONTROL_TOKEN isn't configured, and error
propagation. No network: `StockSimulatorClient`'s underlying `requests.request`
call is stubbed, matching the loopback `TestClient` convention in
`test_alpha_compare_api.py`.
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


def test_start_replay_returns_503_without_token(monkeypatch) -> None:
    monkeypatch.delenv("SIMULATOR_CONTROL_TOKEN", raising=False)
    res = _client().post("/trade/recording/2024-04-15/replay", json={})
    assert res.status_code == 503
    assert "SIMULATOR_CONTROL_TOKEN" in res.json()["detail"]


def test_start_replay_forwards_end_date_speed_and_loop() -> None:
    captured: dict[str, Any] = {}

    def fake_request(method, url, json=None, params=None, headers=None, timeout=None):
        captured["method"] = method
        captured["url"] = url
        captured["json"] = json
        captured["headers"] = headers
        return _FakeResponse(200, {"mode": "replay", "clock": {"replay_date": "2024-04-15"}})

    with patch("requests.request", side_effect=fake_request):
        res = _client().post(
            "/trade/recording/2024-04-15/replay",
            json={"end_date": "2024-04-19", "speed": 10, "loop": True},
        )

    assert res.status_code == 200
    assert captured["method"] == "POST"
    assert captured["url"].endswith("/control/replay/start")
    assert captured["json"] == {
        "date": "2024-04-15",
        "end_date": "2024-04-19",
        "speed": 10,
        "loop": True,
    }
    assert captured["headers"] == {"X-Simulator-Control-Token": "test-shared-secret"}


def test_start_replay_omits_end_date_when_not_given() -> None:
    captured: dict[str, Any] = {}

    def fake_request(method, url, json=None, params=None, headers=None, timeout=None):
        captured["json"] = json
        return _FakeResponse(200, {"mode": "replay"})

    with patch("requests.request", side_effect=fake_request):
        res = _client().post("/trade/recording/2024-04-15/replay", json={})

    assert res.status_code == 200
    assert "end_date" not in captured["json"]


def test_start_replay_propagates_service_error() -> None:
    def fake_request(method, url, json=None, params=None, headers=None, timeout=None):
        return _FakeResponse(400, {"detail": "date must be YYYY-MM-DD"})

    with patch("requests.request", side_effect=fake_request):
        res = _client().post("/trade/recording/not-a-date/replay", json={})

    assert res.status_code == 400


def test_seek_replay_returns_503_without_token(monkeypatch) -> None:
    monkeypatch.delenv("SIMULATOR_CONTROL_TOKEN", raising=False)
    res = _client().post("/trade/recording/replay/seek", json={"time": "11:30"})
    assert res.status_code == 503
    assert "SIMULATOR_CONTROL_TOKEN" in res.json()["detail"]


def test_seek_replay_forwards_time_to_service() -> None:
    captured: dict[str, Any] = {}

    def fake_request(method, url, json=None, params=None, headers=None, timeout=None):
        captured["url"] = url
        captured["json"] = json
        return _FakeResponse(200, {"clock": {"sim_now": "2024-04-15T11:30:00+05:30"}})

    with patch("requests.request", side_effect=fake_request):
        res = _client().post("/trade/recording/replay/seek", json={"time": "11:30"})

    assert res.status_code == 200
    assert captured["url"].endswith("/control/replay/seek")
    assert captured["json"] == {"time": "11:30"}
    assert res.json()["replay"]["clock"]["sim_now"] == "2024-04-15T11:30:00+05:30"


def test_seek_replay_propagates_service_error() -> None:
    def fake_request(method, url, json=None, params=None, headers=None, timeout=None):
        return _FakeResponse(400, {"detail": "time must be HH:MM[:SS]"})

    with patch("requests.request", side_effect=fake_request):
        res = _client().post("/trade/recording/replay/seek", json={"time": "not-a-time"})

    assert res.status_code == 400


def test_replay_status_reads_directly_from_the_service() -> None:
    """No more env mirroring: VibeTrading talks to the one shared clock
    directly, so a status poll just returns whatever the service reports —
    there's no local `ReplayService` to hard-sync any more."""

    def fake_request(method, url, json=None, params=None, headers=None, timeout=None):
        return _FakeResponse(
            200,
            {
                "mode": "replay",
                "week_dates": ["2024-04-15", "2024-04-16"],
                "clock": {
                    "replay_date": "2024-04-15",
                    "sim_now": "2024-04-15T11:30:00+05:30",
                    "speed": 2.0,
                    "loop": False,
                },
            },
        )

    with patch("requests.request", side_effect=fake_request):
        res = _client().get("/trade/recording/replay/status")

    assert res.status_code == 200
    assert res.json()["replay"]["clock"]["replay_date"] == "2024-04-15"


def test_replay_calendar_reports_unconfigured_without_token(monkeypatch) -> None:
    monkeypatch.delenv("SIMULATOR_CONTROL_TOKEN", raising=False)
    res = _client().get("/trade/recording/replay/calendar")
    assert res.status_code == 200
    body = res.json()
    assert body["configured"] is False
    assert body["days"] == []
