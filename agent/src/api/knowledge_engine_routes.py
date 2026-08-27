"""Human-facing HTTP routes for module 8, the knowledge engine
(.claude/backlog/items/2026-08-27-knowledge-engine-no-human-facing-surface.md).

Until now `trade_integrations.knowledge_engine.query` was reachable only
through the agent-facing `knowledge_engine_tool.py` (MCP tool surface) — no
human could browse the wiki, strategy catalog, or news-derived concepts
without going through the agent's own chat/tool-call surface. These routes
are thin read-only wrappers around the same query functions the tool calls,
so results are identical to what the agent sees.

New standalone file (not nested under an existing routes module) since this
isn't options- or execution-specific — same "new sidecar per surface"
convention as `execution_advisor_routes.py`. Registered directly from
`api_server.py`.

Routes (auth via the caller-supplied ``require_auth`` dependency):

- ``GET /knowledge/wiki``               — search/browse wiki entries (metadata only)
- ``GET /knowledge/wiki/{slug}``        — one wiki page's full content
- ``GET /knowledge/strategies``         — strategy catalog, filterable/rankable
- ``GET /knowledge/news-derived-strategies`` — verified news-derived concepts

Error surface: an unexpected exception inside a query call → 502 with a
generic envelope (never leaks a stack frame). A missing wiki slug → 200 with
``{"found": false}`` (matches ``get_wiki_page``'s own not-found shape, not a
404 — the query layer treats "not found" as a normal result, not an error).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable

from fastapi import Depends, FastAPI, Query
from fastapi.responses import JSONResponse, Response

logger = logging.getLogger(__name__)

AuthDep = Callable[..., Awaitable[Any] | Any]


def _split_tags(tags: str | None) -> list[str] | None:
    if not tags:
        return None
    return [t.strip() for t in tags.split(",") if t.strip()] or None


def register_knowledge_engine_routes(app: FastAPI, require_auth: AuthDep | None = None) -> None:
    """Mount the knowledge-engine read routes onto ``app``.

    Args:
        app: The host FastAPI app.
        require_auth: Header-auth dependency for every endpoint.

    For backwards compatibility, when the dependency callable is not passed
    explicitly we resolve it from the host ``api_server`` module via
    ``sys.modules`` — same convention as ``register_options_routes``/
    ``register_execution_advisor_routes``.
    """
    if require_auth is None:
        import sys as _sys

        host = _sys.modules.get("api_server") or _sys.modules.get("agent.api_server")
        if host is None:  # pragma: no cover — only triggers on weird import setups
            raise RuntimeError(
                "register_knowledge_engine_routes: api_server module not in sys.modules; "
                "pass require_auth explicitly"
            )
        require_auth = host.require_auth

    @app.get("/knowledge/wiki", dependencies=[Depends(require_auth)])
    async def knowledge_wiki(
        text: str | None = Query(None, max_length=200),
        tags: str | None = Query(None, description="Comma-separated tags"),
        wiki_type: str | None = Query(None, max_length=64),
        limit: int = Query(10, ge=1, le=50),
    ) -> Response:
        try:
            from trade_integrations.knowledge_engine.query import query_wiki

            results = await asyncio.to_thread(
                query_wiki, text=text, tags=_split_tags(tags), wiki_type=wiki_type, limit=limit
            )
        except Exception:  # noqa: BLE001 — never leak a stack frame to clients
            logger.exception("knowledge wiki query failed")
            return JSONResponse(status_code=502, content={"ok": False, "error": "wiki query unavailable"})
        return {"ok": True, "count": len(results), "results": results}

    @app.get("/knowledge/wiki/{slug:path}", dependencies=[Depends(require_auth)])
    async def knowledge_wiki_page(slug: str) -> Response:
        try:
            from trade_integrations.knowledge_engine.query import get_wiki_page

            page = await asyncio.to_thread(get_wiki_page, slug)
        except Exception:  # noqa: BLE001 — never leak a stack frame to clients
            logger.exception("knowledge wiki page fetch failed (slug=%s)", slug)
            return JSONResponse(status_code=502, content={"ok": False, "error": "wiki page unavailable"})
        return {"ok": True, **page}

    @app.get("/knowledge/strategies", dependencies=[Depends(require_auth)])
    async def knowledge_strategies(
        market_view: str | None = Query(None, max_length=64),
        risk_profile: str | None = Query(None, max_length=64),
        horizon: str | None = Query(None, max_length=64),
        tags: str | None = Query(None, description="Comma-separated tags"),
        limit: int = Query(5, ge=1, le=50),
    ) -> Response:
        try:
            from trade_integrations.knowledge_engine.query import query_strategies

            results = await asyncio.to_thread(
                query_strategies,
                market_view=market_view,
                risk_profile=risk_profile,
                horizon=horizon,
                tags=_split_tags(tags),
                limit=limit,
            )
        except Exception:  # noqa: BLE001 — never leak a stack frame to clients
            logger.exception("knowledge strategies query failed")
            return JSONResponse(status_code=502, content={"ok": False, "error": "strategy catalog unavailable"})
        return {"ok": True, "count": len(results), "results": results}

    @app.get("/knowledge/news-derived-strategies", dependencies=[Depends(require_auth)])
    async def knowledge_news_derived_strategies(
        text: str | None = Query(None, max_length=200),
        tags: str | None = Query(None, description="Comma-separated tags"),
        limit: int = Query(5, ge=1, le=50),
    ) -> Response:
        try:
            from trade_integrations.knowledge_engine.query import query_news_derived_strategies

            results = await asyncio.to_thread(
                query_news_derived_strategies, text=text, tags=_split_tags(tags), limit=limit
            )
        except Exception:  # noqa: BLE001 — never leak a stack frame to clients
            logger.exception("knowledge news-derived-strategies query failed")
            return JSONResponse(
                status_code=502, content={"ok": False, "error": "news-derived strategies unavailable"}
            )
        return {"ok": True, "count": len(results), "results": results}
