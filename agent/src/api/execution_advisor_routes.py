"""Fork-only execution-advisor routes — module 7 of the
options-profitability-prediction-platform backlog item
(.claude/backlog/items/2026-08-22-realtime-execution-position-advisor.md).

Read-only advisory surface: watches open OpenAlgo sandbox positions against
module 3/6's live forecast confidence and an ATR-based trailing stop, and
recommends hold/tighten_stop/exit — never mutates an order. Real stop-mutation
was investigated and confirmed blocked (no OpenAlgo `edit_position_stops`
equivalent exists, and `order_guard.py` is MCP-remote-tool infrastructure, not
a plain importable gate) — see that backlog item's 2026-08-23 Attempts entry.
This route only ever surfaces `trade_integrations.dataflows.index_research
.execution_advisor`'s recommendations for a UI panel to render.

New standalone file (not nested under ``options_routes.py``/
``india_options_routes.py``) since this isn't options-specific — it advises
on any open sandbox position — and per this repo's fork conventions
(``docs/FORK_CONVENTIONS.md``), new fork-only surfaces get their own sidecar
file rather than growing an existing one. Registered directly from
``api_server.py``, the same way ``register_options_routes``/
``register_alpha_routes`` are.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable

from fastapi import Depends, FastAPI
from fastapi.responses import JSONResponse, Response

logger = logging.getLogger(__name__)

AuthDep = Callable[..., Awaitable[Any] | Any]


def _record_advisories_best_effort(advisories: list[dict[str, Any]]) -> None:
    """Write the served advisories to the append-only ledger.

    Best-effort by design: this route's contract is to return advice, and a hub-storage
    hiccup must not turn a working advisory response into a 502. The failure is logged
    rather than swallowed silently so a persistently failing ledger is still visible.
    """
    try:
        from trade_integrations.dataflows.index_research.execution_advisor_ledger import (
            record_advisories,
        )

        record_advisories(advisories)
    except Exception:  # noqa: BLE001 — advice delivery must not depend on the ledger
        logger.exception("execution advisor advisory ledger write failed")


def register_execution_advisor_routes(app: FastAPI, require_auth: AuthDep | None = None) -> None:
    """Mount ``GET /execution-advisor/positions`` onto ``app``.

    Args:
        app: The host FastAPI app.
        require_auth: Header-auth dependency for the endpoint.

    For backwards compatibility, when the dependency callable is not passed
    explicitly we resolve it from the host ``api_server`` module via
    ``sys.modules`` — same convention as ``register_options_routes``.
    """
    if require_auth is None:
        import sys as _sys

        host = _sys.modules.get("api_server") or _sys.modules.get("agent.api_server")
        if host is None:  # pragma: no cover — only triggers on weird import setups
            raise RuntimeError(
                "register_execution_advisor_routes: api_server module not in sys.modules; "
                "pass require_auth explicitly"
            )
        require_auth = host.require_auth

    @app.get("/execution-advisor/positions", dependencies=[Depends(require_auth)])
    async def execution_advisor_positions() -> Response:
        """Advisory recommendations for every open OpenAlgo sandbox position —
        hold/tighten_stop/exit, grouped by ``strategy_group_id`` (module 9's
        multi-leg strategy tagging) when available. Advisory only; never
        places, closes, or edits an order.
        """
        try:
            from trade_integrations.dataflows.index_research.execution_advisor import (
                advise_positions,
                group_advisories_by_strategy,
            )

            advisories = await asyncio.to_thread(advise_positions)
            grouped = group_advisories_by_strategy(advisories)
            # Persist what was actually served. The advisor itself keeps only a mutable
            # FSM blob that is deleted when a position closes, so without this write no
            # later pass can check an advisory against what the market then did.
            await asyncio.to_thread(_record_advisories_best_effort, advisories)
        except Exception:  # noqa: BLE001 — never leak a stack frame to clients
            logger.exception("execution advisor positions failed")
            return JSONResponse(
                status_code=502, content={"ok": False, "error": "execution advisor unavailable"}
            )

        return {
            "ok": True,
            "count": len(advisories),
            "advisories": advisories,
            "grouped": grouped,
        }
