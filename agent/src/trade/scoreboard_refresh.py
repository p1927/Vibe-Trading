"""The one track-scoreboard recompute path: the POST route and the scheduled job both start it here.

The walk-forward takes minutes, so it runs on its own thread, one at a time per process. A second
start while one runs returns the running job (`started: False`) instead of queueing another.
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from typing import Any

from src.api.helpers import redacted_error_detail

logger = logging.getLogger(__name__)

_LOCK = threading.Lock()
_JOB: dict[str, Any] = {}  # latest recompute: {job_id, state, ticker, started_at, finished_at, error}
_THREAD: threading.Thread | None = None


def current_job() -> dict[str, Any] | None:
    return dict(_JOB) or None


def history_days(key: str, days: int) -> int:
    """A refresh must never silently shrink an already-cached wider window back to the 730-day floor
    ([[2026-08-29-strategy-promotion-default-history-window-too-short-for-low-frequency-candidates]]):
    a wide window (an explicit `days=5000` run for a low-frequency strategy) is sticky."""
    from src.trade.hub_bridge import ensure_trade_stack_path

    ensure_trade_stack_path()
    from trade_integrations.dataflows.index_research.prediction_algorithms.evaluator.scoreboard import load_scoreboard

    cached_history_days = int((load_scoreboard(key) or {}).get("history_days") or 0)
    return max(days, 730, cached_history_days)


def _run(job: dict[str, Any], key: str, days: int, horizon_days: int | None, eval_step: int) -> None:
    try:
        from src.trade.hub_bridge import ensure_trade_stack_path

        ensure_trade_stack_path()
        from trade_integrations.dataflows.index_research.prediction_algorithms.evaluator.walk_forward import (
            run_strategy_walk_forward,
            run_track_walk_forward,
        )

        run_track_walk_forward(ticker=key, days=days, horizon_days=horizon_days, eval_step=eval_step)
        run_strategy_walk_forward(ticker=key, days=days, horizon_days=horizon_days)
        job["state"] = "done"
    except Exception as exc:  # the job's state is the only place a background failure can surface
        logger.exception("track-scoreboard recompute failed for %s", key)
        job["state"] = "failed"
        job["error"] = redacted_error_detail(exc)
    finally:
        job["finished_at"] = datetime.now(timezone.utc).isoformat()
        _LOCK.release()


def start_refresh(
    key: str, days: int = 365, horizon_days: int | None = None, eval_step: int = 5
) -> tuple[bool, dict[str, Any]]:
    """Start the recompute unless one runs. Returns (started, job); poll `current_job()` for `state`."""
    global _THREAD
    if not _LOCK.acquire(blocking=False):
        return False, dict(_JOB)
    try:
        _JOB.clear()
        _JOB.update(
            {
                "job_id": f"scoreboard-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')}",
                "state": "running",
                "ticker": key,
                "started_at": datetime.now(timezone.utc).isoformat(),
                "finished_at": None,
                "error": None,
            }
        )
        _THREAD = threading.Thread(
            target=_run,
            args=(_JOB, key, history_days(key, days), horizon_days, eval_step),
            name="track-scoreboard-refresh",
            daemon=True,
        )
        _THREAD.start()
    except Exception:
        _LOCK.release()
        raise
    return True, dict(_JOB)


def refresh_and_wait(key: str, days: int = 365, horizon_days: int | None = None, eval_step: int = 5) -> dict[str, Any]:
    """The scheduled job's entry: the same `start_refresh`, then block until the run ends. Raises when it
    failed, so the scheduler records a failed run. A run already in flight is reported, not duplicated."""
    started, job = start_refresh(key, days, horizon_days, eval_step)
    if not started:
        return {"status": "skipped", "reason": "refresh_in_flight", "job": job}
    thread = _THREAD
    if thread is not None:
        thread.join()
    final = current_job() or job
    if final.get("state") != "done":
        raise RuntimeError(f"track-scoreboard recompute for {key} {final.get('state')}: {final.get('error')}")
    return {"status": "ok", "job": final}
