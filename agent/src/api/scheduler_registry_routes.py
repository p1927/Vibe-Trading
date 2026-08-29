"""Cross-service scheduler-registry aggregation route.

``GET /scheduler-registry`` fans out to every scheduler mechanism the
Scheduler tab wants to show beyond vibetrading's own engine (Mechanism A,
served by ``GET /scheduled-runs`` — unchanged, not duplicated here):
stock_simulator's recorder poll-spec (Mechanism B, read-only) and openalgo's
five independent APScheduler instances (Mechanism C, read/pause/resume). Each
source is queried independently with a short timeout so a down/unreachable
service degrades that source to an empty list plus a `sources.<name>` status
entry, never a failed request — see
.claude/backlog/items/2026-08-29-unified-scheduler-registry.md.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable, Dict

from fastapi import Depends, FastAPI, HTTPException

logger = logging.getLogger(__name__)

AuthDep = Callable[..., Awaitable[Any] | Any]


def _stock_simulator_entries() -> tuple[list[Dict[str, Any]], Dict[str, Any]]:
    """Best-effort fetch of stock_simulator's recorder-category entries.

    Returns ``(entries, source_status)`` — never raises. A configuration
    problem, timeout, or unreachable service all degrade to an empty entry
    list plus a status dict the caller surfaces under ``sources``, so one
    down mechanism never takes the whole registry response down with it.
    """
    try:
        from src.trade.hub_bridge import ensure_trade_stack_path

        ensure_trade_stack_path()
        from trade_integrations.stock_simulator.client import (
            StockSimulatorClient,
            StockSimulatorClientError,
        )

        client = StockSimulatorClient(timeout=3.0)
        if not client.is_configured:
            return [], {"status": "unreachable", "error": "no control token configured"}
        payload = client.list_scheduler_registry()
        entries = list(payload.get("entries") or [])
        for entry in entries:
            # stock_simulator's own DTO can't fill this in — it doesn't know
            # its own externally-reachable host/port — so stamp it in here
            # via the same client that already does (see
            # `StockSimulatorClient.log_stream_url`'s docstring). `section`
            # is always `f"recorder:{recorder_name}"` for a Mechanism-B
            # entry (`scheduler_introspection.list_recorder_categories`).
            if entry.get("supports_live_log") and str(entry.get("section", "")).startswith("recorder:"):
                recorder_name = str(entry["section"])[len("recorder:") :]
                entry["live_log_stream_url"] = client.log_stream_url(recorder_name)
        return entries, {"status": "ok"}
    except StockSimulatorClientError as exc:
        logger.warning("scheduler-registry: stock_simulator unreachable: %s", exc)
        return [], {"status": "unreachable", "error": str(exc)}
    except Exception as exc:  # pragma: no cover - defensive, mirrors client's own catch-all
        logger.exception("scheduler-registry: unexpected error querying stock_simulator")
        return [], {"status": "unreachable", "error": str(exc)}


def _openalgo_entries() -> tuple[list[Dict[str, Any]], Dict[str, Any]]:
    """Best-effort fetch of openalgo's five scheduler instances' entries.

    Returns ``(entries, source_status)`` — never raises. Mirrors
    ``_stock_simulator_entries``'s degrade-don't-fail contract.
    """
    try:
        from trade_integrations.execution.openalgo_client import OpenAlgoClient

        client = OpenAlgoClient()
        entries = client.list_scheduler_registry()
        for entry in entries:
            # openalgo's own DTO can't embed its apikey into a URL from
            # inside a service module — stamp it in here via the same
            # client that already holds it. `section` is the raw scheduler
            # source ("flow"/"historify"/...) for a Mechanism-C entry, and
            # `id` is "C:<source>:<job_id>" — strip that exact prefix to
            # recover the raw job id (see scheduler_registry_service.py's
            # `_job_to_entry`).
            if entry.get("supports_live_log"):
                source = str(entry.get("section", ""))
                prefix = f"C:{source}:"
                entry_id = str(entry.get("id", ""))
                if entry_id.startswith(prefix):
                    job_id = entry_id[len(prefix) :]
                    entry["live_log_stream_url"] = client.log_stream_url(source, job_id)
        return entries, {"status": "ok"}
    except RuntimeError as exc:
        # OpenAlgoClient() raises this when OPENALGO_API_KEY isn't configured.
        logger.warning("scheduler-registry: openalgo not configured: %s", exc)
        return [], {"status": "unreachable", "error": str(exc)}
    except Exception as exc:  # pragma: no cover - defensive, mirrors client's own catch-all
        logger.exception("scheduler-registry: unexpected error querying openalgo")
        return [], {"status": "unreachable", "error": str(exc)}


def register_scheduler_registry_routes(
    app: FastAPI,
    require_auth: AuthDep | None = None,
) -> None:
    """Mount ``GET /scheduler-registry`` onto ``app``.

    Resolves ``require_auth`` from the host ``api_server`` module via
    ``sys.modules`` when not passed explicitly, the same convention
    ``register_scheduled_routes`` uses.
    """
    import sys as _sys

    host = _sys.modules.get("api_server") or _sys.modules.get("agent.api_server")
    if host is None:
        raise RuntimeError(
            "register_scheduler_registry_routes: api_server module not in sys.modules; "
            "ensure api_server is imported before calling this function"
        )
    if require_auth is None:
        require_auth = host.require_auth

    @app.get(
        "/scheduler-registry",
        dependencies=[Depends(require_auth)],
    )
    async def scheduler_registry() -> Dict[str, Any]:
        """Cross-service scheduler entries beyond Mechanism A (see module docstring)."""
        # Both helpers make a blocking `requests` call under the hood — offload
        # them so a slow/unreachable service can't stall this process's event
        # loop for other in-flight requests. Independent, not a barrier on
        # each other: either can be slow without slowing the other down.
        (stock_simulator_entries, stock_simulator_status), (openalgo_entries, openalgo_status) = (
            await asyncio.gather(
                asyncio.to_thread(_stock_simulator_entries),
                asyncio.to_thread(_openalgo_entries),
            )
        )
        return {
            "status": "ok",
            "entries": [*stock_simulator_entries, *openalgo_entries],
            "sources": {
                "stock_simulator": stock_simulator_status,
                "openalgo": openalgo_status,
            },
        }

    @app.post(
        "/scheduler-registry/openalgo/{source}/{job_id}/pause",
        dependencies=[Depends(require_auth)],
    )
    async def scheduler_registry_openalgo_pause(source: str, job_id: str) -> Dict[str, Any]:
        try:
            from trade_integrations.execution.openalgo_client import OpenAlgoClient

            await asyncio.to_thread(OpenAlgoClient().pause_scheduler_job, source, job_id)
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except Exception as exc:
            logger.exception("scheduler-registry: failed to pause openalgo job %s/%s", source, job_id)
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        return {"status": "ok"}

    @app.post(
        "/scheduler-registry/openalgo/{source}/{job_id}/resume",
        dependencies=[Depends(require_auth)],
    )
    async def scheduler_registry_openalgo_resume(source: str, job_id: str) -> Dict[str, Any]:
        try:
            from trade_integrations.execution.openalgo_client import OpenAlgoClient

            await asyncio.to_thread(OpenAlgoClient().resume_scheduler_job, source, job_id)
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except Exception as exc:
            logger.exception("scheduler-registry: failed to resume openalgo job %s/%s", source, job_id)
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        return {"status": "ok"}
