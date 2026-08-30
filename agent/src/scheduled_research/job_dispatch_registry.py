"""Registry of scheduled-research pipeline job dispatchers.

Fork-only sidecar for ``agent/src/api/scheduled_routes.py`` (an upstream
file): each pipeline module owns a ``<PREFIX>_JOB_TYPES`` frozenset and a
``dispatch_<name>_job(job)`` coroutine. Modeled on
``agent/src/channels/registry.py``'s auto-discovery pattern, but kept
explicit (an ordered tuple, not filesystem discovery) since dispatch order
matters here and there are few enough pipelines that discovery would add
indirection without buying anything.

Each loader below is a separate function so a pipeline module is only
imported when its job-type set is actually checked, matching the original
inline cascade's import-cost-avoidance (most dispatch calls never need to
import every pipeline module).
"""

from __future__ import annotations

from typing import Awaitable, Callable


def _load_index():
    from src.scheduled_research.index_jobs import INDEX_JOB_TYPES, dispatch_index_job

    return INDEX_JOB_TYPES, dispatch_index_job


def _load_options():
    from src.scheduled_research.options_jobs import OPTIONS_JOB_TYPES, dispatch_options_job

    return OPTIONS_JOB_TYPES, dispatch_options_job


def _load_trade_data():
    from src.scheduled_research.trade_data_jobs import TRADE_DATA_JOB_TYPES, dispatch_trade_data_job

    return TRADE_DATA_JOB_TYPES, dispatch_trade_data_job


def _load_hub_calibration():
    from src.scheduled_research.hub_calibration_jobs import (
        HUB_CALIBRATION_JOB_TYPES,
        dispatch_hub_calibration_job,
    )

    return HUB_CALIBRATION_JOB_TYPES, dispatch_hub_calibration_job


def _load_hub_capture():
    from src.scheduled_research.capture_jobs import HUB_CAPTURE_JOB_TYPES, dispatch_hub_capture_job

    return HUB_CAPTURE_JOB_TYPES, dispatch_hub_capture_job


def _load_autonomous():
    from src.scheduled_research.autonomous_agent_jobs import AUTONOMOUS_JOB_TYPES, dispatch_autonomous_job

    return AUTONOMOUS_JOB_TYPES, dispatch_autonomous_job


def _load_recording_wake():
    # Phase C: recording-wake jobs (cron-driven respawn of
    # ``wait_for_open=True`` recordings). See
    # ``src.scheduled_research.recording_wake_jobs``.
    from src.scheduled_research.recording_wake_jobs import (
        RECORDING_WAKE_JOB_TYPES,
        dispatch_recording_wake_job,
    )

    return RECORDING_WAKE_JOB_TYPES, dispatch_recording_wake_job


def _load_dst_eval():
    from src.scheduled_research.dst_eval_jobs import DST_EVAL_JOB_TYPES, dispatch_dst_eval_job

    return DST_EVAL_JOB_TYPES, dispatch_dst_eval_job


# Order matters only in that it's the sequence checked; job-type sets are
# disjoint in practice so it has no behavioral effect today.
_DISPATCH_LOADERS: tuple[Callable[[], tuple[frozenset, Callable[..., Awaitable[None]]]], ...] = (
    _load_index,
    _load_options,
    _load_trade_data,
    _load_hub_calibration,
    _load_hub_capture,
    _load_autonomous,
    _load_recording_wake,
    _load_dst_eval,
)


async def try_dispatch_pipeline_job(job) -> bool:
    """Dispatch ``job`` to its pipeline handler if its job_type is registered.

    Returns True when a pipeline handled the job (caller should not fall
    through to the legacy agent-session enqueue path), False when no
    registered pipeline claims this job_type.
    """
    job_type = str(job.config.get("job_type") or "")
    for loader in _DISPATCH_LOADERS:
        job_types, dispatch_fn = loader()
        if job_type in job_types:
            await dispatch_fn(job)
            return True
    return False
