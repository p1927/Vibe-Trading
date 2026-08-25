"""HTTP route for the live-positions forecast board
(2026-08-25-live-positions-forecast-band-board) — a human-facing view of an already-open
agent position's live POP re-run and day-by-day `pnl_forecast_band`, split out of
[[2026-08-25-wire-pnl-forecast-band-into-consumers]] since this data was LLM-prompt-only
before this item (`strategy_progress.py` -> `turns.py`/`context_prefetch.py`), with zero
FastAPI/frontend exposure. Deliberately a read-only display layer over
`live_pop.compute_live_pop_for_agent` — it doesn't decide or trigger anything, same boundary
that function's own module docstring states. Same pattern as `board_routes.py`/
`advisory_routes.py`: deferred imports inside the handler body, plain dict return.
"""

from __future__ import annotations

import logging
from typing import Any, Dict

from fastapi import APIRouter, HTTPException

logger = logging.getLogger(__name__)

positions_router = APIRouter(prefix="/board/positions", tags=["board"])


@positions_router.get("/{agent_id}")
def get_agent_live_positions(agent_id: str) -> Dict[str, Any]:
    """Live POP re-run + day-by-day p10/p50/p90 P&L forecast band per open-position group."""
    from trade_integrations.autonomous_agents.store import get_agent

    if not get_agent(agent_id):
        raise HTTPException(status_code=404, detail="agent not found")

    from nautilus_openalgo_bridge.live_pop import compute_live_pop_for_agent

    try:
        return compute_live_pop_for_agent(agent_id)
    except Exception as exc:  # noqa: BLE001 — never leak a bare 500/no-CORS crash to the browser
        # `compute_live_pop_for_agent` calls the broker (OpenAlgo positionbook/Greeks) with
        # no internal fallback of its own — `strategy_progress.py`'s LLM-prompt caller
        # already wraps this same call in a best-effort `except Exception: live_pop = {}`;
        # this HTTP surface needs the equivalent translation to a real error response
        # (matching `india_options_routes.py`'s 502-for-broker-failure convention) instead
        # of an unhandled exception, which bypasses CORSMiddleware entirely and shows the
        # browser a bare connection failure with no diagnosable message.
        logger.exception("compute_live_pop_for_agent failed (agent_id=%s)", agent_id)
        raise HTTPException(status_code=502, detail=f"live positions request failed: {exc}") from exc
