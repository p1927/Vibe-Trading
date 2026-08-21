"""Scheduled research HTTP routes.

Mounted by ``agent/api_server.py`` via ``register_scheduled_routes(app, ...)``.
"""

from __future__ import annotations

import logging
import os
import re
import sys as _sys
import time
import uuid
from typing import Any, Awaitable, Callable, Dict, List, Optional

from fastapi import Depends, FastAPI, HTTPException, Query, status
from pydantic import BaseModel, Field
from starlette.responses import Response

from src.config.accessor import get_env_config

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_SCHEDULED_RESEARCH_SCHEDULER_ENV = "VIBE_TRADING_ENABLE_SCHEDULER"
_SCHEDULED_RESEARCH_TRUE_VALUES = {"1", "true", "yes", "on"}

# Mirrors ``_SAFE_PATH_PARAM_RE`` in src/api/helpers.py, which the delete route
# enforces on the job id. Kept in sync so a job can never be created under an
# id the delete route refuses.
_SAFE_JOB_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


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


async def _dispatch_scheduled_research_job(job) -> Optional[str]:
    """Dispatch a scheduled research job.

    Jobs with ``config.job_type`` in one of the pipeline job-type sets (index
    research, options, trade-data, hub-calibration, hub-capture, autonomous
    agent, recording-wake) run their dedicated pipeline directly and return
    ``None``. All other jobs enqueue an agent session (legacy path).

    ``send_message`` queues the agent attempt and returns once accepted; it
    does not wait for that agent run to reach a terminal status. The executor's
    ``COMPLETED`` state for the pipeline paths means "pipeline finished"; for
    the legacy path it means "successfully enqueued."

    Returns:
        The session id the attempt was enqueued into (legacy path only),
        which is what lets the delivery outbox find the briefing once the
        run actually finishes. ``None`` for the direct pipeline paths.
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
    # Phase C: recording-wake jobs (cron-driven respawn of
    # ``wait_for_open=True`` recordings). See
    # ``src.scheduled_research.recording_wake_jobs``.
    from src.scheduled_research.recording_wake_jobs import (
        RECORDING_WAKE_JOB_TYPES,
        dispatch_recording_wake_job,
    )

    if job_type in RECORDING_WAKE_JOB_TYPES:
        await dispatch_recording_wake_job(job)
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
    return session.session_id


def _read_scheduled_briefing(session_id: str) -> Optional[tuple[str, str]]:
    """Return ``(terminal status, briefing text)`` once a run has finished.

    The assistant reply carries the attempt's terminal status in its metadata,
    and it is the same text the user sees in the session — delivering anything
    else would mean the channel and the app disagree about what the run said.

    Args:
        session_id: Session the scheduled run was dispatched into.

    Returns:
        The status and text, or ``None`` while the run is still in flight.
    """
    host = _sys.modules.get("api_server") or _sys.modules.get("agent.api_server")
    svc = host._get_session_service() if host else None
    if not svc:
        return None
    for message in reversed(svc.get_messages(session_id, limit=50)):
        if message.role != "assistant":
            continue
        status = (message.metadata or {}).get("status")
        if isinstance(status, str) and status:
            return status, message.content or ""
    return None


async def _send_scheduled_briefing(channel: str, target: Optional[str], text: str) -> None:
    """Deliver one briefing through the configured IM channel.

    Args:
        channel: Channel id as configured in the channel runtime.
        target: Address within that channel, or ``None`` for its default.
        text: The briefing to deliver.

    Raises:
        RuntimeError: If the channel runtime is unavailable or has no such
            channel, so the outbox records a retryable failure rather than
            reporting a delivery that never happened.
    """
    from src.channels.bus.events import OutboundMessage

    host = _sys.modules.get("api_server") or _sys.modules.get("agent.api_server")
    manager = getattr(host, "_channel_manager", None) if host else None
    if manager is None:
        raise RuntimeError("channel runtime is not running")
    adapter = manager.get_channel(channel)
    if adapter is None:
        raise RuntimeError(f"channel {channel!r} is not configured")
    if not target:
        raise RuntimeError(f"channel {channel!r} has no delivery target configured")
    await adapter.send(
        OutboundMessage(channel=channel, chat_id=target, content=text)
    )


def _get_scheduled_research_executor():
    """Return the singleton scheduled research executor."""
    global _scheduled_research_executor
    if _scheduled_research_executor is None:
        from src.scheduled_research.executor import ScheduledResearchExecutor

        _scheduled_research_executor = ScheduledResearchExecutor(
            _get_scheduled_research_store(),
            _dispatch_scheduled_research_job,
            enabled=_scheduled_research_scheduler_enabled(),
            briefing_reader=_read_scheduled_briefing,
            channel_sender=_send_scheduled_briefing,
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
        from pathlib import Path

        trade_root = Path(__file__).resolve().parents[4]
        integrations = trade_root / "integrations"
        if integrations.is_dir() and str(integrations) not in _sys.path:
            _sys.path.insert(0, str(integrations))
        from trade_integrations.autonomous_agents.proposals import pause_running_agents_on_boot

        # A process start/restart (crash, deploy, or dev uvicorn --reload
        # respawn) is indistinguishable on disk from an agent that was
        # legitimately left running. Force every "running" agent to
        # "paused" before any resume/bootstrap sweep below can see it, so
        # nothing auto-fires LLM work until a user explicitly resumes it.
        pause_running_agents_on_boot()
    except Exception:
        logger.exception("failed to boot-pause running autonomous agents")
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
    # Recording-wake poller: unlike the general executor below, this starts
    # unconditionally (not gated by the scheduler enable/resume flag) — see
    # ``src.trade.recording_wait_scheduler`` module docstring for why a
    # ``wait_for_open`` recording must not depend on that LLM-cost gate.
    try:
        from src.trade.recording_wait_scheduler import start_recording_wake_poller

        start_recording_wake_poller(_get_scheduled_research_store())
    except Exception:
        logger.exception("failed to start recording-wake poller on startup")
    # Hot-reload safety: every ``register_default_*`` helper stamps
    # ``next_run_at=now_ms`` so a fresh job fires immediately. On uvicorn
    # --reload this means every code save re-stamps every default job and the
    # first executor tick dispatches them all, cascading LLM calls and IO
    # before the user types anything. Push never-executed PENDING jobs forward
    # by ``SCHEDULED_RESEARCH_FRESH_DEFER_MS`` (default 30 min) so the
    # scheduler only fires on the persisted cron schedule, not on every reload.
    try:
        from src.scheduled_research.executor import defer_fresh_registrations

        deferred = defer_fresh_registrations(_get_scheduled_research_store())
        if deferred:
            logger.info(
                "deferred %d fresh scheduled job(s) on startup to avoid hot-reload cascade",
                deferred,
            )
    except Exception:
        logger.exception("defer_fresh_registrations failed on startup")
    # Note: no bootstrap-resume sweep runs here anymore. Every agent that
    # was "running" was just boot-paused above, so resuming bootstrap work
    # is now exclusively a user action via POST /autonomous-agents/{id}/resume
    # (see autonomous_routes.resume_agent, which re-fires a stuck bootstrap
    # when appropriate).
    try:
        from trade_integrations.autonomous_agents.recovery import run_autonomous_agent_recovery

        recovery = run_autonomous_agent_recovery()
        if any(recovery.values()):
            logger.info("autonomous agent recovery: %s", recovery)
    except Exception:
        logger.debug("autonomous agent recovery on startup failed", exc_info=True)
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
    # Global scheduled-research jobs (index/options/hub-calibration/trade-data/
    # hub-capture) default to paused on every boot, the same "always ephemeral,
    # resume via UI" model used for autonomous agents. Jobs were registered
    # above so they're visible in the Scheduled UI with their real cadence,
    # but the executor's dispatch loop is intentionally NOT started here —
    # only POST /scheduled-runs/scheduler/resume starts it, and every fresh
    # process start (clean shutdown, crash, or dev --reload) requires that
    # click again rather than remembering the prior running state.


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
    try:
        from src.trade.recording_wait_scheduler import stop_recording_wake_poller

        await stop_recording_wake_poller()
    except Exception:
        logger.exception("failed to stop recording-wake poller on shutdown")


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class CreateScheduledRunRequest(BaseModel):
    """Request body for POST /scheduled-runs."""

    id: Optional[str] = Field(
        None,
        description=(
            "Job id; auto-generated UUID when omitted. Must match the id rule "
            "the delete route enforces: letters, digits, '_' and '-', 1-128 "
            "characters."
        ),
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
    timezone: Optional[str] = Field(
        None,
        description=(
            "IANA timezone key the cron schedule is evaluated in "
            "(e.g. 'Pacific/Auckland'); null = UTC, the pre-existing "
            "semantics. Ignored by interval schedules."
        ),
    )
    delivery_channel: Optional[str] = Field(
        None,
        description=(
            "Channel id a finished briefing is pushed to. Delivery is opt-in "
            "per job: omit this and results stay in the app, which is the "
            "behaviour every existing job keeps."
        ),
    )
    delivery_target: Optional[str] = Field(
        None,
        description="Address within that channel (chat / group / user id).",
    )


class PlaybookResponse(BaseModel):
    """Catalogue record for one research playbook template.

    ``body`` is the raw instruction text with ``{{placeholders}}`` unresolved.
    It is populated only by the single-template endpoint; the list endpoint
    leaves it null so a catalogue response stays small.
    """

    slug: str
    name: str
    description: str
    suggested_schedule: str
    suggested_timezone: Optional[str] = None
    markets: List[str] = Field(default_factory=list)
    data_capabilities: List[str] = Field(default_factory=list)
    variables: Dict[str, str] = Field(default_factory=dict)
    body: Optional[str] = None


class CreateRunFromPlaybookRequest(BaseModel):
    """Request body for POST /scheduled-runs/playbooks/{slug}.

    Every field is optional: posting ``{}`` schedules the template on its own
    suggested cadence with its declared variable defaults.
    """

    id: Optional[str] = Field(
        None, description="Job id; defaults to a slug-prefixed generated id"
    )
    schedule: Optional[str] = Field(
        None,
        min_length=1,
        description=(
            "Schedule override (interval-ms or 5-field cron); defaults to the "
            "template's suggested_schedule"
        ),
    )
    timezone: Optional[str] = Field(
        None,
        description=(
            "IANA timezone override. Omit the field to keep the template's "
            "suggested_timezone; send null explicitly to force UTC."
        ),
    )
    variables: Dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Overrides for the template's declared variables. An undeclared "
            "name is rejected rather than silently ignored."
        ),
    )
    config: Dict[str, Any] = Field(
        default_factory=dict, description="Session config forwarded to the agent run"
    )
    next_run_at: Optional[int] = Field(
        None, description="Explicit first-fire epoch-ms, bypassing the default rule"
    )


class ScheduledRunResponse(BaseModel):
    """API response for a single scheduled job."""

    id: str
    prompt: str
    schedule: str
    next_run_at: int
    status: str
    created_at: int
    paused: bool = False
    last_run_at: Optional[int] = None
    consecutive_failures: int = 0
    last_error: Optional[str] = None
    failure_kind: Optional[str] = None
    config: Dict[str, Any] = Field(default_factory=dict)
    timezone: Optional[str] = None
    delivery_channel: Optional[str] = None
    delivery_target: Optional[str] = None
    delivery_status: str = "none"
    delivery_error: Optional[str] = None
    delivery_updated_at: Optional[int] = None
    last_verdict: Optional[Dict[str, Any]] = None


def _job_to_response(job: ScheduledResearchJob) -> "ScheduledRunResponse":
    """Flatten a job for the wire, delivery record included.

    ``to_dict`` nests the outbox row; the API keeps it flat because the list
    view reads it per row and a nested object would make every consumer reach
    through a key that means nothing to them.

    Args:
        job: The stored job.

    Returns:
        The response model for that job.
    """
    payload = job.to_dict()
    delivery = payload.pop("delivery", {}) or {}
    last_verdict = payload.pop("last_verdict", None)
    return ScheduledRunResponse(
        **payload,
        last_verdict=last_verdict,
        delivery_status=delivery.get("status", "none"),
        delivery_error=delivery.get("error"),
        delivery_updated_at=delivery.get("updated_at"),
    )


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

    @app.get(
        "/scheduled-runs/scheduler/status",
        dependencies=[Depends(require_auth)],
    )
    async def scheduler_status() -> Dict[str, Any]:
        """Report whether the global scheduled-research executor is dispatching jobs.

        ``enabled`` reflects the static env-var gate (VIBE_TRADING_ENABLE_SCHEDULER);
        ``running`` reflects whether the dispatch loop is actually active right now.
        Every process boot starts with running=False regardless of prior session state.
        """
        executor = _get_scheduled_research_executor()
        return {"enabled": _scheduled_research_scheduler_enabled(), "running": executor.is_running}

    @app.post(
        "/scheduled-runs/scheduler/resume",
        dependencies=[Depends(require_auth)],
    )
    async def resume_scheduler() -> Dict[str, Any]:
        """Start dispatching due global scheduled-research jobs."""
        if not _scheduled_research_scheduler_enabled():
            raise HTTPException(
                status_code=400,
                detail="scheduler disabled via VIBE_TRADING_ENABLE_SCHEDULER",
            )
        executor = _get_scheduled_research_executor()
        executor.start()
        return {"status": "ok", "running": executor.is_running}

    @app.post(
        "/scheduled-runs/scheduler/pause",
        dependencies=[Depends(require_auth)],
    )
    async def pause_scheduler() -> Dict[str, Any]:
        """Stop dispatching global scheduled-research jobs; due jobs stay pending until resumed."""
        executor = _get_scheduled_research_executor()
        await executor.stop()
        return {"status": "ok", "running": executor.is_running}

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
            is_interval_schedule,
            validate_schedule,
            validate_timezone,
            validate_timezone_shape,
        )

        from src.scheduled_research.executor import next_due

        # A job whose id the delete route rejects can never be cancelled
        # through the API, so the id is held to that same rule at creation
        # rather than at first attempted delete.
        if request.id is not None and not _SAFE_JOB_ID_RE.fullmatch(request.id):
            raise HTTPException(
                status_code=422,
                detail=(
                    "job id must be 1-128 characters of letters, digits, "
                    "'_' or '-'"
                ),
            )

        try:
            validate_schedule(request.schedule)
            # Interval schedules ignore the timezone, and the executor
            # deliberately skips resolving it for them so an interval job keeps
            # advancing on a host whose timezone database lacks the key. The
            # create path follows the same rule: only a cron schedule, whose
            # evaluation actually needs the zone, resolves it. Both forms still
            # reject a blank value.
            if is_interval_schedule(request.schedule):
                validate_timezone_shape(request.timezone)
            else:
                validate_timezone(request.timezone)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        now_ms = int(time.time() * 1000)
        next_run_at = request.next_run_at
        if next_run_at is None:
            next_run_at = now_ms
            if request.timezone is not None and not is_interval_schedule(request.schedule):
                # A timezone-carrying cron job's contract is the authored wall
                # clock, so its first fire is the first authored occurrence —
                # not the creation moment. Interval jobs and timezone-less
                # jobs keep the pre-existing immediate-first-fire default.
                try:
                    next_run_at = next_due(request.schedule, now_ms, request.timezone)
                except ValueError as exc:
                    raise HTTPException(status_code=422, detail=str(exc)) from exc

        job = ScheduledResearchJob(
            id=request.id or str(uuid.uuid4()),
            prompt=request.prompt,
            schedule=request.schedule,
            next_run_at=next_run_at,
            status=JobStatus.PENDING,
            created_at=now_ms,
            config=request.config,
            timezone=request.timezone,
            delivery_channel=request.delivery_channel,
            delivery_target=request.delivery_target,
        )
        _get_scheduled_research_store().upsert(job)
        return _job_to_response(job)

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
        return [_job_to_response(j) for j in jobs]

    @app.delete(
        "/scheduled-runs/{job_id}",
        status_code=status.HTTP_204_NO_CONTENT,
        dependencies=[Depends(require_auth)],
    )
    async def delete_scheduled_run(job_id: str) -> Response:
        """Cancel (delete) a scheduled research job by id."""
        _host_validate_path_param(job_id, "job_id")
        removed = _get_scheduled_research_store().delete(job_id)
        if not removed:
            raise HTTPException(
                status_code=404, detail=f"scheduled run {job_id} not found"
            )
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    def _set_job_paused(job_id: str, paused: bool) -> ScheduledRunResponse:
        _host_validate_path_param(job_id, "job_id")
        store = _get_scheduled_research_store()
        job = store.get(job_id)
        if job is None:
            raise HTTPException(
                status_code=404, detail=f"scheduled run {job_id} not found"
            )
        job.paused = paused
        store.upsert(job)
        return ScheduledRunResponse(**job.to_dict())

    @app.post(
        "/scheduled-runs/{job_id}/pause",
        response_model=ScheduledRunResponse,
        dependencies=[Depends(require_auth)],
    )
    async def pause_scheduled_run(job_id: str) -> ScheduledRunResponse:
        """Pause a single scheduled job without changing its schedule or config.

        The job is skipped by the executor's due-check until resumed; its
        ``next_run_at`` is left untouched.
        """
        return _set_job_paused(job_id, True)

    @app.post(
        "/scheduled-runs/{job_id}/resume",
        response_model=ScheduledRunResponse,
        dependencies=[Depends(require_auth)],
    )
    async def resume_scheduled_run(job_id: str) -> ScheduledRunResponse:
        """Resume a single paused scheduled job on its existing cadence."""
        return _set_job_paused(job_id, False)

    # --- Playbook templates ---
    #
    # Mounted under /scheduled-runs because a template is only useful as the
    # source of a scheduled run. ``{job_id}`` never matches across a "/", so
    # these paths cannot collide with the CRUD routes above. Every one carries
    # the same ``require_auth`` dependency: there is deliberately no unguarded
    # way to enumerate the catalogue or create a job from it.

    @app.get(
        "/scheduled-runs/playbooks",
        response_model=List[PlaybookResponse],
        dependencies=[Depends(require_auth)],
    )
    async def list_research_playbooks() -> List[PlaybookResponse]:
        """List the available research playbook templates, without bodies."""
        from src.scheduled_research.playbooks import PlaybookError, list_playbooks

        try:
            playbooks = list_playbooks()
        except (PlaybookError, OSError) as exc:
            # A malformed file is surfaced, not skipped: a user template the
            # loader refuses must be visible as a broken template rather than
            # vanish from the catalogue.
            raise HTTPException(
                status_code=500, detail=f"playbook catalogue is unreadable: {exc}"
            ) from exc
        return [PlaybookResponse(**pb.to_dict()) for pb in playbooks]

    @app.get(
        "/scheduled-runs/playbooks/{slug}",
        response_model=PlaybookResponse,
        dependencies=[Depends(require_auth)],
    )
    async def get_research_playbook(slug: str) -> PlaybookResponse:
        """Read one research playbook template, including its instruction body."""
        from src.scheduled_research.playbooks import (
            PlaybookError,
            PlaybookNotFoundError,
            get_playbook,
        )

        _host_validate_path_param(slug, "slug")
        try:
            playbook = get_playbook(slug)
        except PlaybookNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except (PlaybookError, OSError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return PlaybookResponse(**playbook.to_dict(include_body=True))

    @app.post(
        "/scheduled-runs/playbooks/{slug}",
        response_model=ScheduledRunResponse,
        status_code=status.HTTP_201_CREATED,
        dependencies=[Depends(require_auth)],
    )
    async def create_scheduled_run_from_playbook(
        slug: str,
        request: CreateRunFromPlaybookRequest,
    ) -> ScheduledRunResponse:
        """Create a scheduled research job from a template.

        The template body becomes ``job.prompt`` verbatim — the natural-language
        data requirements are never rewritten into tool calls here; routing
        stays the agent's job at run time.

        Schedule and timezone are validated by ``ResearchPlaybook.to_job``,
        which calls the same ``validate_schedule`` / ``validate_timezone`` the
        plain ``POST /scheduled-runs`` path uses, so the two entry points can
        never accept different schedule grammars.
        """
        from src.scheduled_research.playbooks import (
            PlaybookError,
            PlaybookNotFoundError,
            get_playbook,
        )

        _host_validate_path_param(slug, "slug")
        # Unlike POST /scheduled-runs this checks the caller-supplied id up
        # front: an id outside the safe pattern would persist fine but could
        # never be removed through DELETE /scheduled-runs/{job_id}, which
        # rejects it with 400.
        if request.id is not None:
            _host_validate_path_param(request.id, "id")

        try:
            playbook = get_playbook(slug)
        except PlaybookNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except (PlaybookError, OSError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        kwargs: Dict[str, Any] = {}
        # An omitted timezone keeps the template's suggestion; an explicit null
        # means UTC. Pydantic collapses both to ``None``, so the distinction has
        # to come from the fields the caller actually sent.
        if "timezone" in request.model_fields_set:
            kwargs["timezone"] = request.timezone

        try:
            job = playbook.to_job(
                job_id=request.id,
                schedule=request.schedule,
                variables=request.variables,
                config=request.config,
                next_run_at=request.next_run_at,
                **kwargs,
            )
        except ValueError as exc:
            # Covers PlaybookError (undeclared/oversized variable) and the
            # schedule / timezone / cron-window failures, all ValueError.
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        _get_scheduled_research_store().upsert(job)
        return _job_to_response(job)
