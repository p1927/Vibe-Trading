"""HTTP routes for autonomous trading agents."""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

autonomous_router = APIRouter(prefix="/autonomous-agents", tags=["autonomous-agents"])

_TRADE_ROOT = Path(__file__).resolve().parents[4]
_INTEGRATIONS = _TRADE_ROOT / "integrations"
if _INTEGRATIONS.is_dir() and str(_INTEGRATIONS) not in sys.path:
    sys.path.insert(0, str(_INTEGRATIONS))


def _session_service():
    host = sys.modules.get("api_server") or sys.modules.get("agent.api_server")
    if host is None:
        return None
    return host._get_session_service()


class CommitAutonomousAgentRequest(BaseModel):
    proposal_id: str = Field(..., min_length=1)
    consent_ack: bool = False
    session_id: Optional[str] = None


class OrchestratorSessionResponse(BaseModel):
    session_id: str
    title: str


@autonomous_router.get("")
def list_autonomous_agents() -> Dict[str, Any]:
    from trade_integrations.autonomous_agents.infra_startup import maybe_heal_infra_paused_agents
    from trade_integrations.autonomous_agents.runtime_status import build_stack_health, enrich_agent
    from trade_integrations.autonomous_agents.store import list_agents

    try:
        from src.scheduled_research.autonomous_agent_jobs import finalize_infra_heal

        before = {
            str(a.get("id") or ""): str(a.get("status") or "")
            for a in list_agents()
            if str(a.get("pause_reason") or "") == "infra"
        }
        maybe_heal_infra_paused_agents()
        for agent_id, prev_status in before.items():
            if not agent_id or prev_status != "paused":
                continue
            from trade_integrations.autonomous_agents.store import get_agent

            updated = get_agent(agent_id)
            if updated and str(updated.get("status") or "") == "running":
                finalize_infra_heal(agent_id)
    except Exception:
        logger.debug("infra heal on list failed", exc_info=True)

    agents = [enrich_agent(a) for a in list_agents()]
    return {"agents": agents, "stack_health": build_stack_health()}


@autonomous_router.get("/stack-health")
def autonomous_stack_health() -> Dict[str, Any]:
    from trade_integrations.autonomous_agents.runtime_status import build_stack_health

    return build_stack_health()


@autonomous_router.post("/clear-all")
def clear_all_agents_route() -> Dict[str, Any]:
    from trade_integrations.autonomous_agents.proposals import clear_all_autonomous_agents
    from trade_integrations.autonomous_agents.store import list_agents
    from src.scheduled_research.autonomous_agent_jobs import unregister_agent_jobs

    for agent in list_agents():
        agent_id = str(agent.get("id") or "").strip()
        if agent_id:
            unregister_agent_jobs(agent_id)
    return clear_all_autonomous_agents()


@autonomous_router.get("/proposals/latest")
def get_latest_autonomous_proposal(orchestrator_session_id: str) -> Dict[str, Any]:
    from trade_integrations.autonomous_agents.store import load_latest_proposal_for_orchestrator

    proposal = load_latest_proposal_for_orchestrator(orchestrator_session_id)
    if proposal is None:
        return {"status": "not_found", "proposal": None}
    return {"status": "ok", "proposal": proposal}


@autonomous_router.get("/{agent_id}")
def get_autonomous_agent(agent_id: str) -> Dict[str, Any]:
    from trade_integrations.autonomous_agents.runtime_status import enrich_agent
    from trade_integrations.autonomous_agents.store import get_agent

    agent = get_agent(agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="agent not found")
    return enrich_agent(agent)


@autonomous_router.post("/commit")
def commit_autonomous_agent_route(body: CommitAutonomousAgentRequest) -> Dict[str, Any]:
    from trade_integrations.autonomous_agents.proposals import commit_autonomous_agent
    from src.scheduled_research.autonomous_agent_jobs import register_agent_jobs

    svc = _session_service()
    if svc is None:
        raise HTTPException(status_code=503, detail="session runtime not enabled")
    try:
        result = commit_autonomous_agent(
            proposal_id=body.proposal_id,
            consent_ack=body.consent_ack,
            session_service=svc,
            orchestrator_session_id=body.session_id,
        )
        register_agent_jobs(result["agent"])
        agent = result.get("agent") or {}
        if (
            not result.get("already_committed")
            and agent.get("id")
            and not result.get("infra_paused")
        ):
            from src.scheduled_research.autonomous_bootstrap import schedule_agent_bootstrap

            schedule_agent_bootstrap(str(agent["id"]))
        committed_payload = {
            "agent_id": agent.get("id"),
            "vibe_session_id": result.get("vibe_session_id"),
            "name": agent.get("name"),
        }
        orch_sid = body.session_id or agent.get("orchestrator_session_id")
        if orch_sid:
            svc.event_bus.emit(str(orch_sid), "autonomous_agent.committed", committed_payload)
        vibe_sid = result.get("vibe_session_id")
        if vibe_sid and str(vibe_sid) != str(orch_sid or ""):
            svc.event_bus.emit(str(vibe_sid), "autonomous_agent.committed", committed_payload)
        return result
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@autonomous_router.post("/orchestrator/session", response_model=OrchestratorSessionResponse)
def get_or_create_orchestrator_session() -> OrchestratorSessionResponse:
    from trade_integrations.autonomous_agents.store import (
        get_active_orchestrator_session_id,
        set_active_orchestrator_session_id,
    )
    from trade_integrations.autonomous_agents.turns import build_orchestrator_system_note

    svc = _session_service()
    if svc is None:
        raise HTTPException(status_code=503, detail="session runtime not enabled")

    from src.session.orchestrator_profile import is_orchestrator_session

    active_sid = get_active_orchestrator_session_id()
    if active_sid:
        existing = svc.get_session(active_sid)
        if existing is not None and is_orchestrator_session(existing.config):
            return OrchestratorSessionResponse(session_id=existing.session_id, title=existing.title)

    session = svc.create_session(
        title="autonomous:orchestrator",
        config={
            "session_kind": "autonomous_orchestrator",
            "orchestrator": True,
            "system_note": build_orchestrator_system_note(),
        },
    )
    set_active_orchestrator_session_id(session.session_id)
    return OrchestratorSessionResponse(session_id=session.session_id, title=session.title)


class PlanApprovalRequest(BaseModel):
    note: Optional[str] = None


@autonomous_router.post("/{agent_id}/approve-plan")
def approve_plan_route(agent_id: str) -> Dict[str, Any]:
    from trade_integrations.autonomous_agents.plan_approval import approve_agent_plan
    from trade_integrations.autonomous_agents.runtime_status import enrich_agent

    result = approve_agent_plan(agent_id)
    if result.get("status") == "error":
        raise HTTPException(status_code=400, detail=result.get("error"))
    agent = enrich_agent(result.get("agent") or {})
    svc = _session_service()
    if svc and agent.get("vibe_session_id"):
        svc.event_bus.emit(
            str(agent["vibe_session_id"]),
            "autonomous_agent.plan_approved",
            {"agent_id": agent_id, "plan_approved_at": result.get("plan_approved_at")},
        )
    return {"status": "ok", "agent": agent}


@autonomous_router.post("/{agent_id}/reject-plan")
def reject_plan_route(agent_id: str, body: PlanApprovalRequest) -> Dict[str, Any]:
    from trade_integrations.autonomous_agents.plan_approval import reject_agent_plan
    from trade_integrations.autonomous_agents.runtime_status import enrich_agent

    result = reject_agent_plan(agent_id, note=body.note or "")
    if result.get("status") == "error":
        raise HTTPException(status_code=400, detail=result.get("error"))
    return {"status": "ok", "agent": enrich_agent(result.get("agent") or {})}


@autonomous_router.post("/{agent_id}/pause")
def pause_agent(agent_id: str) -> Dict[str, Any]:
    from trade_integrations.autonomous_agents.proposals import pause_autonomous_agent
    from src.scheduled_research.autonomous_agent_jobs import unregister_agent_jobs

    try:
        unregister_agent_jobs(agent_id)
        return pause_autonomous_agent(agent_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@autonomous_router.post("/{agent_id}/resume")
def resume_agent(agent_id: str) -> Dict[str, Any]:
    from trade_integrations.autonomous_agents.proposals import resume_autonomous_agent
    from trade_integrations.autonomous_agents.store import get_agent
    from src.scheduled_research.autonomous_agent_jobs import register_agent_jobs

    try:
        result = resume_autonomous_agent(agent_id)
        agent = get_agent(agent_id)
        if agent:
            register_agent_jobs(agent)
        return result
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@autonomous_router.post("/{agent_id}/stop")
def stop_agent(agent_id: str) -> Dict[str, Any]:
    from trade_integrations.autonomous_agents.proposals import stop_autonomous_agent
    from src.scheduled_research.autonomous_agent_jobs import unregister_agent_jobs

    try:
        unregister_agent_jobs(agent_id)
        return stop_autonomous_agent(agent_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@autonomous_router.delete("/{agent_id}")
def delete_agent_route(agent_id: str) -> Dict[str, Any]:
    from trade_integrations.autonomous_agents.proposals import delete_autonomous_agent
    from src.scheduled_research.autonomous_agent_jobs import unregister_agent_jobs

    try:
        unregister_agent_jobs(agent_id)
        return delete_autonomous_agent(agent_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
