"""Trading connector profile HTTP routes (list / select / check).

The agent ``trading_*`` tools and CLI ``vibe-trading connector`` use the same
``src.trading`` registry. This module exposes that registry to the Web UI so
SDK connectors (OpenAlgo, Alpaca, Dhan, …) appear alongside the OAuth live
runtime panel (Robinhood / IBKR on ``GET /live/status``).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from src.api.security import require_auth

router = APIRouter(prefix="/trading", tags=["trading-connectors"])


class TradingConnectorProfile(BaseModel):
    id: str
    connector: str
    label: str
    environment: str
    transport: str
    capabilities: list[str]
    readonly: bool
    config: dict[str, Any] = Field(default_factory=dict)
    notes: str = ""
    selected: bool = False


class TradingConnectorsResponse(BaseModel):
    selected_profile: str
    profiles: list[TradingConnectorProfile]


class SelectTradingConnectorRequest(BaseModel):
    profile_id: str = Field(..., min_length=1, max_length=128)


class SelectTradingConnectorResponse(BaseModel):
    status: str
    selected_profile: str


@router.get("/connectors", response_model=TradingConnectorsResponse, dependencies=[Depends(require_auth)])
def list_trading_connectors() -> TradingConnectorsResponse:
    """Return built-in trading connector profiles and the selected default."""
    from src.trading.profiles import list_profiles, load_selected_profile_id

    selected = load_selected_profile_id()
    profiles = [
        TradingConnectorProfile(**profile.to_dict(selected=profile.id == selected))
        for profile in list_profiles()
    ]
    return TradingConnectorsResponse(selected_profile=selected, profiles=profiles)


@router.post(
    "/connectors/select",
    response_model=SelectTradingConnectorResponse,
    dependencies=[Depends(require_auth)],
)
def select_trading_connector(body: SelectTradingConnectorRequest) -> SelectTradingConnectorResponse:
    """Persist the default trading connector profile id."""
    from src.trading.profiles import profile_by_id, save_selected_profile_id

    try:
        profile = profile_by_id(body.profile_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    save_selected_profile_id(profile.id)
    return SelectTradingConnectorResponse(status="ok", selected_profile=profile.id)


@router.get("/connectors/{profile_id}/check", dependencies=[Depends(require_auth)])
def check_trading_connector(profile_id: str) -> dict[str, Any]:
    """Run a non-mutating health check for a connector profile."""
    from src.trading.profiles import profile_by_id
    from src.trading.service import check_connection

    try:
        profile_by_id(profile_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return check_connection(profile_id)
