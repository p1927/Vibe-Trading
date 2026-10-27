"""Model-adapter routes (list and patch the LLM/embedding provider adapters). Split out of trade_routes.py; the routes register on its trade_router."""

from __future__ import annotations

from fastapi import Depends, HTTPException
from pydantic import BaseModel, Field
from src.api.helpers import redacted_error_detail
from src.api.security import require_local_or_auth
from typing import Any, Dict, List
from .trade_routes import logger, trade_router


class ModelAdapterRateLimit(BaseModel):
    rpm: int
    max_concurrent: int
    max_attempts: int
    base_delay_s: float
    max_delay_s: float
    jitter: bool
    honor_retry_after: bool


class ModelAdapter(BaseModel):
    adapter_id: str
    kind: str
    provider: str
    model: str
    enabled: bool
    priority: int
    fallback_adapter_id: str | None
    rate_limit: ModelAdapterRateLimit


class ModelAdaptersResponse(BaseModel):
    status: str = "ok"
    adapters: List[ModelAdapter] = Field(default_factory=list)
    message: str = ""


class ModelAdapterUpdate(BaseModel):
    enabled: bool | None = None
    priority: int | None = None
    fallback_adapter_id: str | None = None
    rate_limit: Dict[str, Any] | None = None
    retry: Dict[str, Any] | None = None


def _model_adapter_to_dict(spec: Any) -> Dict[str, Any]:
    return {
        "adapter_id": spec.adapter_id,
        "kind": spec.kind,
        "provider": spec.provider,
        "model": spec.model,
        "enabled": spec.enabled,
        "priority": spec.priority,
        "fallback_adapter_id": spec.fallback_adapter_id,
        "rate_limit": {
            "rpm": spec.rate_limit.rpm,
            "max_concurrent": spec.rate_limit.max_concurrent,
            "max_attempts": spec.rate_limit.max_attempts,
            "base_delay_s": spec.rate_limit.base_delay_s,
            "max_delay_s": spec.rate_limit.max_delay_s,
            "jitter": spec.rate_limit.jitter,
            "honor_retry_after": spec.rate_limit.honor_retry_after,
        },
    }


@trade_router.get("/model-adapters", response_model=ModelAdaptersResponse)
def get_model_adapters(
    _auth: None = Depends(require_local_or_auth),
) -> ModelAdaptersResponse:
    """List every registered LLM/embedding model adapter (catalog + runtime overrides)."""
    try:
        from src.trade.hub_bridge import ensure_trade_stack_path

        ensure_trade_stack_path()
        from trade_integrations.dataflows.model_adapters import list_adapters

        adapters = [_model_adapter_to_dict(spec) for spec in list_adapters()]
        return ModelAdaptersResponse(status="ok", adapters=adapters)
    except Exception as exc:
        logger.exception("model adapters list failed")
        raise HTTPException(status_code=502, detail=redacted_error_detail(exc)) from exc


@trade_router.patch("/model-adapters/{adapter_id}", response_model=ModelAdaptersResponse)
def patch_model_adapter(
    adapter_id: str,
    body: ModelAdapterUpdate,
    _auth: None = Depends(require_local_or_auth),
) -> ModelAdaptersResponse:
    """Persist a runtime override for one adapter (enable/disable, priority, rate limits, ...)."""
    try:
        from src.trade.hub_bridge import ensure_trade_stack_path

        ensure_trade_stack_path()
        from trade_integrations.dataflows.model_adapters import list_adapters, update_adapter

        # exclude_unset, not "drop every None": an explicit fallback_adapter_id=null is how the UI
        # clears a fallback ("(none)"); dropping it made that selection a silent no-op.
        patch = {
            k: v
            for k, v in body.model_dump(exclude_unset=True).items()
            if v is not None or k == "fallback_adapter_id"
        }
        update_adapter(adapter_id, patch)
        adapters = [_model_adapter_to_dict(spec) for spec in list_adapters()]
        return ModelAdaptersResponse(status="ok", adapters=adapters)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("model adapter update failed")
        raise HTTPException(status_code=502, detail=redacted_error_detail(exc)) from exc
