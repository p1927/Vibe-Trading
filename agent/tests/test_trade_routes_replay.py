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
