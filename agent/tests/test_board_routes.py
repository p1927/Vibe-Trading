"""TestClient coverage for `board_routes.py` (`/board/*`) —
2026-08-25-dual-board-advisory-agent-ui's Board 2 (Agent) read layer.

`test_router_is_mounted_on_the_app` mirrors `test_autonomous_routes.py`'s own regression
test for the exact "router defined but never `include_router`'d" bug that audit found —
worth checking again every time a new router is added rather than assuming it won't recur.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import api_server
import trade_integrations.context.hub as hub_context
from trade_integrations.autonomous_agents.store import save_agent


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    tmp = Path(tempfile.mkdtemp(prefix="board_routes_test_"))
    monkeypatch.setattr(hub_context, "get_hub_dir", lambda: tmp)
    monkeypatch.setattr(api_server, "_API_KEY", "")
    return TestClient(api_server.app, client=("127.0.0.1", 50000))


def _make_agent(agent_id: str = "aa_test1") -> dict:
    agent = {"id": agent_id, "status": "running", "symbols": ["NIFTY"]}
    save_agent(agent)
    return agent


def test_router_is_mounted_on_the_app(client: TestClient) -> None:
    _make_agent()
    response = client.get("/board/agent/aa_test1/summary")

    assert response.status_code != 404, (
        "GET /board/agent/<id>/summary returned 404 — board_router is not mounted on the "
        "app. Check api_server.py includes `from src.api.board_routes import board_router` "
        "+ `app.include_router(board_router)`."
    )
    assert response.status_code == 200


def test_summary_for_unknown_agent_is_a_client_error(client: TestClient) -> None:
    response = client.get("/board/agent/does-not-exist/summary")
    assert response.status_code == 404


def test_summary_shape_for_a_fresh_agent(client: TestClient) -> None:
    _make_agent()
    response = client.get("/board/agent/aa_test1/summary")

    assert response.status_code == 200
    body = response.json()
    assert body["agent_id"] == "aa_test1"
    assert body["pnl_summary"]["trade_count"] == 0
    assert body["alignment"]["total"] == 0


def test_wealth_curve_empty_for_a_fresh_agent(client: TestClient) -> None:
    _make_agent()
    response = client.get("/board/agent/aa_test1/wealth-curve")

    assert response.status_code == 200
    assert response.json() == {"agent_id": "aa_test1", "points": []}


def test_wealth_curve_for_unknown_agent_is_a_client_error(client: TestClient) -> None:
    response = client.get("/board/agent/does-not-exist/wealth-curve")
    assert response.status_code == 404


def test_hindsight_summary_empty_for_a_fresh_agent(client: TestClient) -> None:
    _make_agent()
    response = client.get("/board/agent/aa_test1/hindsight")

    assert response.status_code == 200
    body = response.json()
    assert body == {
        "trade_decision_points": 0,
        "candidates": [],
        "best_candidate_rank": None,
        "attribution_findings": [],
        "attribution_factor_rollup": [],
    }


def test_hindsight_curves_empty_for_a_fresh_agent(client: TestClient) -> None:
    _make_agent()
    response = client.get("/board/agent/aa_test1/hindsight-curves")

    assert response.status_code == 200
    assert response.json() == {"agent_id": "aa_test1", "curves": []}


def test_hindsight_for_unknown_agent_is_a_client_error(client: TestClient) -> None:
    response = client.get("/board/agent/does-not-exist/hindsight")
    assert response.status_code == 404


def test_model_version_timeline_empty_when_nothing_applied(client: TestClient) -> None:
    response = client.get("/board/model-version-timeline")

    assert response.status_code == 200
    assert response.json() == {"timeline": []}


def test_model_version_timeline_scoped_to_unknown_agent_is_a_client_error(client: TestClient) -> None:
    response = client.get("/board/model-version-timeline?agent_id=does-not-exist")
    assert response.status_code == 404


def test_model_version_timeline_omitting_agent_id_does_not_require_an_agent(client: TestClient) -> None:
    # No agent created at all in this test -- must not 404 just because agent_id was omitted.
    response = client.get("/board/model-version-timeline")
    assert response.status_code == 200
