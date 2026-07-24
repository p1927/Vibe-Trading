"""FastAPI routes for Tier 0 observability (agent SSOT)."""

from __future__ import annotations

from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from src.api.security import require_local_or_auth
from trade_integrations.observability.issues import list_issues, open_issue_count, resolve_issue
from trade_integrations.observability.paths import events_path, issues_path
from trade_integrations.observability.store import read_jsonl_tail

observability_router = APIRouter(prefix="/trade/observability", tags=["observability"])


class ObservabilityIssueResolveResponse(BaseModel):
    issue_id: str
    resolved: bool


class ObservabilityIssuesResponse(BaseModel):
    issues: list[dict[str, Any]]
    open_count: int


class ObservabilitySummaryResponse(BaseModel):
    open_issue_count: int
    recent_events: list[dict[str, Any]]
    events_path: str
    issues_path: str


@observability_router.get("/issues", response_model=ObservabilityIssuesResponse)
def get_observability_issues(
    status: Literal["open", "resolved"] = "open",
    module: Optional[str] = None,
    limit: int = 100,
    _: None = Depends(require_local_or_auth),
) -> ObservabilityIssuesResponse:
    issues = list_issues(status=status, module=module, limit=min(limit, 500))
    return ObservabilityIssuesResponse(
        issues=issues,
        open_count=open_issue_count(module=module),
    )


@observability_router.post(
    "/issues/{issue_id}/resolve",
    response_model=ObservabilityIssueResolveResponse,
)
def post_resolve_observability_issue(
    issue_id: str,
    _: None = Depends(require_local_or_auth),
) -> ObservabilityIssueResolveResponse:
    ok = resolve_issue(issue_id)
    if not ok:
        raise HTTPException(status_code=404, detail="issue not found or already resolved")
    return ObservabilityIssueResolveResponse(issue_id=issue_id, resolved=True)


@observability_router.get("/summary", response_model=ObservabilitySummaryResponse)
def get_observability_summary(
    event_limit: int = Query(default=50, ge=1, le=500),
    _: None = Depends(require_local_or_auth),
) -> ObservabilitySummaryResponse:
    return ObservabilitySummaryResponse(
        open_issue_count=open_issue_count(),
        recent_events=read_jsonl_tail(events_path(), limit=event_limit),
        events_path=str(events_path()),
        issues_path=str(issues_path()),
    )
