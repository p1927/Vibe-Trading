"""Scheduled research HTTP routes.

Mounted by ``agent/api_server.py`` via ``register_scheduled_routes(app, ...)``.
"""

from __future__ import annotations

import logging
import os
import sys as _sys
import time
import uuid
from typing import Any, Awaitable, Callable, Dict, List, Optional

from fastapi import Depends, FastAPI, HTTPException, Query, status
from pydantic import BaseModel, Field

from src.config.accessor import get_env_config

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_SCHEDULED_RESEARCH_SCHEDULER_ENV = "VIBE_TRADING_ENABLE_SCHEDULER"
_SCHEDULED_RESEARCH_TRUE_VALUES = {"1", "true", "yes", "on"}


# ---------------------------------------------------------------------------
# Module-level state
# ---------------------------------------------------------------------------

_scheduled_research_store: Any = None
_scheduled_research_executor: Any = None


def _scheduled_research_scheduler_enabled() -> bool:
    """Return whether scheduled research execution is enabled."""
    return get_env_config().agent_tuning.vibe_trading_enable_scheduler


def _get_scheduled_research_store():
    """Return the singleton ScheduledResearchJobStore, creating it on first call."""
    global _scheduled_research_store
    if _scheduled_research_store is None:
        from src.scheduled_research.store import ScheduledResearchJobStore

        _scheduled_research_store = ScheduledResearchJobStore()
    return _scheduled_research_store


async def _dispatch_scheduled_research_job(job) -> None:
    """Dispatch a scheduled research job.

    Jobs with ``config.job_type`` in ``index_factor_snapshot`` or
    ``index_research`` run the index research pipeline directly. All other
    jobs enqueue an agent session (legacy path).

    ``send_message`` queues the agent attempt and returns once accepted; it
    does not wait for that agent run to reach a terminal status. The executor's
    ``COMPLETED`` state for those paths means "successfully enqueued" or
    "pipeline finished" respectively.
    """
    from src.scheduled_research.index_jobs import INDEX_JOB_TYPES, dispatch_index_job
    from src.scheduled_research.options_jobs import OPTIONS_JOB_TYPES, dispatch_options_job
    from src.scheduled_research.trade_data_jobs import TRADE_DATA_JOB_TYPES, dispatch_trade_data_job
    from src.scheduled_research.hub_calibration_jobs import (
        HUB_CALIBRATION_JOB_TYPES,
        dispatch_hub_calibration_job,
    )

    job_type = str(job.config.get("job_type") or "")
    if job_type in INDEX_JOB_TYPES:
        await dispatch_index_job(job)
        return
    if job_type in OPTIONS_JOB_TYPES:
        await dispatch_options_job(job)
        return
    if job_type in TRADE_DATA_JOB_TYPES:
        await dispatch_trade_data_job(job)
        return
    if job_type in HUB_CALIBRATION_JOB_TYPES:
        await dispatch_hub_calibration_job(job)
        return
    from src.scheduled_research.capture_jobs import (
        HUB_CAPTURE_JOB_TYPES,
        dispatch_hub_capture_job,
    )

    if job_type in HUB_CAPTURE_JOB_TYPES:
        await dispatch_hub_capture_job(job)
        return
    from src.scheduled_research.autonomous_agent_jobs import (
        AUTONOMOUS_JOB_TYPES,
        dispatch_autonomous_job,
    )

    if job_type in AUTONOMOUS_JOB_TYPES:
        await dispatch_autonomous_job(job)
        return

    host = _sys.modules.get("api_server") or _sys.modules.get("agent.api_server")
    svc = host._get_session_service()
    if not svc:
        raise RuntimeError("Session runtime not enabled")
    # Pass a copy so the session runtime's internal config writes (e.g.
    # include_shell_tools) do not mutate the persisted scheduled-run config.
    session = svc.create_session(
        title=f"scheduled-research:{job.id}", config=dict(job.config)
    )
    logger.info(
        "dispatching scheduled research job %s via session %s",
        job.id,
        session.session_id,
    )
    await svc.send_message(session.session_id, job.prompt)


def _get_scheduled_research_executor():
    """Return the singleton scheduled research executor."""
    global _scheduled_research_executor
    if _scheduled_research_executor is None:
        from src.scheduled_research.executor import ScheduledResearchExecutor

        _scheduled_research_executor = ScheduledResearchExecutor(
            _get_scheduled_research_store(),
            _dispatch_scheduled_research_job,
            enabled=_scheduled_research_scheduler_enabled(),
        )
    return _scheduled_research_executor


def _register_persisted_autonomous_agent_jobs() -> None:
    """Re-register scheduler jobs for running autonomous agents after API restart."""
    try:
        from pathlib import Path

        trade_root = Path(__file__).resolve().parents[4]
        integrations = trade_root / "integrations"
        if integrations.is_dir() and str(integrations) not in _sys.path:
            _sys.path.insert(0, str(integrations))
        from trade_integrations.autonomous_agents.store import list_agents
        from src.scheduled_research.autonomous_agent_jobs import register_agent_jobs

        for agent in list_agents():
            if str(agent.get("status")) == "running":
                register_agent_jobs(agent)
    except Exception:
        logger.exception("failed to register persisted autonomous agent jobs")


def _start_scheduled_research_executor() -> None:
    """Start scheduled research execution when explicitly enabled."""
    try:
        from src.scheduled_research.lifecycle import recover_scheduler_jobs_on_stack_boot

        recover_scheduler_jobs_on_stack_boot(_get_scheduled_research_store())
    except Exception:
        logger.exception("failed to recover stale scheduler jobs on API startup")
    from src.scheduled_research.index_jobs import (
        is_index_scheduler_enabled,
        register_default_index_jobs,
    )
    from src.scheduled_research.options_jobs import (
        is_options_scheduler_enabled,
        register_default_options_jobs,
    )
    from src.scheduled_research.trade_data_jobs import (
        is_trade_data_scheduler_enabled,
        register_default_trade_data_jobs,
    )
    from src.scheduled_research.hub_calibration_jobs import (
        is_hub_calibration_scheduler_enabled,
        register_default_hub_calibration_jobs,
    )
    from src.scheduled_research.capture_jobs import (
        is_hub_capture_scheduler_enabled,
        register_default_hub_capture_jobs,
    )

    if is_index_scheduler_enabled():
        register_default_index_jobs(_get_scheduled_research_store())
    if is_options_scheduler_enabled():
        register_default_options_jobs(_get_scheduled_research_store())
    if is_hub_calibration_scheduler_enabled():
        register_default_hub_calibration_jobs(_get_scheduled_research_store())
    elif is_trade_data_scheduler_enabled():
        register_default_trade_data_jobs(_get_scheduled_research_store())
    if is_hub_capture_scheduler_enabled():
        register_default_hub_capture_jobs(_get_scheduled_research_store())
    _register_persisted_autonomous_agent_jobs()
    try:
        from src.scheduled_research.autonomous_bootstrap import (
            resume_pending_bootstraps,
            resume_stale_pending_bootstraps,
            resume_stale_running_bootstraps,
        )

        resumed = resume_pending_bootstraps()
        if resumed:
            logger.info("resumed %d pending autonomous agent bootstrap(s)", resumed)
        stale = resume_stale_pending_bootstraps()
        if stale:
            logger.info("re-scheduled %d stale pending autonomous bootstrap(s)", stale)
        stale_running = resume_stale_running_bootstraps()
        if stale_running:
            logger.info("re-scheduled %d stale running autonomous bootstrap(s)", stale_running)
        try:
            from trade_integrations.autonomous_agents.recovery import run_autonomous_agent_recovery

            recovery = run_autonomous_agent_recovery()
            if any(recovery.values()):
                logger.info("autonomous agent recovery: %s", recovery)
        except Exception:
            logger.debug("autonomous agent recovery on startup failed", exc_info=True)
    except Exception:
        logger.exception("failed to resume pending autonomous bootstraps")
    try:
        from pathlib import Path

        if os.getenv("STACK_DEV", "").strip().lower() in {"1", "true", "yes", "on"}:
            logger.debug("skipping Nautilus watch ensure in dev mode (use: trade reload nautilus)")
        else:
            trade_root = Path(__file__).resolve().parents[3]
            integrations = trade_root / "integrations"
            if integrations.is_dir() and str(integrations) not in _sys.path:
                _sys.path.insert(0, str(integrations))
            from trade_integrations.autonomous_agents.nautilus_watch import (
                ensure_nautilus_watch_for_running_agents,
                get_watch_process_status,
            )

            status = get_watch_process_status(reconcile=True)
            if status.get("alive") and status.get("registry_agent_ids"):
                logger.debug("Nautilus watch already alive with registry — skipping startup ensure")
            elif ensure_nautilus_watch_for_running_agents():
                logger.info("ensured Nautilus watch for running India bridge agent(s)")
    except Exception:
        logger.exception("failed to ensure Nautilus watch on startup")
    if not _scheduled_research_scheduler_enabled():
        return
    _get_scheduled_research_executor().start()


async def _stop_scheduled_research_executor() -> None:
    """Stop scheduled research execution if it was started."""
    global _scheduled_research_executor
    logger.info("scheduled research shutdown: recovering jobs and stopping executor")
    try:
        from src.scheduled_research.lifecycle import recover_scheduler_jobs_on_stack_shutdown

        recover_scheduler_jobs_on_stack_shutdown(_get_scheduled_research_store())
    except Exception:
        logger.exception("failed to recover scheduler jobs on API shutdown")
    executor = _scheduled_research_executor
    if executor is not None:
        await executor.stop()
    _scheduled_research_executor = None


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class CreateScheduledRunRequest(BaseModel):
    """Request body for POST /scheduled-runs."""

    id: Optional[str] = Field(
        None, description="Job id; auto-generated UUID when omitted"
    )
    prompt: str = Field(
        ..., min_length=1, description="Research prompt or backtest description"
    )
    schedule: str = Field(
        ..., min_length=1, description="Interval-ms or 5-field cron expression"
    )
    next_run_at: Optional[int] = Field(
        None, description="Epoch-ms for next run; defaults to now"
    )
    config: Dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Optional job parameters. Use job_type "
            "'index_factor_snapshot' or 'index_research' for Nifty index "
            "pipeline jobs (see scheduled_research.index_jobs)."
        ),
    )


class ScheduledRunResponse(BaseModel):
    """API response for a single scheduled job."""

    id: str
    prompt: str
    schedule: str
    next_run_at: int
    status: str
    created_at: int
    config: Dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

AuthDep = Callable[..., Awaitable[Any] | Any]


def register_scheduled_routes(
    app: FastAPI,
    require_auth: AuthDep | None = None,
) -> None:
    """Mount the scheduled routes onto ``app``.

    Resolves ``require_auth`` from the host ``api_server`` module via
    ``sys.modules`` when not passed explicitly.
    """
    host = _sys.modules.get("api_server") or _sys.modules.get("agent.api_server")

    if host is None:
        raise RuntimeError(
            "register_scheduled_routes: api_server module not in sys.modules; "
            "ensure api_server is imported before calling this function"
        )

    if require_auth is None:
        require_auth = host.require_auth

    def _host_validate_path_param(value: str, kind: str) -> None:
        h = _sys.modules.get("api_server") or _sys.modules.get("agent.api_server")
        h._validate_path_param(value, kind)

    # --- Routes ---

    @app.post(
        "/scheduled-runs",
        response_model=ScheduledRunResponse,
        status_code=status.HTTP_201_CREATED,
        dependencies=[Depends(require_auth)],
    )
    async def create_scheduled_run(
        request: CreateScheduledRunRequest,
    ) -> ScheduledRunResponse:
        """Create (or replace) a scheduled research job.

        The job is persisted immediately. No execution is triggered.
        """
        from src.scheduled_research.models import (
            JobStatus,
            ScheduledResearchJob,
            validate_schedule,
        )

        try:
            validate_schedule(request.schedule)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        now_ms = int(time.time() * 1000)
        job = ScheduledResearchJob(
            id=request.id or str(uuid.uuid4()),
            prompt=request.prompt,
            schedule=request.schedule,
            next_run_at=request.next_run_at if request.next_run_at is not None else now_ms,
            status=JobStatus.PENDING,
            created_at=now_ms,
            config=request.config,
        )
        _get_scheduled_research_store().upsert(job)
        return ScheduledRunResponse(**job.to_dict())

    @app.get(
        "/scheduled-runs",
        response_model=List[ScheduledRunResponse],
        dependencies=[Depends(require_auth)],
    )
    async def list_scheduled_runs(
        status_filter: Optional[str] = Query(None, alias="status"),
        limit: int = Query(50, ge=1, le=200),
    ) -> List[ScheduledRunResponse]:
        """List scheduled research jobs, optionally filtered by status."""
        jobs = _get_scheduled_research_store().list_jobs(
            status=status_filter, limit=limit
        )
        return [ScheduledRunResponse(**j.to_dict()) for j in jobs]

    @app.delete(
        "/scheduled-runs/{job_id}",
        status_code=status.HTTP_204_NO_CONTENT,
        dependencies=[Depends(require_auth)],
    )
    async def delete_scheduled_run(job_id: str) -> None:
        """Cancel (delete) a scheduled research job by id."""
        _host_validate_path_param(job_id, "job_id")
        removed = _get_scheduled_research_store().delete(job_id)
        if not removed:
            raise HTTPException(
                status_code=404, detail=f"scheduled run {job_id} not found"
            )
