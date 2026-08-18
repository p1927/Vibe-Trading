"""Tests for the VibeTrading -> OpenAlgo replay-control proxy routes.

Covers the start_replay proxy's end_date forwarding (range replay) and the
fail-closed 503 when OPENALGO_SIMULATOR_CONTROL_TOKEN isn't configured.
No network: ``requests.post``/``.get`` are stubbed, matching the loopback
``TestClient`` convention in ``test_alpha_compare_api.py``.
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
    monkeypatch.setenv("OPENALGO_SIMULATOR_CONTROL_TOKEN", "test-shared-secret")
    yield


def test_start_replay_returns_503_without_token(monkeypatch) -> None:
    monkeypatch.delenv("OPENALGO_SIMULATOR_CONTROL_TOKEN", raising=False)
    res = _client().post("/trade/recording/2024-04-15/replay", json={})
    assert res.status_code == 503
    assert "OPENALGO_SIMULATOR_CONTROL_TOKEN" in res.json()["detail"]


def test_start_replay_forwards_end_date_speed_and_loop() -> None:
    captured: dict[str, Any] = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        captured["url"] = url
        captured["json"] = json
        captured["headers"] = headers
        return _FakeResponse(200, {"mode": "replay", "clock": {"replay_date": "2024-04-15"}})

    with patch("requests.post", side_effect=fake_post):
        res = _client().post(
            "/trade/recording/2024-04-15/replay",
            json={"end_date": "2024-04-19", "speed": 10, "loop": True},
        )

    assert res.status_code == 200
    assert captured["url"].endswith("/stock_simulator/control/replay/start")
    assert captured["json"] == {
        "date": "2024-04-15",
        "end_date": "2024-04-19",
        "speed": 10,
        "loop": True,
    }
    assert captured["headers"] == {"X-Simulator-Control-Token": "test-shared-secret"}


def test_start_replay_omits_end_date_when_not_given() -> None:
    captured: dict[str, Any] = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        captured["json"] = json
        return _FakeResponse(200, {"mode": "replay"})

    with patch("requests.post", side_effect=fake_post):
        res = _client().post("/trade/recording/2024-04-15/replay", json={})

    assert res.status_code == 200
    assert "end_date" not in captured["json"]


def test_start_replay_propagates_openalgo_error() -> None:
    def fake_post(url, json=None, headers=None, timeout=None):
        return _FakeResponse(400, {"status": "error", "message": "date must be YYYY-MM-DD"})

    with patch("requests.post", side_effect=fake_post):
        res = _client().post("/trade/recording/not-a-date/replay", json={})

    assert res.status_code == 502


def test_seek_replay_returns_503_without_token(monkeypatch) -> None:
    monkeypatch.delenv("OPENALGO_SIMULATOR_CONTROL_TOKEN", raising=False)
    res = _client().post("/trade/recording/replay/seek", json={"time": "11:30"})
    assert res.status_code == 503
    assert "OPENALGO_SIMULATOR_CONTROL_TOKEN" in res.json()["detail"]


def test_seek_replay_forwards_time_to_openalgo() -> None:
    captured: dict[str, Any] = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        captured["url"] = url
        captured["json"] = json
        return _FakeResponse(200, {"clock": {"sim_now": "2024-04-15T11:30:00+05:30"}})

    with patch("requests.post", side_effect=fake_post):
        res = _client().post("/trade/recording/replay/seek", json={"time": "11:30"})

    assert res.status_code == 200
    assert captured["url"].endswith("/stock_simulator/control/replay/seek")
    assert captured["json"] == {"time": "11:30"}
    assert res.json()["replay"]["clock"]["sim_now"] == "2024-04-15T11:30:00+05:30"


def test_seek_replay_propagates_openalgo_error() -> None:
    def fake_post(url, json=None, headers=None, timeout=None):
        return _FakeResponse(400, {"status": "error", "message": "time must be HH:MM[:SS]"})

    with patch("requests.post", side_effect=fake_post):
        res = _client().post("/trade/recording/replay/seek", json={"time": "not-a-time"})

    assert res.status_code == 502


def test_replay_status_mirrors_env_when_process_missed_the_arm(monkeypatch) -> None:
    """A VibeTrading process that never witnessed the original arm (restarted,
    or armed from another tab) must still pick up OpenAlgo's replay state on
    the next status poll — otherwise the chart/option-chain endpoints, which
    read STOCK_SIMULATOR_MODE/NSE_REPLAY_* from *this* process, stay dark even
    though the sim clock is visibly running."""
    monkeypatch.delenv("STOCK_SIMULATOR_MODE", raising=False)
    monkeypatch.delenv("NSE_REPLAY_DATE", raising=False)
    monkeypatch.delenv("NSE_REPLAY_END_DATE", raising=False)

    def fake_get(url, headers=None, timeout=None):
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

    with patch("requests.get", side_effect=fake_get):
        res = _client().get("/trade/recording/replay/status")

    assert res.status_code == 200
    import os

    assert os.environ["STOCK_SIMULATOR_MODE"] == "replay"
    assert os.environ["NSE_REPLAY_DATE"] == "2024-04-15"
    assert os.environ["NSE_REPLAY_END_DATE"] == "2024-04-16"
    assert os.environ["NSE_REPLAY_SPEED"] == "2.0"
    assert os.environ["NSE_REPLAY_LOOP"] == "0"


def test_replay_status_clears_env_when_openalgo_reports_not_armed(monkeypatch) -> None:
    monkeypatch.setenv("STOCK_SIMULATOR_MODE", "replay")

    def fake_get(url, headers=None, timeout=None):
        return _FakeResponse(200, {"mode": "", "clock": {}})

    with patch("requests.get", side_effect=fake_get):
        res = _client().get("/trade/recording/replay/status")

    assert res.status_code == 200
    import os

    assert "STOCK_SIMULATOR_MODE" not in os.environ
