"""REST routes for unified Nautilus watch registry."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

watch_router = APIRouter(prefix="/watches", tags=["watches"])

_TRADE_ROOT = Path(__file__).resolve().parents[4]
_INTEGRATIONS = _TRADE_ROOT / "integrations"
if _INTEGRATIONS.is_dir() and str(_INTEGRATIONS) not in sys.path:
    sys.path.insert(0, str(_INTEGRATIONS))


class CreateWatchRequest(BaseModel):
    watch_spec: Dict[str, Any] = Field(..., description="Nautilus watch_spec with rules")
    symbols: Optional[List[str]] = None
    label: Optional[str] = None
    one_shot: bool = False
    vibe_session_id: Optional[str] = None


class UpdateWatchRequest(BaseModel):
    watch_spec: Optional[Dict[str, Any]] = None
    label: Optional[str] = None


@watch_router.get("")
def list_watches_route(
    session_id: Optional[str] = None,
    agent_id: Optional[str] = None,
) -> Dict[str, Any]:
    from trade_integrations.watch_registry.api import mcp_list_watches

    return mcp_list_watches(session_id=session_id, agent_id=agent_id)


@watch_router.post("/session/{session_id}")
def create_session_watch(session_id: str, body: CreateWatchRequest) -> Dict[str, Any]:
    from trade_integrations.watch_registry.api import mcp_create_watch

    vibe_sid = str(body.vibe_session_id or session_id).strip()
    result = mcp_create_watch(
        owner_kind="session",
        owner_id=session_id,
        vibe_session_id=vibe_sid,
        watch_spec=body.watch_spec,
        symbols=body.symbols,
        label=body.label,
        one_shot=body.one_shot,
    )
    if result.get("status") != "ok":
        raise HTTPException(status_code=400, detail=result.get("error") or "create failed")
    return result


@watch_router.post("/agent/{agent_id}")
def create_agent_watch(agent_id: str, body: CreateWatchRequest) -> Dict[str, Any]:
    from trade_integrations.autonomous_agents.store import get_agent
    from trade_integrations.watch_registry.api import mcp_create_watch

    agent = get_agent(agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail=f"agent not found: {agent_id}")
    vibe_sid = str(body.vibe_session_id or agent.get("vibe_session_id") or "").strip()
    if not vibe_sid:
        raise HTTPException(status_code=400, detail="agent has no vibe_session_id")
    result = mcp_create_watch(
        owner_kind="autonomous_agent",
        owner_id=agent_id,
        vibe_session_id=vibe_sid,
        watch_spec=body.watch_spec,
        symbols=body.symbols or list(agent.get("symbols") or []),
        label=body.label,
        one_shot=body.one_shot,
    )
    if result.get("status") != "ok":
        raise HTTPException(status_code=400, detail=result.get("error") or "create failed")
    return result


@watch_router.patch("/{watch_id}")
def update_watch_route(watch_id: str, body: UpdateWatchRequest) -> Dict[str, Any]:
    from trade_integrations.watch_registry.api import mcp_update_watch

    result = mcp_update_watch(watch_id, watch_spec=body.watch_spec, label=body.label)
    if result.get("status") != "ok":
        raise HTTPException(status_code=404, detail=result.get("error") or "update failed")
    return result


@watch_router.delete("/{watch_id}")
def delete_watch_route(watch_id: str) -> Dict[str, Any]:
    from trade_integrations.watch_registry.api import mcp_delete_watch

    result = mcp_delete_watch(watch_id)
    if result.get("status") != "ok":
        raise HTTPException(status_code=404, detail=result.get("error") or "delete failed")
    return result
