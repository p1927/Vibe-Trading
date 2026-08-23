"""TestClient coverage for `execution_advisor_routes.py`
(`GET /execution-advisor/positions`) — module 7's read-only advisory surface.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import api_server


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(api_server, "_API_KEY", "")
    return TestClient(api_server.app, client=("127.0.0.1", 50000))


def test_execution_advisor_positions_returns_advisories_and_grouping(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    import trade_integrations.dataflows.index_research.execution_advisor as advisor_mod

    fake_advisories = [
        {"symbol": "NIFTY24AUGFUT", "strategy_group_id": "iron_condor_1", "fsm_state": "trailing", "action": "hold"},
        {"symbol": "RELIANCE", "strategy_group_id": None, "fsm_state": "in_trade", "action": "hold"},
    ]
    monkeypatch.setattr(advisor_mod, "advise_positions", lambda: fake_advisories)

    response = client.get("/execution-advisor/positions")
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["count"] == 2
    assert body["advisories"] == fake_advisories
    assert body["grouped"]["iron_condor_1"] == [fake_advisories[0]]
    assert body["grouped"]["ungrouped"] == [fake_advisories[1]]


def test_execution_advisor_positions_empty_when_no_open_positions(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    import trade_integrations.dataflows.index_research.execution_advisor as advisor_mod

    monkeypatch.setattr(advisor_mod, "advise_positions", lambda: [])

    response = client.get("/execution-advisor/positions")
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["count"] == 0
    assert body["advisories"] == []
    assert body["grouped"] == {}


def test_execution_advisor_positions_failure_returns_502(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    import trade_integrations.dataflows.index_research.execution_advisor as advisor_mod

    def _boom():
        raise RuntimeError("openalgo unreachable")

    monkeypatch.setattr(advisor_mod, "advise_positions", _boom)

    response = client.get("/execution-advisor/positions")
    assert response.status_code == 502
    assert response.json()["ok"] is False
