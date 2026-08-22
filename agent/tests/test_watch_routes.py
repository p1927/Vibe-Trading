"""TestClient coverage for `watch_routes.py` (`/watches/*`) — previously untested.

Domain: `2026-08-21-agent-api-route-coverage-audit`. `watch_routes.py` is a thin wrapper over
`trade_integrations.watch_registry.api`'s `mcp_*` functions — real file-backed CRUD, no broker
calls — so these drive the real store through the real HTTP surface, isolated to a tmp hub dir.

Note: unlike every other privileged router audited in this backlog (`live_routes`,
`autonomous_routes`), no endpoint here has an auth dependency — every `/watches/*` route is
reachable with no credentials. Not fixed here (out of scope for a coverage-audit pass — a
behavior change like that needs a product decision, not a drive-by test-file fix), but worth
flagging for whoever picks up the "wire real auth" follow-up.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import api_server
import trade_integrations.watch_registry.store as watch_store


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    tmp = Path(tempfile.mkdtemp(prefix="watch_routes_test_"))
    # store.py binds `get_hub_dir` via `from ... import get_hub_dir` (a local name, not a
    # module-attribute lookup) — patching the defining module's attribute wouldn't reach this
    # already-bound reference, so the module's own name must be patched directly.
    monkeypatch.setattr(watch_store, "get_hub_dir", lambda: tmp)
    monkeypatch.setattr(api_server, "_API_KEY", "")
    return TestClient(api_server.app, client=("127.0.0.1", 50000))


def test_list_watches_empty_initially(client: TestClient) -> None:
    response = client.get("/watches", params={"session_id": "s1"})

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "watches": [], "count": 0}


def test_create_list_update_delete_session_watch_round_trip(client: TestClient) -> None:
    create = client.post(
        "/watches/session/s1",
        json={
            "watch_spec": {"rules": [{"field": "price", "op": ">", "value": 100}]},
            "symbols": ["AAPL"],
            "label": "test watch",
        },
    )
    assert create.status_code == 200
    created = create.json()
    assert created["status"] in ("ok", "partial")  # "partial" when the real Nautilus sync no-ops
    watch_id = created["watch"]["watch_id"]
    assert created["watch"]["symbols"] == ["AAPL"]
    assert created["watch"]["label"] == "test watch"

    listed = client.get("/watches", params={"session_id": "s1"})
    assert listed.status_code == 200
    assert listed.json()["count"] == 1
    assert listed.json()["watches"][0]["watch_id"] == watch_id

    updated = client.patch(f"/watches/{watch_id}", json={"label": "renamed"})
    assert updated.status_code == 200
    assert updated.json()["watch"]["label"] == "renamed"

    deleted = client.delete(f"/watches/{watch_id}")
    assert deleted.status_code == 200

    listed_after = client.get("/watches", params={"session_id": "s1"})
    assert listed_after.json()["count"] == 0


def test_create_watch_without_rules_returns_400(client: TestClient) -> None:
    response = client.post(
        "/watches/session/s1",
        json={"watch_spec": {}, "symbols": ["AAPL"]},
    )

    assert response.status_code == 400


def test_update_unknown_watch_returns_404(client: TestClient) -> None:
    response = client.patch("/watches/does-not-exist", json={"label": "x"})

    assert response.status_code == 404


def test_delete_unknown_watch_returns_404(client: TestClient) -> None:
    response = client.delete("/watches/does-not-exist")

    assert response.status_code == 404


def test_create_agent_watch_for_unknown_agent_returns_404(client: TestClient) -> None:
    response = client.post(
        "/watches/agent/does-not-exist",
        json={"watch_spec": {"rules": [{"field": "price", "op": ">", "value": 1}]}},
    )

    assert response.status_code == 404


def test_live_snapshot_sets_suggested_poll_header(client: TestClient) -> None:
    response = client.get("/watches/live", params={"session_id": "s1"})

    assert response.status_code == 200
    assert response.headers["X-Suggested-Poll-Ms"] == "5000"
