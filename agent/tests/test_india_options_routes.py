"""TestClient coverage for `india_options_routes.py`
(`GET /options/india/underlyings`, `GET /options/research`) — previously untested.

Domain: `2026-08-21-agent-api-route-coverage-audit`. Confirmed correctly mounted (via
`register_india_options_routes()`, called from `register_options_routes()` in
`options_routes.py`, itself mounted by `api_server.py`). Both routes wrap tools that make real
market-data/vendor calls (`list_india_underlyings`, `IndiaOptionsResearchTool`) — mocked at the
module-attribute level rather than exercised live.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

import api_server
import src.api.india_options_routes as india_options_routes


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(api_server, "_API_KEY", "")
    return TestClient(api_server.app, client=("127.0.0.1", 50000))


def test_underlyings_returns_tool_data(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        india_options_routes,
        "list_india_underlyings",
        lambda source: {"indexes": ["NIFTY", "BANKNIFTY"], "equities": ["RELIANCE"]},
    )
    response = client.get("/options/india/underlyings")
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["data"]["indexes"] == ["NIFTY", "BANKNIFTY"]


def test_underlyings_passes_through_source_query_param(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen = {}

    def fake_list(source):
        seen["source"] = source
        return {"indexes": [], "equities": None}

    monkeypatch.setattr(india_options_routes, "list_india_underlyings", fake_list)
    response = client.get("/options/india/underlyings", params={"source": "live"})
    assert response.status_code == 200
    assert seen["source"] == "live"


def test_underlyings_tool_error_returns_502(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(source):
        raise RuntimeError("nselib is down")

    monkeypatch.setattr(india_options_routes, "list_india_underlyings", boom)
    response = client.get("/options/india/underlyings")
    assert response.status_code == 502
    assert response.json() == {"ok": False, "error": "underlyings lookup failed"}


def test_research_requires_ticker(client: TestClient) -> None:
    response = client.get("/options/research")
    assert response.status_code == 400
    assert "ticker" in response.json()["error"]


def test_research_rejects_blank_ticker(client: TestClient) -> None:
    response = client.get("/options/research", params={"ticker": "   "})
    assert response.status_code == 400


class _FakeResearchTool:
    def execute(self, **kwargs):
        return json.dumps({"ok": True, "ticker": kwargs["ticker"], "strategies": []})


def test_research_success_returns_tool_envelope(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(india_options_routes, "IndiaOptionsResearchTool", _FakeResearchTool)
    response = client.get("/options/research", params={"ticker": "NIFTY"})
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["ticker"] == "NIFTY"


class _FakeFailingResearchTool:
    def execute(self, **kwargs):
        return json.dumps({"ok": False, "error": "no strategies found"})


def test_research_tool_reported_failure_returns_502(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(india_options_routes, "IndiaOptionsResearchTool", _FakeFailingResearchTool)
    response = client.get("/options/research", params={"ticker": "NIFTY"})
    assert response.status_code == 502
    assert response.json()["ok"] is False


class _FakeRaisingResearchTool:
    def execute(self, **kwargs):
        raise RuntimeError("yfinance timed out")


def test_research_tool_exception_returns_502(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(india_options_routes, "IndiaOptionsResearchTool", _FakeRaisingResearchTool)
    response = client.get("/options/research", params={"ticker": "NIFTY"})
    assert response.status_code == 502
    assert response.json() == {"ok": False, "error": "options research request failed"}
