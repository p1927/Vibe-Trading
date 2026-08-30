"""TestClient coverage for `autonomous_routes.py` (`/autonomous-agents/*`) — previously untested.

Domain: `2026-08-21-agent-api-route-coverage-audit`. Found and fixed a real bug while auditing
this module: `autonomous_router` was defined (`src/api/autonomous_routes.py:17`) but never
mounted — no `app.include_router(autonomous_router)` anywhere in `api_server.py`, unlike every
other router (`qveris_router`, `trade_router`, `watch_router`, ...). The frontend actively calls
`/autonomous-agents/*` from several components (`AutonomousAgentHub.tsx`, `Autonomous.tsx`,
`TradePlanWidgetCard.tsx`) — this meant the entire Autonomous Agents feature's backend API was
404ing on every request. Fixed in `api_server.py` (added the missing import + include_router,
mirroring the `qveris_router` mounting pattern) as part of this same change.
`test_router_is_mounted_on_the_app` below is a regression test for exactly that bug — it would
have failed before the fix and must never regress.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import api_server
import trade_integrations.context.hub as hub_context


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    tmp = Path(tempfile.mkdtemp(prefix="autonomous_routes_test_"))
    # autonomous_agents/store.py does `from trade_integrations.context import hub as
    # hub_context` then calls `hub_context.get_hub_dir()` at call time — a module-attribute
    # lookup, not an import-time-bound name — so patching the real module's attribute here
    # reaches every caller that resolves it this way.
    monkeypatch.setattr(hub_context, "get_hub_dir", lambda: tmp)
    monkeypatch.setattr(api_server, "_API_KEY", "")
    return TestClient(api_server.app, client=("127.0.0.1", 50000))


def test_router_is_mounted_on_the_app(client: TestClient) -> None:
    """Regression test for the router-never-mounted bug found while auditing this module
    (see module docstring) — `/autonomous-agents/*` must be reachable, not 404."""
    response = client.get("/autonomous-agents/stack-health")

    assert response.status_code != 404, (
        "GET /autonomous-agents/stack-health returned 404 — autonomous_router is not mounted "
        "on the app (the exact bug this test regresses against). Check api_server.py includes "
        "`from src.api.autonomous_routes import autonomous_router` + "
        "`app.include_router(autonomous_router)`."
    )
    assert response.status_code == 200


def test_list_agents_empty_on_fresh_hub_dir(client: TestClient) -> None:
    response = client.get("/autonomous-agents")

    assert response.status_code == 200
    body = response.json()
    assert body["agents"] == []
    # The endpoint also folds in a stack_health snapshot — not this test's concern (covered by
    # the dedicated /stack-health endpoint test below), just asserting it's present/well-formed.
    assert "stack_health" in body


def test_clear_all_agents_succeeds_via_loopback_auth(client: TestClient) -> None:
    """No Authorization header is sent — must succeed because the TestClient's
    ("127.0.0.1", 50000) address satisfies `require_local_or_auth`'s loopback bypass."""
    response = client.post("/autonomous-agents/clear-all")

    assert response.status_code == 200


def test_delete_unknown_agent_is_a_client_error(client: TestClient) -> None:
    response = client.delete("/autonomous-agents/does-not-exist")

    assert 400 <= response.status_code < 500, (
        f"expected a 4xx client error for an unknown agent id, got {response.status_code}: "
        f"{response.text}"
    )


def test_pnl_history_for_unknown_agent_is_a_client_error(client: TestClient) -> None:
    response = client.get("/autonomous-agents/does-not-exist/pnl-history")

    assert 400 <= response.status_code < 500


def test_drafts_get_is_explicitly_not_allowed(client: TestClient) -> None:
    """GET /drafts is deliberately blocked (405) — only POST creates a draft. Regression for
    the route's own explicit `raise HTTPException(405, ...)` contract."""
    response = client.get("/autonomous-agents/drafts")

    assert response.status_code == 405


def test_resume_reschedules_a_failed_bootstrap(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression test for
    `.claude/backlog/items/2026-08-29-failed-bootstrap-not-retried-on-resume.md`:
    resuming an agent left with `bootstrap_status=="failed"` (e.g. a user-paused agent
    whose bootstrap was force-failed by `_fail_stuck_bootstrap`, or a plain bootstrap.py
    timeout/exception) must explicitly re-schedule bootstrap — `resume_autonomous_agent`
    only flips `status` back to `"running"` and nothing else revisits a terminal
    `bootstrap_status`.
    """
    from trade_integrations.autonomous_agents.store import save_agent
    import src.scheduled_research.autonomous_bootstrap as autonomous_bootstrap

    agent_id = "agent-failed-bootstrap"
    save_agent(
        {
            "id": agent_id,
            "type": "autonomous_agent.instance",
            "name": "test agent",
            "status": "paused",
            "pause_reason": "user",
            "bootstrap_status": "failed",
            "bootstrap_error": "bootstrap timed out after 300s",
            "vibe_session_id": "sess-1",
            "symbols": ["RELIANCE"],
            "execution_market": "IN",
            "execution_backend": "paper",
            "schedules": {},
        }
    )

    calls: list[str] = []
    monkeypatch.setattr(
        autonomous_bootstrap,
        "schedule_agent_bootstrap",
        lambda aid: calls.append(aid) or True,
    )

    response = client.post(f"/autonomous-agents/{agent_id}/resume")

    assert response.status_code == 200, response.text
    assert calls == [agent_id], (
        "resuming an agent with bootstrap_status=='failed' did not call "
        "schedule_agent_bootstrap — the bootstrap will never retry"
    )
