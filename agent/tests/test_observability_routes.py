"""TestClient coverage for `observability_routes.py` (`/trade/observability/*`) —
previously untested and, until this pass, not mounted at all.

Domain: `2026-08-21-agent-api-route-coverage-audit`.

**Real production bug found and fixed while auditing this module**: same bug class as the
`autonomous_routes` fix earlier in this audit — `observability_router` was defined but never
`include_router`ed onto the app (`api_server.py`), so every `/trade/observability/*` request
404ed. The frontend actively calls `GET /trade/observability/summary`
(`frontend/src/lib/api.ts:1495-1496`, used by `frontend/src/pages/Hub.tsx`), so this was a real
broken feature, not a theoretical gap. Fixed by adding the missing
`app.include_router(observability_router)` call, mirroring the `autonomous_router`/`qveris_router`
mounting pattern. `test_router_is_mounted_on_the_app` below is a regression test for exactly
this.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import api_server
from trade_integrations.observability import issues as observability_issues


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    tmp = Path(tempfile.mkdtemp(prefix="observability_routes_test_"))
    # paths.py resolves everything relative to TRADE_OBSERVABILITY_DIR when set.
    monkeypatch.setenv("TRADE_OBSERVABILITY_DIR", str(tmp))
    # issues.py memoizes `_open_cache` once populated ("if _open_cache: return") — a stale
    # cache from an earlier test/process would silently ignore this test's isolated tmp dir,
    # so it must be cleared per-test the same way other module-level caches in this codebase
    # (e.g. news_staging_store's get_hub_dir binding) have been found to leak across tests.
    monkeypatch.setattr(observability_issues, "_open_cache", {})
    monkeypatch.setattr(api_server, "_API_KEY", "")
    return TestClient(api_server.app, client=("127.0.0.1", 50000))


def test_router_is_mounted_on_the_app(client: TestClient) -> None:
    response = client.get("/trade/observability/summary")
    assert response.status_code == 200


def test_summary_shape_when_empty(client: TestClient) -> None:
    response = client.get("/trade/observability/summary")
    assert response.status_code == 200
    body = response.json()
    assert body["open_issue_count"] == 0
    assert body["recent_events"] == []
    assert body["events_path"]
    assert body["issues_path"]


def test_list_issues_empty_initially(client: TestClient) -> None:
    response = client.get("/trade/observability/issues")
    assert response.status_code == 200
    assert response.json() == {"issues": [], "open_count": 0}


def test_list_issues_accepts_status_and_module_filters(client: TestClient) -> None:
    response = client.get(
        "/trade/observability/issues", params={"status": "resolved", "module": "ingest"}
    )
    assert response.status_code == 200
    assert response.json() == {"issues": [], "open_count": 0}


def test_resolve_unknown_issue_returns_404(client: TestClient) -> None:
    response = client.post("/trade/observability/issues/nonexistent-id/resolve")
    assert response.status_code == 404


def test_resolve_real_issue_round_trip(client: TestClient) -> None:
    from trade_integrations.observability.schema import ObservabilityEvent

    event = ObservabilityEvent(
        module="ingest",
        event="ingest_failed",
        level="error",
        detail={"source": "test-source"},
    )
    issue = observability_issues.record_issue_from_event(event)
    assert issue is not None

    listed = client.get("/trade/observability/issues").json()
    assert listed["open_count"] == 1
    assert listed["issues"][0]["issue_id"] == issue.issue_id

    resolved = client.post(f"/trade/observability/issues/{issue.issue_id}/resolve")
    assert resolved.status_code == 200
    assert resolved.json() == {"issue_id": issue.issue_id, "resolved": True}

    after = client.get("/trade/observability/issues").json()
    assert after == {"issues": [], "open_count": 0}
