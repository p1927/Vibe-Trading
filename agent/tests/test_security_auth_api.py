"""Security regression tests for API authentication boundaries."""

from __future__ import annotations

import ipaddress
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import api_server


def _remote_client() -> TestClient:
    """Return a TestClient that simulates a non-loopback caller."""
    return TestClient(api_server.app, client=("203.0.113.10", 50000))


def _local_client() -> TestClient:
    """Return a TestClient that simulates a loopback caller."""
    return TestClient(api_server.app, client=("127.0.0.1", 50000))


@pytest.fixture(autouse=True)
def clear_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Start every auth test from dev-mode auth."""
    monkeypatch.delenv("API_AUTH_KEY", raising=False)
    monkeypatch.delenv("VIBE_TRADING_TRUST_DOCKER_LOOPBACK", raising=False)
    monkeypatch.delenv("VIBE_TRADING_ENABLE_SHELL_TOOLS", raising=False)
    monkeypatch.setattr(api_server, "_API_KEY", "")


def test_remote_write_requires_api_key_when_key_unset() -> None:
    response = _remote_client().post("/sessions", json={})

    assert response.status_code == 403
    assert "API_AUTH_KEY" in response.json()["detail"]


def test_local_dev_write_allowed_when_key_unset() -> None:
    response = _local_client().post("/sessions", json={})

    assert response.status_code in {201, 501}


def test_docker_gateway_dev_write_allowed_only_with_compose_trust_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = SimpleNamespace(client=SimpleNamespace(host="172.18.0.1"))
    monkeypatch.setattr(
        api_server,
        "_default_gateway_ips",
        lambda: {ipaddress.IPv4Address("172.18.0.1")},
    )

    assert not api_server._is_local_client(request)

    monkeypatch.setenv("VIBE_TRADING_TRUST_DOCKER_LOOPBACK", "1")

    assert api_server._is_local_client(request)


def test_docker_network_peer_is_not_local_even_with_compose_trust_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = SimpleNamespace(client=SimpleNamespace(host="172.18.0.42"))
    monkeypatch.setenv("VIBE_TRADING_TRUST_DOCKER_LOOPBACK", "1")
    monkeypatch.setattr(
        api_server,
        "_default_gateway_ips",
        lambda: {ipaddress.IPv4Address("172.18.0.1")},
    )

    assert not api_server._is_local_client(request)


def test_configured_api_key_required_for_sensitive_reads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("API_AUTH_KEY", "secret")
    monkeypatch.setattr(api_server, "_API_KEY", "secret")
    client = _remote_client()

    for path in [
        "/runs",
        "/sessions",
        "/swarm/runs",
    ]:
        response = client.get(path)
        assert response.status_code == 401, path


def test_configured_api_key_accepts_bearer_for_sensitive_reads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("API_AUTH_KEY", "secret")
    monkeypatch.setattr(api_server, "_API_KEY", "secret")

    response = _remote_client().get(
        "/runs",
        headers={"Authorization": "Bearer secret"},
    )

    assert response.status_code == 200


def test_configured_api_key_required_for_session_event_stream(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("API_AUTH_KEY", "secret")
    monkeypatch.setattr(api_server, "_API_KEY", "secret")

    response = _remote_client().get("/sessions/missing/events")

    assert response.status_code == 401


def test_session_event_stream_accepts_query_token_for_browser_eventsource(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("API_AUTH_KEY", "secret")
    monkeypatch.setattr(api_server, "_API_KEY", "secret")

    response = _remote_client().get("/sessions/missing/events?api_key=secret")

    assert response.status_code in {404, 501}


def test_shell_tools_allowed_for_loopback_api_request() -> None:
    request = SimpleNamespace(client=SimpleNamespace(host="127.0.0.1"))

    assert api_server._shell_tools_enabled_for_request(request)


def test_shell_tools_disabled_for_remote_api_request_by_default() -> None:
    request = SimpleNamespace(client=SimpleNamespace(host="203.0.113.10"))

    assert not api_server._shell_tools_enabled_for_request(request)


def test_shell_tools_remote_api_request_accepts_explicit_opt_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = SimpleNamespace(client=SimpleNamespace(host="203.0.113.10"))
    monkeypatch.setenv("VIBE_TRADING_ENABLE_SHELL_TOOLS", "1")

    assert api_server._shell_tools_enabled_for_request(request)
