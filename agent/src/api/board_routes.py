"""HTTP routes for the dual-board advisory/agent UI
(2026-08-25-dual-board-advisory-agent-ui) — Board 2 (Agent): the recommendation delivered to
the autonomous agent alongside what it actually did, shadow-track P&L, multi-candidate
hindsight comparison, and the weight-change-vs-performance timeline.

Deliberately a read-only display layer over `autonomous_agents.shadow_pnl`/`shadow_strategy`/
`model_version_timeline` and `weight_model` — it doesn't itself decide or execute anything, per
the backlog item's own scope. Same pattern as `autonomous_routes.py`: deferred imports of
`trade_integrations.*` inside each handler body, plain dict returns, 404 via `HTTPException`
for an unknown agent.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException

board_router = APIRouter(prefix="/board", tags=["board"])


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
