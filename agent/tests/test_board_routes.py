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
from trade_integrations.autonomous_agents.store import save_agent


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """Isolated hub dir via ``TRADE_STACK_HUB_DIR`` (not attribute-patching
    ``hub_context.get_hub_dir`` directly) — ``get_hub_dir()`` reads the env var fresh on
    every call, so this isolates every caller regardless of which module imported the
    name. The attribute-patch form used here previously did NOT isolate `weight_model`
    (`store.py`/`proposals.py` both do ``from trade_integrations.context.hub import
    get_hub_dir``, a name binding immune to patching the source module's attribute) —
    confirmed live to silently write real test data into the production hub dir. See
    .claude/backlog/items/2026-08-24-recurring-test-order-flakiness-pattern.md for the
    same fragility pattern in a different test file."""
    tmp = Path(tempfile.mkdtemp(prefix="board_routes_test_"))
    monkeypatch.setenv("TRADE_STACK_HUB_DIR", str(tmp))
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


def test_pending_weight_proposals_empty_when_none_proposed(client: TestClient) -> None:
    response = client.get("/board/weight-proposals")

    assert response.status_code == 200
    assert response.json() == {"proposals": []}


def test_pending_weight_proposals_lists_a_real_proposal(client: TestClient) -> None:
    from trade_integrations.weight_model import REGISTRY, propose_weight_adjustment

    weight_id = next(iter(REGISTRY))
    spec = REGISTRY[weight_id]
    proposed_value = round(min(spec.max_value, spec.default + 0.01), 6)
    propose_weight_adjustment(weight_id, proposed_value, rationale="test proposal")

    response = client.get("/board/weight-proposals")

    assert response.status_code == 200
    proposals = response.json()["proposals"]
    assert len(proposals) == 1
    assert proposals[0]["weight_id"] == weight_id
    assert proposals[0]["status"] == "pending"


def test_apply_weight_proposal_promotes_it_to_the_live_store(client: TestClient) -> None:
    from trade_integrations.weight_model import REGISTRY, get_weight, propose_weight_adjustment

    weight_id = next(iter(REGISTRY))
    spec = REGISTRY[weight_id]
    proposed_value = round(min(spec.max_value, spec.default + 0.01), 6)
    created = propose_weight_adjustment(weight_id, proposed_value, rationale="test proposal")
    proposal_id = created["proposal"]["id"]

    response = client.post(f"/board/weight-proposals/{proposal_id}/apply")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["proposal"]["status"] == "applied"
    assert get_weight(weight_id) == proposed_value


def test_apply_unknown_weight_proposal_is_a_client_error(client: TestClient) -> None:
    response = client.post("/board/weight-proposals/does-not-exist/apply")
    assert response.status_code == 400


def test_apply_already_applied_weight_proposal_is_a_client_error(client: TestClient) -> None:
    from trade_integrations.weight_model import REGISTRY, propose_weight_adjustment

    weight_id = next(iter(REGISTRY))
    spec = REGISTRY[weight_id]
    proposed_value = round(min(spec.max_value, spec.default + 0.01), 6)
    created = propose_weight_adjustment(weight_id, proposed_value, rationale="test proposal")
    proposal_id = created["proposal"]["id"]

    first = client.post(f"/board/weight-proposals/{proposal_id}/apply")
    assert first.status_code == 200

    second = client.post(f"/board/weight-proposals/{proposal_id}/apply")
    assert second.status_code == 400
