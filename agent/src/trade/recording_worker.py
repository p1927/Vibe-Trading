"""Detached worker entry point for a stock-simulator recording job.

Invoked as ``python -m src.trade.recording_worker <job_id>`` by
`recording_jobs.spawn_worker`. Blocking — runs for the remainder of the
trading day (or until manually stopped).
"""

from __future__ import annotations

import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def run_worker(job_id: str) -> None:
    from src.trade.hub_bridge import ensure_trade_stack_path
    from src.trade import recording_jobs as jobs

    job = jobs.get_job(job_id)
    if job is None:
        return

    underlyings = list(job.get("underlyings") or [])
    poll_interval_s = int(job.get("poll_interval_s") or 10)
    wait_for_open = bool(job.get("wait_for_open"))

    jobs.mark_running(job_id)

    def on_log(entry: dict) -> None:
        jobs.append_log(job_id, entry)

    def should_stop() -> bool:
        return jobs.stop_flag_path(job_id).is_file()

    try:
        ensure_trade_stack_path()
        from trade_integrations.stock_simulator.config import load_sim_config
        from trade_integrations.stock_simulator.recorder.session_recorder import (
            run_recording_session,
        )

        data_root = load_sim_config().data_root
        result = run_recording_session(
            job_id,
            underlyings=underlyings,
            poll_interval_s=poll_interval_s,
            data_root=Path(data_root),
            on_log=on_log,
            should_stop=should_stop,
            wait_for_open=wait_for_open,
        )
        jobs.complete_job(
            job_id,
            result={
                "session_date": result.session_date,
                "underlyings": result.underlyings,
                "stopped_reason": result.stopped_reason,
                "cycles": result.cycles,
                "errors": result.errors,
            },
        )
    except Exception as exc:
        logger.exception("recording worker failed (job=%s)", job_id)
        jobs.append_log(
            job_id,
            {"stage": "error", "message": str(exc), "level": "error", "at": _now_iso()},
        )
        jobs.fail_job(job_id, str(exc))


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: python -m src.trade.recording_worker <job_id>", file=sys.stderr)
        sys.exit(1)
    run_worker(sys.argv[1])
