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


def _save_test_agent(agent_id: str) -> None:
    from trade_integrations.autonomous_agents.store import save_agent

    save_agent(
        {
            "id": agent_id,
            "type": "autonomous_agent.instance",
            "name": "test agent",
            "status": "running",
            "vibe_session_id": "sess-1",
            "symbols": ["NIFTY"],
            "execution_market": "IN",
            "execution_backend": "paper",
            "schedules": {},
        }
    )


def test_exit_evaluation_post_for_unknown_agent_is_a_client_error(client: TestClient) -> None:
    response = client.post(
        "/autonomous-agents/does-not-exist/exit-evaluations",
        json={"ticker": "NIFTY", "exit_decision_at": "2026-08-20T10:00:00+00:00"},
    )

    assert response.status_code == 404


def test_exit_evaluation_get_for_unknown_agent_is_a_client_error(client: TestClient) -> None:
    response = client.get("/autonomous-agents/does-not-exist/exit-evaluations")

    assert response.status_code == 404


def test_exit_evaluation_post_records_and_get_lists_it(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No Authorization header sent — loopback bypass, same as
    `test_clear_all_agents_succeeds_via_loopback_auth` above."""
    from trade_integrations.dataflows.news_hub_bridge import replay_gate
    from trade_integrations.stock_simulator.client import StockSimulatorClient

    agent_id = "agent-exit-eval"
    _save_test_agent(agent_id)

    def fake_get_quote(self, symbol, exchange, *, force_live=False):
        assert force_live is True
        return {"status": "ok", "mode": "live", "data": {"ltp": 25200.0}}

    monkeypatch.setattr(StockSimulatorClient, "get_quote", fake_get_quote)
    monkeypatch.setattr(
        replay_gate,
        "current_headlines",
        lambda **kw: [{"title": "rate cut", "actual_impact": {"nifty_points": 30.0}}],
    )

    response = client.post(
        f"/autonomous-agents/{agent_id}/exit-evaluations",
        json={
            "ticker": "NIFTY",
            "exit_decision_at": "2026-08-20T10:00:00+00:00",
            "exit_rationale": "target hit",
            "exit_direction": "LONG",
            "reference_price": 25000.0,
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["verdict"] == "early_exit"
    assert body["news_alignment"] == "consistent"

    list_response = client.get(f"/autonomous-agents/{agent_id}/exit-evaluations")
    assert list_response.status_code == 200
    evaluations = list_response.json()["evaluations"]
    assert len(evaluations) == 1
    assert evaluations[0]["exit_decision_at"] == "2026-08-20T10:00:00+00:00"


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


def test_resume_onto_a_stopped_executor_says_nothing_will_dispatch(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """2026-09-06-boot-pause-under-reload: after a restart, resuming the agent alone gave
    `status: running` with all jobs registered and zero ticks, and nothing said so."""
    import src.api.scheduled_routes as scheduled_routes
    import src.scheduled_research.autonomous_agent_jobs as agent_jobs
    from trade_integrations.autonomous_agents.store import save_agent

    agent_id = "agent-resume-stopped-executor"
    paused_agent = {
        "id": agent_id,
        "type": "autonomous_agent.instance",
        "name": "test agent",
        "status": "paused",
        "pause_reason": "user",
        "bootstrap_status": "done",
        "vibe_session_id": "sess-1",
        "symbols": ["RELIANCE"],
        "execution_market": "IN",
        "execution_backend": "paper",
        "schedules": {},
    }
    save_agent(dict(paused_agent))
    monkeypatch.setattr(agent_jobs, "register_agent_jobs", lambda agent: None)

    class _StoppedExecutor:
        is_running = False

    monkeypatch.setattr(scheduled_routes, "_get_scheduled_research_executor", lambda: _StoppedExecutor())
    monkeypatch.setattr(scheduled_routes, "_scheduled_research_scheduler_enabled", lambda: True)

    body = client.post(f"/autonomous-agents/{agent_id}/resume").json()

    assert body["scheduler"] == {"enabled": True, "running": False}
    assert "/scheduled-runs/scheduler/resume" in body["warning"]

    class _RunningExecutor:
        is_running = True

    monkeypatch.setattr(scheduled_routes, "_get_scheduled_research_executor", lambda: _RunningExecutor())
    save_agent(dict(paused_agent))

    body = client.post(f"/autonomous-agents/{agent_id}/resume").json()

    assert body["scheduler"] == {"enabled": True, "running": True}
    assert "warning" not in body


def test_commit_already_in_progress_is_409_not_a_client_error(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """2026-09-07-commit-route-times-out: a retry of a slow commit that is still running
    server-side used to get a bare 400, which reads like "your commit failed"."""
    import src.api.autonomous_routes as autonomous_routes
    import trade_integrations.autonomous_agents.proposals as proposals
    from trade_integrations.autonomous_agents.store import CommitInProgressError

    monkeypatch.setattr(autonomous_routes, "_session_service", lambda: object())

    def _in_progress(**kwargs):
        raise CommitInProgressError("commit already in progress")

    monkeypatch.setattr(proposals, "commit_autonomous_agent", _in_progress)

    response = client.post(
        "/autonomous-agents/commit",
        json={"proposal_id": "aap_slow", "consent_ack": True},
    )

    assert response.status_code == 409, response.text
    detail = response.json()["detail"]
    assert detail["status"] == "in_progress"
    assert detail["proposal_id"] == "aap_slow"

    def _invalid(**kwargs):
        raise ValueError("proposal expired")

    monkeypatch.setattr(proposals, "commit_autonomous_agent", _invalid)

    response = client.post(
        "/autonomous-agents/commit",
        json={"proposal_id": "aap_slow", "consent_ack": True},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "proposal expired"


def test_every_mutating_route_requires_local_or_auth() -> None:
    """Mechanical regression test for
    `.claude/backlog/items/2026-09-07-approve-plan-route-no-auth.md` (and the earlier
    `.claude/backlog/archive/items/2026-08-29-resume-agent-missing-auth-guard.md`, which this
    generalizes): every mutating route (POST/PUT/PATCH/DELETE) on `autonomous_router` must
    depend on `require_local_or_auth`. A one-route-at-a-time hand fix has already let this same
    bug through twice (`resume_agent`, then `approve_plan_route`/`reject_plan_route`) — this
    test is the actual deliverable, so the *next* mutating route added to this router fails CI
    if it silently omits the dependency.
    """
    from src.api.autonomous_routes import autonomous_router
    from src.api.security import require_local_or_auth

    mutating_methods = {"POST", "PUT", "PATCH", "DELETE"}
    missing: list[str] = []

    for route in autonomous_router.routes:
        methods = getattr(route, "methods", None) or set()
        if not (methods & mutating_methods):
            continue
        dependant = getattr(route, "dependant", None)
        dependencies = getattr(dependant, "dependencies", []) if dependant else []
        has_guard = any(
            getattr(dep, "call", None) is require_local_or_auth for dep in dependencies
        )
        if not has_guard:
            missing.append(f"{sorted(methods)} {route.path}")

    assert not missing, (
        "mutating route(s) on autonomous_router missing Depends(require_local_or_auth): "
        f"{missing}"
    )


# --- simulation completion prompt (Step G of the replay-epoch item) ---------------------
#
# A completed replay pass stops the agent terminally and the UI asks the human whether to run
# the next simulation, with a choice of *same configuration* or *new configuration*. Declining
# keeps every record. See `docs/add/autonomous_agents.md` § "Simulation runs".


def _save_stopped_simulation(agent_id: str) -> None:
    from trade_integrations.autonomous_agents.store import save_agent

    save_agent(
        {
            "id": agent_id,
            "type": "autonomous_agent.instance",
            "name": "sim agent",
            "status": "stopped",
            "stop_reason": "simulation_complete",
            "pause_reason": None,
            "symbols": ["NIFTY"],
            "mandate": "Paper trade NIFTY autonomously.",
            "execution_market": "IN",
            "constraints": {"mode": "paper", "budget_inr": 20000.0},
            "simulation_run_id": "sim_abcabcabcabc_e1",
            "simulation_completed_run_id": "sim_abcabcabcabc_e1",
            "simulation_next_run_id": "sim_abcabcabcabc_e2",
        }
    )


def test_simulation_state_reports_a_pending_prompt(client: TestClient) -> None:
    _save_stopped_simulation("aa_route_sim1")

    body = client.get("/autonomous-agents/aa_route_sim1/simulation").json()

    assert body["simulation_complete"] is True
    assert body["prompt_pending"] is True
    assert body["resumable"] is False
    assert body["choices"] == ["same_configuration", "new_configuration", "decline"]
    assert body["config"]["symbols"] == ["NIFTY"]


def test_simulation_state_404s_for_an_unknown_agent(client: TestClient) -> None:
    assert client.get("/autonomous-agents/aa_missing/simulation").status_code == 404


def test_next_simulation_rejects_an_agent_whose_pass_has_not_completed(client: TestClient) -> None:
    """A restart-paused agent must never be offered the next-simulation prompt."""
    from trade_integrations.autonomous_agents.store import save_agent

    save_agent(
        {
            "id": "aa_route_sim2",
            "type": "autonomous_agent.instance",
            "name": "paused agent",
            "status": "paused",
            "pause_reason": "restart",
            "symbols": ["NIFTY"],
            "constraints": {"mode": "paper"},
        }
    )

    response = client.post("/autonomous-agents/aa_route_sim2/next-simulation", json={"configuration": "decline"})

    assert response.status_code == 409
    assert response.json()["detail"]["error"] == "simulation_not_complete"


def test_declining_keeps_the_agent_and_its_data(client: TestClient) -> None:
    from trade_integrations.autonomous_agents.store import get_agent

    _save_stopped_simulation("aa_route_sim3")

    body = client.post(
        "/autonomous-agents/aa_route_sim3/next-simulation", json={"configuration": "decline"}
    ).json()

    assert body["action"] == "declined"
    assert body["data_retained"] is True
    agent = get_agent("aa_route_sim3")
    assert agent is not None and agent["status"] == "stopped"
    assert client.get("/autonomous-agents/aa_route_sim3/simulation").json()["prompt_pending"] is False


def test_new_configuration_returns_a_prefill_and_creates_nothing(client: TestClient) -> None:
    from trade_integrations.autonomous_agents.store import list_agents

    _save_stopped_simulation("aa_route_sim4")
    before = len(list_agents())

    body = client.post(
        "/autonomous-agents/aa_route_sim4/next-simulation", json={"configuration": "new_configuration"}
    ).json()

    assert body["action"] == "prefill"
    assert body["config"]["mandate"].startswith("Paper trade NIFTY")
    assert len(list_agents()) == before


def test_same_configuration_requires_consent(client: TestClient) -> None:
    _save_stopped_simulation("aa_route_sim5")

    response = client.post(
        "/autonomous-agents/aa_route_sim5/next-simulation",
        json={"configuration": "same_configuration", "consent_ack": False},
    )

    assert response.status_code == 400
    assert "consent_ack" in str(response.json()["detail"])


def test_unknown_configuration_choice_is_rejected(client: TestClient) -> None:
    _save_stopped_simulation("aa_route_sim6")

    response = client.post(
        "/autonomous-agents/aa_route_sim6/next-simulation", json={"configuration": "resume"}
    )

    assert response.status_code == 400
