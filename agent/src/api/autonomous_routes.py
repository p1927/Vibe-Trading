"""HTTP routes for autonomous trading agents."""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from src.api.security import require_local_or_auth

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


class DraftAgentResponse(BaseModel):
    agent_id: str
    session_id: str
    agent: Dict[str, Any]


@autonomous_router.get("")
def list_autonomous_agents() -> Dict[str, Any]:
    from trade_integrations.autonomous_agents.infra_startup import maybe_heal_infra_paused_agents
    from trade_integrations.autonomous_agents.agent_status import load_openalgo_authority
    from trade_integrations.autonomous_agents.runtime_status import build_stack_health, enrich_agent
    from trade_integrations.autonomous_agents.store import backfill_orphan_orchestrator_session, list_agents

    svc = _session_service()
    if svc is not None:
        try:
            backfill_orphan_orchestrator_session(session_service=svc)
        except Exception:
            logger.debug("orchestrator backfill failed", exc_info=True)

    try:
        from src.scheduled_research.autonomous_bootstrap import (
            resume_stale_pending_bootstraps,
            resume_stale_running_bootstraps,
        )

        resume_stale_pending_bootstraps()
    except Exception:
        logger.debug("stale pending bootstrap resume failed", exc_info=True)
    try:
        from src.scheduled_research.autonomous_bootstrap import resume_stale_running_bootstraps

        resume_stale_running_bootstraps()
    except Exception:
        logger.debug("stale running bootstrap resume failed", exc_info=True)
    try:
        from trade_integrations.autonomous_agents.recovery import run_autonomous_agent_recovery

        run_autonomous_agent_recovery()
    except Exception:
        logger.debug("autonomous agent recovery failed", exc_info=True)

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

    shared_authority = load_openalgo_authority(agent=None)
    agents = [enrich_agent(a, authority=shared_authority) for a in list_agents()]
    return {"agents": agents, "stack_health": build_stack_health(authority=shared_authority)}


@autonomous_router.get("/stack-health")
def autonomous_stack_health() -> Dict[str, Any]:
    from trade_integrations.autonomous_agents.runtime_status import build_stack_health

    return build_stack_health()


@autonomous_router.post("/clear-all")
def clear_all_agents_route(
    _auth: None = Depends(require_local_or_auth),
) -> Dict[str, Any]:
    from trade_integrations.autonomous_agents.proposals import clear_all_autonomous_agents
    from trade_integrations.autonomous_agents.scheduler_cleanup import remove_agent_scheduler_jobs
    from trade_integrations.autonomous_agents.store import list_agents

    for agent in list_agents():
        agent_id = str(agent.get("id") or "").strip()
        if agent_id:
            remove_agent_scheduler_jobs(agent_id)
    svc = _session_service()
    return clear_all_autonomous_agents(session_service=svc)


@autonomous_router.get("/proposals/latest")
def get_latest_autonomous_proposal(orchestrator_session_id: str) -> Dict[str, Any]:
    from trade_integrations.autonomous_agents.store import load_latest_proposal_for_orchestrator

    proposal = load_latest_proposal_for_orchestrator(orchestrator_session_id)
    if proposal is None:
        return {"status": "not_found", "proposal": None}
    return {"status": "ok", "proposal": proposal}


@autonomous_router.get("/drafts")
def drafts_get_not_allowed() -> Dict[str, Any]:
    raise HTTPException(
        status_code=405,
        detail={"error": "method_not_allowed", "message": "Use POST /autonomous-agents/drafts to create a draft agent"},
    )


@autonomous_router.post("/drafts", response_model=DraftAgentResponse)
def create_draft_agent_route(
    _auth: None = Depends(require_local_or_auth),
) -> DraftAgentResponse:
    from trade_integrations.autonomous_agents.store import backfill_orphan_orchestrator_session, create_draft_agent

    svc = _session_service()
    if svc is None:
        raise HTTPException(status_code=503, detail="session runtime not enabled")
    try:
        backfilled = backfill_orphan_orchestrator_session(session_service=svc)
    except Exception:
        logger.debug("orchestrator backfill failed", exc_info=True)
        backfilled = None
    if backfilled and backfilled.get("agent"):
        agent = backfilled["agent"]
        return DraftAgentResponse(
            agent_id=str(backfilled.get("agent_id") or agent.get("id") or ""),
            session_id=str(backfilled.get("session_id") or agent.get("vibe_session_id") or ""),
            agent=agent,
        )
    result = create_draft_agent(session_service=svc)
    return DraftAgentResponse(
        agent_id=str(result["agent_id"]),
        session_id=str(result["session_id"]),
        agent=result["agent"],
    )


@autonomous_router.get("/commit")
def commit_get_not_allowed() -> Dict[str, Any]:
    raise HTTPException(
        status_code=405,
        detail={"error": "method_not_allowed", "message": "Use POST /autonomous-agents/commit to activate a draft agent"},
    )


@autonomous_router.get("/{agent_id}")
def get_autonomous_agent(agent_id: str) -> Dict[str, Any]:
    from trade_integrations.autonomous_agents.runtime_status import enrich_agent
    from trade_integrations.autonomous_agents.store import get_agent

    agent = get_agent(agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="agent not found")
    return enrich_agent(agent)


@autonomous_router.post("/commit")
def commit_autonomous_agent_route(
    body: CommitAutonomousAgentRequest,
    _auth: None = Depends(require_local_or_auth),
) -> Dict[str, Any]:
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
            and str(agent.get("status") or "") != "draft"
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
def get_or_create_orchestrator_session(
    _auth: None = Depends(require_local_or_auth),
) -> OrchestratorSessionResponse:
    """Legacy alias — returns backfilled orphan draft or creates a fresh draft agent + session."""
    from trade_integrations.autonomous_agents.store import backfill_orphan_orchestrator_session, create_draft_agent

    svc = _session_service()
    if svc is None:
        raise HTTPException(status_code=503, detail="session runtime not enabled")

    try:
        backfilled = backfill_orphan_orchestrator_session(session_service=svc)
    except Exception:
        logger.debug("orchestrator backfill failed", exc_info=True)
        backfilled = None
    if backfilled and backfilled.get("session_id"):
        return OrchestratorSessionResponse(
            session_id=str(backfilled["session_id"]),
            title=str((backfilled.get("agent") or {}).get("name") or "Agent draft"),
        )
    result = create_draft_agent(session_service=svc)
    return OrchestratorSessionResponse(
        session_id=str(result["session_id"]),
        title=str((result.get("agent") or {}).get("name") or "New agent draft"),
    )


class PlanApprovalRequest(BaseModel):
    note: Optional[str] = None
    widget_id: Optional[str] = None


@autonomous_router.post("/{agent_id}/approve-plan")
def approve_plan_route(agent_id: str, body: PlanApprovalRequest | None = None) -> Dict[str, Any]:
    from trade_integrations.autonomous_agents.plan_approval import approve_agent_plan
    from trade_integrations.autonomous_agents.runtime_status import enrich_agent

    widget_id = (body.widget_id if body else None) or None
    result = approve_agent_plan(agent_id, widget_id=widget_id)
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
def pause_agent(
    agent_id: str,
    _auth: None = Depends(require_local_or_auth),
) -> Dict[str, Any]:
    from trade_integrations.autonomous_agents.proposals import pause_autonomous_agent
    from trade_integrations.autonomous_agents.scheduler_cleanup import remove_agent_scheduler_jobs

    try:
        remove_agent_scheduler_jobs(agent_id)
        return pause_autonomous_agent(agent_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@autonomous_router.post("/{agent_id}/resume")
def resume_agent(agent_id: str) -> Dict[str, Any]:
    from trade_integrations.autonomous_agents.proposals import resume_autonomous_agent
    from trade_integrations.autonomous_agents.store import get_agent
    from src.scheduled_research.autonomous_agent_jobs import finalize_infra_heal, register_agent_jobs

    try:
        before = get_agent(agent_id)
        was_infra = before is not None and str(before.get("pause_reason") or "") == "infra"
        result = resume_autonomous_agent(agent_id)
        agent = get_agent(agent_id)
        if agent and str(agent.get("status")) == "running":
            bootstrap = str(agent.get("bootstrap_status") or "")
            if was_infra and bootstrap in {"pending", "failed"}:
                finalize_infra_heal(agent_id)
                agent = get_agent(agent_id) or agent
                result = {**result, "agent": agent}
            else:
                register_agent_jobs(agent)
        return result
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@autonomous_router.post("/{agent_id}/stop")
def stop_agent(
    agent_id: str,
    _auth: None = Depends(require_local_or_auth),
) -> Dict[str, Any]:
    from trade_integrations.autonomous_agents.proposals import stop_autonomous_agent
    from trade_integrations.autonomous_agents.scheduler_cleanup import (
        remove_agent_scheduler_jobs,
        remove_obsolete_scheduler_jobs,
    )

    try:
        result = stop_autonomous_agent(agent_id)
        remove_agent_scheduler_jobs(agent_id)
        remove_obsolete_scheduler_jobs()
        return result
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@autonomous_router.delete("/{agent_id}")
def delete_agent_route(
    agent_id: str,
    flatten_positions: bool = False,
    _auth: None = Depends(require_local_or_auth),
) -> Dict[str, Any]:
    from trade_integrations.autonomous_agents.proposals import delete_autonomous_agent
    from trade_integrations.autonomous_agents.scheduler_cleanup import remove_agent_scheduler_jobs
    from trade_integrations.autonomous_agents.teardown import (
        FlattenIncompleteError,
        OpenPositionsConflictError,
        OpenPositionsLookupError,
    )

    svc = _session_service()
    try:
        remove_agent_scheduler_jobs(agent_id)
        return delete_autonomous_agent(
            agent_id,
            session_service=svc,
            flatten_positions=flatten_positions,
        )
    except OpenPositionsConflictError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "open_positions",
                "agent_id": exc.agent_id,
                "count": exc.count,
                "openalgo_count": exc.openalgo_count,
                "alpaca_count": exc.alpaca_count,
            },
        ) from exc
    except OpenPositionsLookupError as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "error": "position_lookup_failed",
                "agent_id": exc.agent_id,
                "message": exc.reason,
            },
        ) from exc
    except FlattenIncompleteError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "flatten_incomplete",
                "agent_id": exc.agent_id,
                "openalgo_remaining": exc.openalgo_remaining,
                "alpaca_remaining": exc.alpaca_remaining,
                "count": exc.openalgo_remaining + exc.alpaca_remaining,
            },
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
