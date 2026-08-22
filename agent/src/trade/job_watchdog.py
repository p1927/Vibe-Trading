"""Shared stuck-job watchdog for every file-backed job store.

``index_prediction_run_jobs.py``, ``recording_jobs.py``, and
``external_predictions_run_jobs.py`` each implement the identical
zombie/queued/stale reconciliation pattern (see each module's
``reconcile_job``/``reconcile_all_active_jobs``), but reconciliation only
ever ran when something polled ``get_active_job``/``get_job`` — a crashed
worker for a job nobody is polling (closed tab, unattended cron run) would
sit reporting "running" forever. Rather than run three near-identical
background threads (one per module), this module runs one shared loop that
hydrates and reconciles all three job stores together.
"""

from __future__ import annotations

import logging
import threading

from src.config.accessor import get_env_config

logger = logging.getLogger(__name__)

_WATCHDOG_INTERVAL_SECONDS = float(get_env_config().trade.job_watchdog_interval_seconds)
_watchdog_thread: threading.Thread | None = None
_watchdog_stop = threading.Event()
_watchdog_lock = threading.RLock()


def _hydrate_all() -> None:
    from src.trade import external_predictions_run_jobs, index_prediction_run_jobs, recording_jobs

    for module in (index_prediction_run_jobs, recording_jobs, external_predictions_run_jobs):
        try:
            module.hydrate_jobs_from_disk()
        except Exception:  # pragma: no cover - hydration must never block startup
            logger.exception("job watchdog hydration failed for %s", module.__name__)


def reconcile_all_job_stores() -> int:
    """Run reconciliation over every active job in every job store. Returns total terminalized."""
    from src.trade import external_predictions_run_jobs, index_prediction_run_jobs, recording_jobs

    total = 0
    for module in (index_prediction_run_jobs, recording_jobs, external_predictions_run_jobs):
        try:
            total += module.reconcile_all_active_jobs()
        except Exception:  # pragma: no cover - one store's failure must not skip the others
            logger.exception("job watchdog reconciliation failed for %s", module.__name__)
    return total


def _watchdog_loop(interval_seconds: float) -> None:
    _hydrate_all()
    while not _watchdog_stop.wait(interval_seconds):
        try:
            reconciled = reconcile_all_job_stores()
            if reconciled:
                logger.warning("job watchdog reconciled %d job(s)", reconciled)
        except Exception:  # pragma: no cover - watchdog must never die
            logger.exception("job watchdog iteration failed")


def start_job_watchdog(interval_seconds: float | None = None) -> None:
    """Start the shared background reconciliation loop (idempotent, safe to call repeatedly)."""
    global _watchdog_thread
    with _watchdog_lock:
        if _watchdog_thread is not None and _watchdog_thread.is_alive():
            return
        _watchdog_stop.clear()
        _watchdog_thread = threading.Thread(
            target=_watchdog_loop,
            args=(interval_seconds if interval_seconds is not None else _WATCHDOG_INTERVAL_SECONDS,),
            name="job-watchdog",
            daemon=True,
        )
        _watchdog_thread.start()


def stop_job_watchdog(timeout: float = 5.0) -> None:
    """Stop the shared background reconciliation loop, if running."""
    global _watchdog_thread
    _watchdog_stop.set()
    with _watchdog_lock:
        thread = _watchdog_thread
        _watchdog_thread = None
    if thread is not None:
        thread.join(timeout=timeout)
