"""TestClient coverage for `channels_routes.py` (`/channels/*`) — previously untested.

Domain: `2026-08-21-agent-api-route-coverage-audit`. Confirmed correctly mounted via
`register_channels_routes(app)` in `api_server.py`. The runtime start/status/stop endpoints are
stubbed (constructing a real `ChannelRuntime` would spin up real IM adapters); the pairing
command endpoint drives the real `handle_pairing_command` against an isolated tmp data dir.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

import api_server


class _FakeChannelRuntime:
    def __init__(self) -> None:
        self.started = False
        self.stopped = False

    def status(self) -> dict[str, Any]:
        return {"running": self.started and not self.stopped, "adapters": []}

    async def start(self, *, start_manager: bool = True) -> None:
        self.started = True

    async def stop(self) -> None:
        self.stopped = True


@pytest.fixture
def fake_runtime() -> _FakeChannelRuntime:
    return _FakeChannelRuntime()


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch, fake_runtime: _FakeChannelRuntime) -> TestClient:
    tmp = Path(tempfile.mkdtemp(prefix="channels_routes_test_"))
    monkeypatch.setenv("VIBE_TRADING_HOME", str(tmp))
    monkeypatch.setattr(api_server, "_get_channel_runtime", lambda: fake_runtime)
    monkeypatch.setattr(api_server, "_API_KEY", "")
    return TestClient(api_server.app, client=("127.0.0.1", 50000))


def test_channels_status_returns_runtime_status(client: TestClient) -> None:
    response = client.get("/channels/status")
    assert response.status_code == 200
    assert response.json() == {"running": False, "adapters": []}


def test_channels_start_starts_runtime_and_reports_status(
    client: TestClient, fake_runtime: _FakeChannelRuntime
) -> None:
    response = client.post("/channels/start")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "started"
    assert body["running"] is True
    assert fake_runtime.started is True


def test_channels_stop_stops_runtime_and_reports_status(
    client: TestClient, fake_runtime: _FakeChannelRuntime
) -> None:
    response = client.post("/channels/stop")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "stopped"
    assert fake_runtime.stopped is True


def test_pairing_command_list_with_no_pending_requests(client: TestClient) -> None:
    response = client.post(
        "/channels/pairing/command", json={"channel": "telegram", "command": "list"}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["channel"] == "telegram"
    assert body["reply"] == "No pending pairing requests."


def test_pairing_command_approve_without_code_returns_usage(client: TestClient) -> None:
    response = client.post(
        "/channels/pairing/command", json={"channel": "telegram", "command": "approve"}
    )
    assert response.status_code == 200
    assert "Usage:" in response.json()["reply"]


def test_pairing_command_rejects_missing_fields(client: TestClient) -> None:
    response = client.post("/channels/pairing/command", json={"channel": "telegram"})
    assert response.status_code == 422
