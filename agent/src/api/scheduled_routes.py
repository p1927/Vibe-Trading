"""Scheduled research HTTP routes.

Mounted by ``agent/api_server.py`` via ``register_scheduled_routes(app, ...)``.
"""

from __future__ import annotations

import asyncio
import logging
import re
import sys as _sys
import time
import uuid
from typing import Any, Awaitable, Callable, Dict, List, Optional

from fastapi import Depends, FastAPI, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from starlette.responses import Response

from src.config.accessor import get_env_config

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_SCHEDULED_RESEARCH_SCHEDULER_ENV = "VIBE_TRADING_ENABLE_SCHEDULER"
_SCHEDULED_RESEARCH_TRUE_VALUES = {"1", "true", "yes", "on"}

# Deliberately broader than ``_SAFE_PATH_PARAM_RE`` in src/api/helpers.py (which
# every *other* route module's path params are validated against): internally
# generated job ids can contain a colon (e.g. recording_wake_jobs' namespaced
# ``recording_wake:<uuid>``), so job-id path params in this module validate
# against this pattern instead of the generic cross-module helper. Every
# ``{job_id}`` route below (delete/pause/resume/cancel/trigger/stream) must use
# ``_validate_job_id_path_param``, not ``_host_validate_path_param``, so a job
# can never be created under an id one of those routes would then refuse.
_SAFE_JOB_ID_RE = re.compile(r"^[A-Za-z0-9_:-]{1,128}$")


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
    from src.scheduled_research.job_dispatch_registry import try_dispatch_pipeline_job

    if await try_dispatch_pipeline_job(job):
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


def _start_scheduled_research_executor() -> None:
    """Start scheduled research execution when explicitly enabled."""
    from src.api.scheduled_startup import boot_scheduled_research_stack

    boot_scheduled_research_stack(_get_scheduled_research_store)


async def _stop_scheduled_research_executor() -> None:
    """Stop scheduled research execution if it was started."""
    global _scheduled_research_executor
    from src.api.scheduled_startup import shutdown_scheduled_research_stack

    await shutdown_scheduled_research_stack(
        _get_scheduled_research_store, _scheduled_research_executor
    )
    _scheduled_research_executor = None


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
    auto_paused_reason: Optional[str] = None
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
    section: str = "general"


class ScheduledJobPreviewResponse(BaseModel):
    """Plain-English description + best-effort live preview for one job."""

    description: str
    preview_available: bool
    preview_items: List[Any] = Field(default_factory=list)
    preview_note: Optional[str] = None
    preview_error: Optional[str] = None


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
    from src.scheduled_research.sections import job_section

    payload = job.to_dict()
    delivery = payload.pop("delivery", {}) or {}
    last_verdict = payload.pop("last_verdict", None)
    return ScheduledRunResponse(
        **payload,
        last_verdict=last_verdict,
        delivery_status=delivery.get("status", "none"),
        delivery_error=delivery.get("error"),
        delivery_updated_at=delivery.get("updated_at"),
        section=job_section(str(job.config.get("job_type") or "")),
    )


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

AuthDep = Callable[..., Awaitable[Any] | Any]


_RUN_LOG_POLL_SECONDS = 0.5
_RUN_LOG_HEARTBEAT_SECONDS = 15.0


def _run_log_sse_frame(event: str, data: Dict[str, Any]) -> str:
    import json

    payload = json.dumps(data, ensure_ascii=False, default=str)
    return f"event: {event}\ndata: {payload}\n\n"


async def _scheduled_run_log_stream(job_id: str, request: Request):
    """Replay the job's buffered logs, then poll until it leaves RUNNING.

    Mirrors ``_index_prediction_run_event_stream``'s replay-then-poll shape
    (trade_routes.py), backed by the bounded in-memory
    ``run_log_buffer`` instead of a persisted job-record field.
    """
    import time as time_mod

    from src.scheduled_research.models import JobStatus
    from src.scheduled_research.run_log_buffer import get_logs_since

    store = _get_scheduled_research_store()
    last_seq = 0
    last_emit = time_mod.monotonic()
    while True:
        if await request.is_disconnected():
            return

        job = store.get(job_id)
        if job is None:
            yield _run_log_sse_frame("error", {"message": "job not found"})
            return

        for entry in get_logs_since(job_id, last_seq):
            yield _run_log_sse_frame("log", entry)
            last_seq = entry["seq"]
            last_emit = time_mod.monotonic()

        if job.status != JobStatus.RUNNING:
            yield _run_log_sse_frame("status", {"status": job.status.value})
            return

        if time_mod.monotonic() - last_emit >= _RUN_LOG_HEARTBEAT_SECONDS:
            yield ": keepalive\n\n"
            last_emit = time_mod.monotonic()

        await asyncio.sleep(_RUN_LOG_POLL_SECONDS)


def register_scheduled_routes(
    app: FastAPI,
    require_auth: AuthDep | None = None,
    require_event_stream_auth: AuthDep | None = None,
) -> None:
    """Mount the scheduled routes onto ``app``.

    Resolves ``require_auth``/``require_event_stream_auth`` from the host
    ``api_server`` module via ``sys.modules`` when not passed explicitly.
    """
    host = _sys.modules.get("api_server") or _sys.modules.get("agent.api_server")

    if host is None:
        raise RuntimeError(
            "register_scheduled_routes: api_server module not in sys.modules; "
            "ensure api_server is imported before calling this function"
        )

    if require_auth is None:
        require_auth = host.require_auth
    if require_event_stream_auth is None:
        require_event_stream_auth = host.require_event_stream_auth

    def _host_validate_path_param(value: str, kind: str) -> None:
        h = _sys.modules.get("api_server") or _sys.modules.get("agent.api_server")
        h._validate_path_param(value, kind)

    def _validate_job_id_path_param(job_id: str) -> None:
        """Validate a ``{job_id}`` path param against ``_SAFE_JOB_ID_RE``.

        Use this, not ``_host_validate_path_param``, for every job-id route
        in this module — the generic cross-module helper's pattern doesn't
        allow the colon internally-generated ids like ``recording_wake:*``
        use, which would make such a job impossible to pause/cancel/trigger/
        delete/stream through this API even though it was created successfully.
        """
        if not _SAFE_JOB_ID_RE.fullmatch(job_id or ""):
            raise HTTPException(status_code=400, detail="invalid job_id")

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
        _validate_job_id_path_param(job_id)
        removed = _get_scheduled_research_store().delete(job_id)
        if not removed:
            raise HTTPException(
                status_code=404, detail=f"scheduled run {job_id} not found"
            )
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    def _set_job_paused(job_id: str, paused: bool) -> ScheduledRunResponse:
        from src.scheduled_research.pause_control import set_job_enabled

        _validate_job_id_path_param(job_id)
        store = _get_scheduled_research_store()
        job = set_job_enabled(job_id, not paused, store=store)
        if job is None:
            raise HTTPException(
                status_code=404, detail=f"scheduled run {job_id} not found"
            )
        return ScheduledRunResponse(**job.to_dict())

    @app.post(
        "/scheduled-runs/{job_id}/pause",
        response_model=ScheduledRunResponse,
        dependencies=[Depends(require_auth)],
    )
    async def pause_scheduled_run(job_id: str) -> ScheduledRunResponse:
        """Pause a single scheduled job without changing its schedule or config.

        The job is skipped by the executor's due-check until resumed; its
        ``next_run_at`` is left untouched. Goes through the single shared
        ``set_job_enabled`` mutation path so this can never diverge from the
        prediction-jobs panel's pause/resume.
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

    @app.post(
        "/scheduled-runs/{job_id}/cancel",
        response_model=ScheduledRunResponse,
        dependencies=[Depends(require_auth)],
    )
    async def cancel_scheduled_run(job_id: str) -> ScheduledRunResponse:
        """Cancel the job's currently running execution.

        Distinct from pausing: this only affects the in-flight run (sets
        ``status = CANCELLED``) and leaves ``paused`` untouched, so the
        job's future schedule is unaffected. Best-effort/cooperative — it
        sets a ``_cancel_requested`` scratch flag in ``config`` that a
        dispatch coroutine may poll between stages, the same scratch-channel
        pattern already used for ``_last_result_summary``; it does not
        preemptively interrupt a dispatch already in flight.
        """
        from src.scheduled_research.pause_control import (
            JobNotRunningError,
            cancel_running_job,
        )

        _validate_job_id_path_param(job_id)
        store = _get_scheduled_research_store()
        try:
            job = cancel_running_job(job_id, store=store)
        except JobNotRunningError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if job is None:
            raise HTTPException(
                status_code=404, detail=f"scheduled run {job_id} not found"
            )
        return _job_to_response(job)

    @app.post(
        "/scheduled-runs/{job_id}/trigger",
        response_model=ScheduledRunResponse,
        dependencies=[Depends(require_auth)],
    )
    async def trigger_scheduled_run(job_id: str) -> ScheduledRunResponse:
        """Fire this job immediately without changing its paused/enabled state.

        Errors 409 if the job is paused (resume it first) or already
        running. Goes through the shared ``trigger_job_now`` mutation path,
        same pattern as pause/resume/cancel above.
        """
        from src.scheduled_research.pause_control import (
            JobAlreadyRunningError,
            JobPausedError,
            trigger_job_now,
        )

        _validate_job_id_path_param(job_id)
        store = _get_scheduled_research_store()
        try:
            job = trigger_job_now(job_id, store=store)
        except (JobPausedError, JobAlreadyRunningError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if job is None:
            raise HTTPException(
                status_code=404, detail=f"scheduled run {job_id} not found"
            )
        executor = _get_scheduled_research_executor()
        wake = getattr(executor, "wake", None)
        if callable(wake):
            wake()
        return _job_to_response(job)

    @app.get(
        "/scheduled-runs/{job_id}/preview",
        response_model=ScheduledJobPreviewResponse,
        dependencies=[Depends(require_auth)],
    )
    async def get_scheduled_run_preview(job_id: str) -> ScheduledJobPreviewResponse:
        """Plain-English description + best-effort live preview for one job.

        Read-only: never calls the job's real ``run_*_job`` function. A
        preview that can't be cheaply computed for this job type still
        200s with ``preview_available=False`` rather than 404/500ing, and a
        preview callable that raises degrades the same way with
        ``preview_error`` set instead of failing the whole panel.
        """
        from src.scheduled_research.job_details import job_type_detail

        _validate_job_id_path_param(job_id)
        job = _get_scheduled_research_store().get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail=f"scheduled run {job_id} not found")

        job_type = str(job.config.get("job_type") or "")
        detail = job_type_detail(job_type)
        if detail.preview is None:
            return ScheduledJobPreviewResponse(description=detail.description, preview_available=False)
        try:
            result = detail.preview(job.config)
            return ScheduledJobPreviewResponse(
                description=detail.description,
                preview_available=True,
                preview_items=result.get("items", []),
                preview_note=result.get("note"),
            )
        except Exception as exc:  # noqa: BLE001 - degrade gracefully, never 500 the panel
            logger.exception("scheduled run preview failed for job_type=%s", job_type)
            return ScheduledJobPreviewResponse(
                description=detail.description,
                preview_available=False,
                preview_error=str(exc),
            )

    @app.get(
        "/scheduled-runs/{job_id}/stream",
        dependencies=[Depends(require_event_stream_auth)],
    )
    async def stream_scheduled_run_logs(job_id: str, request: Request) -> StreamingResponse:
        """SSE: live-log-tail for one job's in-flight run.

        Emits ``log`` events for buffered output, then a terminal ``status``
        event once the job leaves RUNNING (including "was never running" for
        a row expanded before/after its run). Ticket-authed like the other
        SSE endpoints — an ``EventSource`` can't send an Authorization header.
        """
        _validate_job_id_path_param(job_id)
        if _get_scheduled_research_store().get(job_id) is None:
            raise HTTPException(status_code=404, detail=f"scheduled run {job_id} not found")
        return StreamingResponse(
            _scheduled_run_log_stream(job_id, request),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

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
