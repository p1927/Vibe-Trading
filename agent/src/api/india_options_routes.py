"""Fork-only India options routes/dispatch — sidecar for ``options_routes.py``.

``options_routes.py`` is an upstream-owned file (inherited from
``HKUDS/Vibe-Trading``'s Options Lab feature). Per this repo's fork
conventions (``docs/FORK_CONVENTIONS.md``), fork-only behavior belongs in a
file the fork owns, with at most a single import + call site left in the
upstream file, so future upstream syncs to ``options_routes.py`` stay close
to a fast-forward instead of conflicting with India-specific logic woven
into its route bodies.

Exposes:

- ``fetch_india_chain(...)`` — the whole ``market="india_equity"`` branch of
  ``GET /options/chain``, called with one line from ``options_chain()``.
- ``register_india_options_routes(app, require_auth)`` — registers
  ``GET /options/research`` (the ranked-strategies endpoint, entirely new
  and fork-only), called once from ``register_options_routes()``.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from fastapi import Depends, FastAPI, Query
from fastapi.responses import JSONResponse, Response

from src.tools.india_options_chain_tool import IndiaOptionsChainTool, list_india_underlyings
from src.tools.india_options_research_tool import IndiaOptionsResearchTool

logger = logging.getLogger(__name__)

AuthDep = Callable[..., Awaitable[Any] | Any]

# The options_research pipeline (aggregator.py) runs several sequential
# external-data stages (chain, events via nselib, analytics via yfinance/
# qfinindia) — the chain stage has its own bounded timeout, but the others
# call vendor libraries with no explicit HTTP timeout at all, so a slow/
# unreachable vendor there stalls the whole pipeline with no ceiling. Rather
# than auditing/patching every vendor call individually, bound the entire
# request here: a caller always gets an explicit response within this
# ceiling regardless of which internal stage is stuck.
#
# 180s, not something tighter: a real successful run (all stages reachable,
# no vendor slowness) measured at 156.5s end to end — the non-chain stages
# dominate, not the chain fetch (~3s alone). A shorter ceiling would kill
# legitimate slow-but-working requests, not just genuine hangs.
_RESEARCH_TIMEOUT_S = 180.0


async def fetch_india_chain(
    ticker: str, expiration: int | None, source: str | None
) -> Response:
    """Run ``IndiaOptionsChainTool`` and return its envelope as an HTTP
    response — same error-surface contract as the US chain branch this
    replaces in ``options_chain()``."""
    kwargs: dict[str, Any] = {"ticker": ticker}
    if expiration is not None:
        kwargs["expiry_date"] = datetime.fromtimestamp(
            expiration, tz=timezone.utc
        ).strftime("%Y-%m-%d")
    if source:
        kwargs["source"] = source

    try:
        raw = await asyncio.to_thread(IndiaOptionsChainTool().execute, **kwargs)
        envelope = json.loads(raw)
    except Exception:  # noqa: BLE001 — never leak a stack frame to clients
        logger.exception("india options chain tool call failed (ticker=%s)", ticker)
        return JSONResponse(
            status_code=502,
            content={"ok": False, "error": "options chain request failed"},
        )
    if not envelope.get("ok"):
        return JSONResponse(status_code=502, content=envelope)
    return envelope


def register_india_options_routes(app: FastAPI, require_auth: AuthDep) -> None:
    """Mount ``GET /options/research`` and ``GET /options/india/underlyings``."""

    @app.get("/options/india/underlyings", dependencies=[Depends(require_auth)])
    async def india_underlyings(source: str = Query("stock_simulator")) -> Response:
        """Known indexes (always) + recorded equities (``stock_history`` only,
        ``null`` for live sources — enumerating those would need an ~80k-row
        scrip-master download per request, not something to do per keystroke)."""
        try:
            data = await asyncio.to_thread(list_india_underlyings, source)
        except Exception:  # noqa: BLE001 — never leak a stack frame to clients
            logger.exception("india underlyings lookup failed (source=%s)", source)
            return JSONResponse(
                status_code=502,
                content={"ok": False, "error": "underlyings lookup failed"},
            )
        return {"ok": True, "data": data}

    @app.get("/options/research", dependencies=[Depends(require_auth)])
    async def options_research(
        ticker: str | None = Query(None, max_length=64),
        expiry_date: str | None = Query(None, max_length=16),
    ) -> Response:
        """Wrap ``IndiaOptionsResearchTool`` (network I/O, run in a thread)."""
        if ticker is None or not ticker.strip():
            return JSONResponse(
                status_code=400, content={"ok": False, "error": "ticker is required"}
            )
        kwargs: dict[str, Any] = {"ticker": ticker}
        if expiry_date:
            kwargs["expiry_date"] = expiry_date
        try:
            raw = await asyncio.wait_for(
                asyncio.to_thread(IndiaOptionsResearchTool().execute, **kwargs),
                timeout=_RESEARCH_TIMEOUT_S,
            )
            envelope = json.loads(raw)
        except asyncio.TimeoutError:
            # The worker thread itself isn't killed (Python can't force-stop
            # a running thread) — it finishes or keeps retrying in the
            # background and is discarded. The client gets an explicit
            # answer now instead of an indefinite wait.
            logger.warning(
                "options research request exceeded %ss (ticker=%s)",
                _RESEARCH_TIMEOUT_S, ticker,
            )
            return JSONResponse(
                status_code=504,
                content={
                    "ok": False,
                    "error": f"options research timed out after {_RESEARCH_TIMEOUT_S}s",
                },
            )
        except Exception:  # noqa: BLE001 — never leak a stack frame to clients
            logger.exception("options research tool call failed (ticker=%s)", ticker)
            return JSONResponse(
                status_code=502,
                content={"ok": False, "error": "options research request failed"},
            )
        if not envelope.get("ok"):
            return JSONResponse(status_code=502, content=envelope)
        return envelope
