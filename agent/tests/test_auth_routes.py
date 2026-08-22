"""TestClient coverage for `auth_routes.py` (`/auth/sse-ticket`) — previously untested.

Domain: `2026-08-21-agent-api-route-coverage-audit`. Single endpoint: mint a short-lived,
single-use ticket that lets a browser `EventSource` (which can't send an `Authorization`
header) authenticate an SSE connection without putting the long-lived API key in the URL.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import api_server
from src.api.security import _consume_sse_ticket


@pytest.fixture
def client() -> TestClient:
    return TestClient(api_server.app, client=("127.0.0.1", 50000))


def test_mint_ticket_requires_auth_header(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(api_server, "_API_KEY", "secret-key")
    response = client.post("/auth/sse-ticket")
    assert response.status_code in (401, 403)


def test_mint_ticket_succeeds_with_valid_auth_header(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(api_server, "_API_KEY", "secret-key")
    response = client.post(
        "/auth/sse-ticket", headers={"Authorization": "Bearer secret-key"}
    )
    assert response.status_code == 200
    ticket = response.json()["ticket"]
    assert isinstance(ticket, str) and len(ticket) > 20


def test_mint_ticket_when_auth_disabled_still_works(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # House pattern: an empty configured key means the loopback bypass applies.
    monkeypatch.setattr(api_server, "_API_KEY", "")
    response = client.post("/auth/sse-ticket")
    assert response.status_code == 200
    assert "ticket" in response.json()


def test_minted_ticket_is_valid_and_single_use(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(api_server, "_API_KEY", "")
    ticket = client.post("/auth/sse-ticket").json()["ticket"]

    assert _consume_sse_ticket(ticket) is True
    # Single-use: a second consume of the same ticket must fail.
    assert _consume_sse_ticket(ticket) is False


def test_each_call_mints_a_distinct_ticket(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(api_server, "_API_KEY", "")
    first = client.post("/auth/sse-ticket").json()["ticket"]
    second = client.post("/auth/sse-ticket").json()["ticket"]
    assert first != second
