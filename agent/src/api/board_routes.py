"""HTTP routes for the dual-board advisory/agent UI
(2026-08-25-dual-board-advisory-agent-ui) — Board 2 (Agent): the recommendation delivered to
the autonomous agent alongside what it actually did, shadow-track P&L, multi-candidate
hindsight comparison, and the weight-change-vs-performance timeline.

Mostly a read-only display layer over `autonomous_agents.shadow_pnl`/`shadow_strategy`/
`model_version_timeline` and `weight_model` — it doesn't itself decide or execute anything, per
the backlog item's own scope. Same pattern as `autonomous_routes.py`: deferred imports of
`trade_integrations.*` inside each handler body, plain dict returns, 404 via `HTTPException`
for an unknown agent.

The one exception is `apply_weight_proposal` below (2026-08-25-weight-model-proposals-no-
review-surface): `weight_model.propose_weight_adjustment` can generate real pending proposals
(e.g. from `options_research.self_learning.evaluate_options_pop_drift`, wired into the morning
calibration job) but until this route existed there was no way for a human to ever see or act
on one except a Python shell — a proposal nobody can see is equivalent to no proposal at all.
This is a real write path (it changes a live weight via `weight_model.store.set_weight`), gated
the same way the module itself already gates it: `apply_pending_weight_proposal` never auto-
applies, only promotes a proposal a human explicitly clicked "Apply" on, and re-validates the
proposal's bounds before writing — this route adds no additional confirmation of its own
because clicking a named "Apply" button on a specific proposal already *is* the explicit human
action the module's whole design requires.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

board_router = APIRouter(prefix="/board", tags=["board"])


class RejectWeightProposalRequest(BaseModel):
    reason: str


class RevertWeightProposalRequest(BaseModel):
    reason: str


class RejectRetrainProposalRequest(BaseModel):
    reason: str


def _require_agent(agent_id: str) -> None:
    from trade_integrations.autonomous_agents.store import get_agent

    if not get_agent(agent_id):
        raise HTTPException(status_code=404, detail="agent not found")


@board_router.get("/agent/{agent_id}/summary")
def get_agent_board_summary(agent_id: str) -> Dict[str, Any]:
    """Agent-actual vs. shadow-track P&L totals, plus the choice-alignment rate against
    the prediction ledger — the top-level "how is this agent doing" card."""
    from trade_integrations.autonomous_agents.shadow_pnl import shadow_vs_actual_summary
    from trade_integrations.autonomous_agents.shadow_strategy import shadow_divergence_summary

    _require_agent(agent_id)
    return {
        "agent_id": agent_id,
        "pnl_summary": shadow_vs_actual_summary(agent_id),
        "alignment": shadow_divergence_summary(agent_id),
    }


@board_router.get("/agent/{agent_id}/wealth-curve")
def get_agent_wealth_curve(agent_id: str, pricing_method: Optional[str] = None) -> Dict[str, Any]:
    """Cumulative agent-actual vs. shadow-track P&L over time, one point per closed trade."""
    from trade_integrations.autonomous_agents.shadow_pnl import wealth_curve

    _require_agent(agent_id)
    return {"agent_id": agent_id, "points": wealth_curve(agent_id, pricing_method=pricing_method)}


@board_router.get("/agent/{agent_id}/hindsight")
def get_agent_hindsight_summary(agent_id: str) -> Dict[str, Any]:
    """Multi-candidate hindsight comparison: per-candidate-rank totals, which rank did
    best, and the factor-level attribution findings explaining why."""
    from trade_integrations.autonomous_agents.shadow_pnl import multi_candidate_hindsight_summary

    _require_agent(agent_id)
    return multi_candidate_hindsight_summary(agent_id)


@board_router.get("/agent/{agent_id}/hindsight-curves")
def get_agent_hindsight_curves(agent_id: str) -> Dict[str, Any]:
    """One cumulative-P&L line per candidate rank — the "graph with multiple lines"
    view of which not-picked alternative would have done better."""
    from trade_integrations.autonomous_agents.shadow_pnl import multi_candidate_wealth_curves

    _require_agent(agent_id)
    return {"agent_id": agent_id, "curves": multi_candidate_wealth_curves(agent_id)}


@board_router.get("/model-version-timeline")
def get_model_version_timeline(
    agent_id: Optional[str] = None,
    weight_id: Optional[str] = None,
    window_days: int = 14,
) -> Dict[str, Any]:
    """Every applied weight-model change, each with real closed-trade win-rate/expectancy
    before vs. after — did a recalibration actually help, not just "was it applied"."""
    from trade_integrations.autonomous_agents.model_version_timeline import (
        model_version_performance_timeline,
    )

    if agent_id:
        _require_agent(agent_id)
    timeline = model_version_performance_timeline(
        agent_id=agent_id, weight_id=weight_id, window_days=window_days
    )
    return {"timeline": timeline}


@board_router.get("/weight-proposals")
def get_pending_weight_proposals(weight_id: Optional[str] = None) -> Dict[str, Any]:
    """Every pending `weight_model` proposal awaiting human review — the counterpart to
    `/model-version-timeline`'s already-applied history."""
    from trade_integrations.weight_model import list_pending_weight_proposals

    return {"proposals": list_pending_weight_proposals(weight_id=weight_id)}


@board_router.get("/weight-proposals/applied")
def get_applied_weight_proposals(weight_id: Optional[str] = None) -> Dict[str, Any]:
    """Every applied `weight_model` proposal — what a "Revert" action in the UI needs to
    list, since only an applied proposal can be reverted."""
    from trade_integrations.weight_model import list_applied_weight_proposals

    return {"proposals": list_applied_weight_proposals(weight_id=weight_id)}


@board_router.post("/weight-proposals/{proposal_id}/apply")
def apply_weight_proposal(proposal_id: str) -> Dict[str, Any]:
    """Promote one pending proposal into the live weight store. See module docstring for
    why no extra confirmation is added here beyond `apply_pending_weight_proposal`'s own
    bounds re-validation."""
    from trade_integrations.weight_model import apply_pending_weight_proposal

    result = apply_pending_weight_proposal(proposal_id)
    if result.get("status") == "error":
        raise HTTPException(status_code=400, detail=result.get("error") or "apply failed")
    return result


@board_router.post("/weight-proposals/{proposal_id}/reject")
def reject_weight_proposal(proposal_id: str, body: RejectWeightProposalRequest) -> Dict[str, Any]:
    """Mark one pending proposal rejected — the "no, this is wrong" counterpart to
    `apply_weight_proposal`. Never touches the live weight store; only records the decision
    and reason so the proposal doesn't sit in the pending queue forever."""
    from trade_integrations.weight_model import reject_pending_weight_proposal

    result = reject_pending_weight_proposal(proposal_id, body.reason)
    if result.get("status") == "error":
        raise HTTPException(status_code=400, detail=result.get("error") or "reject failed")
    return result


@board_router.post("/weight-proposals/{proposal_id}/revert")
def revert_weight_proposal(proposal_id: str, body: RevertWeightProposalRequest) -> Dict[str, Any]:
    """Undo one already-applied proposal, writing the weight back to its pre-apply value.
    Same no-extra-confirmation reasoning as `apply_weight_proposal`: a named "Revert" click on
    a specific proposal already is the explicit human action `revert_applied_weight_proposal`
    requires; it separately refuses if the live value has since moved on."""
    from trade_integrations.weight_model import revert_applied_weight_proposal

    result = revert_applied_weight_proposal(proposal_id, body.reason)
    if result.get("status") == "error":
        raise HTTPException(status_code=400, detail=result.get("error") or "revert failed")
    return result


@board_router.get("/retrain-proposals")
def get_pending_retrain_proposals() -> Dict[str, Any]:
    """Every pending index-calibrator retrain proposal awaiting human review — the
    counterpart to `/weight-proposals` but for the Ridge model artifact rather than a scalar
    weight. See `retrain_proposals.py`'s module docstring for why this is a separate queue."""
    from trade_integrations.dataflows.index_research.retrain_proposals import (
        list_pending_retrain_proposals,
    )

    return {"proposals": list_pending_retrain_proposals()}


@board_router.get("/retrain-proposals/applied")
def get_applied_retrain_proposals() -> Dict[str, Any]:
    """Every applied index-calibrator retrain proposal."""
    from trade_integrations.dataflows.index_research.retrain_proposals import (
        list_applied_retrain_proposals,
    )

    return {"proposals": list_applied_retrain_proposals()}


@board_router.post("/retrain-proposals/{proposal_id}/apply")
def apply_retrain_proposal(proposal_id: str) -> Dict[str, Any]:
    """Promote one pending retrain proposal's candidate artifact into the live model store.
    Same no-extra-confirmation reasoning as `apply_weight_proposal`."""
    from trade_integrations.dataflows.index_research.retrain_proposals import (
        apply_pending_retrain_proposal,
    )

    result = apply_pending_retrain_proposal(proposal_id)
    if result.get("status") == "error":
        raise HTTPException(status_code=400, detail=result.get("error") or "apply failed")
    return result


@board_router.post("/retrain-proposals/{proposal_id}/reject")
def reject_retrain_proposal(proposal_id: str, body: RejectRetrainProposalRequest) -> Dict[str, Any]:
    """Mark one pending retrain proposal rejected. Never touches the live model artifact."""
    from trade_integrations.dataflows.index_research.retrain_proposals import (
        reject_pending_retrain_proposal,
    )

    result = reject_pending_retrain_proposal(proposal_id, body.reason)
    if result.get("status") == "error":
        raise HTTPException(status_code=400, detail=result.get("error") or "reject failed")
    return result


def _weight_activity_entry(p: Dict[str, Any]) -> Dict[str, Any]:
    resolved_at = p.get("applied_at") or p.get("rejected_at") or p.get("reverted_at")
    reason = p.get("rationale")
    if p.get("status") == "rejected":
        reason = p.get("reject_reason") or reason
    elif p.get("status") == "reverted":
        reason = p.get("revert_reason") or reason
    return {
        "track": "weight_model",
        "id": p["id"],
        "status": p.get("status"),
        "created_at": p.get("created_at"),
        "resolved_at": resolved_at,
        "summary": f"{p.get('weight_id')}: {p.get('current_value')} → {p.get('proposed_value')}",
        "reason": reason,
    }


def _retrain_activity_entry(p: Dict[str, Any]) -> Dict[str, Any]:
    resolved_at = p.get("applied_at") or p.get("rejected_at")
    reason = p.get("reason")
    if p.get("status") == "rejected":
        reason = p.get("reject_reason") or reason
    diff = p.get("diff") or {}
    prev_mae = diff.get("previous_mae")
    prev_mae_str = f"{prev_mae:.4f}" if isinstance(prev_mae, (int, float)) else "—"
    cand_mae = diff.get("candidate_mae")
    cand_mae_str = f"{cand_mae:.4f}" if isinstance(cand_mae, (int, float)) else "—"
    return {
        "track": "index_calibrator",
        "id": p["id"],
        "status": p.get("status"),
        "created_at": p.get("created_at"),
        "resolved_at": resolved_at,
        "summary": f"NIFTY Ridge retrain: MAE {prev_mae_str} → {cand_mae_str}",
        "reason": reason,
    }


@board_router.get("/learning-activity")
def get_learning_activity() -> Dict[str, Any]:
    """Every learning-mechanism proposal — pending, applied, rejected, or reverted — across
    both `weight_model` and the index calibrator's retrain queue, in one timeline. Read-only
    aggregation over each track's own list_* accessors; doesn't itself decide or execute
    anything, same as the rest of this module. See
    [[2026-08-27-unified-learning-review-dashboard]]: before this route, a human wanting to
    "monitor what it's learning" had to check `weight_model`'s pending list, its applied
    history, and (once it existed) the calibrator's retrain-proposal queue separately, with no
    single place to see rejected/reverted entries at all."""
    from trade_integrations.weight_model import (
        list_applied_weight_proposals,
        list_pending_weight_proposals,
        list_rejected_weight_proposals,
        list_reverted_weight_proposals,
    )
    from trade_integrations.dataflows.index_research.retrain_proposals import (
        list_applied_retrain_proposals,
        list_pending_retrain_proposals,
        list_rejected_retrain_proposals,
    )

    entries = [
        _weight_activity_entry(p)
        for p in (
            list_pending_weight_proposals()
            + list_applied_weight_proposals()
            + list_rejected_weight_proposals()
            + list_reverted_weight_proposals()
        )
    ] + [
        _retrain_activity_entry(p)
        for p in (
            list_pending_retrain_proposals()
            + list_applied_retrain_proposals()
            + list_rejected_retrain_proposals()
        )
    ]
    entries.sort(key=lambda e: e.get("created_at") or "", reverse=True)
    return {"entries": entries}
