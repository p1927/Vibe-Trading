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
from typing import Any

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def run_worker(job_id: str) -> None:
    # This process's stdout/stderr is redirected to worker.log with no
    # other logging config in the call path — without this, every
    # logger.info/warning call (e.g. tick_stream's connect/subscribe/
    # close diagnostics) is silently dropped by Python's unconfigured
    # root logger instead of reaching the log file.
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    from src.trade.hub_bridge import ensure_trade_stack_path
    from src.trade import recording_jobs as jobs

    job = jobs.get_job(job_id)
    if job is None:
        return

    underlyings = list(job.get("underlyings") or [])
    equities = list(job.get("equities") or [])
    poll_interval_s = int(job.get("poll_interval_s") or 10)
    # Per-category intervals. None means "fall back to poll_interval_s
    # for all three REST categories" (the recorder applies this fallback
    # in run_recording_session).
    raw_category_intervals = job.get("category_intervals")
    category_intervals: dict[str, float] | None = (
        {k: float(v) for k, v in raw_category_intervals.items()}
        if isinstance(raw_category_intervals, dict)
        else None
    )
    raw_equity_intervals = job.get("equity_intervals")
    equity_intervals: dict[str, float] | None = (
        {k: float(v) for k, v in raw_equity_intervals.items()}
        if isinstance(raw_equity_intervals, dict)
        else None
    )
    raw_ws_throttle = job.get("ws_throttle_hz")
    ws_throttle_hz: float | None = (
        float(raw_ws_throttle) if isinstance(raw_ws_throttle, (int, float)) and raw_ws_throttle > 0
        else None
    )
    raw_historical = job.get("historical_config")
    historical_config: dict[str, Any] | None = (
        {"interval": str(raw_historical["interval"]),
         "lookback_days": int(raw_historical["lookback_days"])}
        if isinstance(raw_historical, dict)
        and "interval" in raw_historical
        and "lookback_days" in raw_historical
        else None
    )
    # Phase C: ``wait_for_open`` is no longer a worker-side concern.
    # The recorder only spawns when NSE hours are already open (the
    # API entry schedules a deferred wake via the scheduled-research
    # executor otherwise), so the worker can always promote
    # ``queued → running`` unconditionally.
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
            equities=equities,
            poll_interval_s=poll_interval_s,
            category_intervals=category_intervals,
            equity_intervals=equity_intervals,
            ws_throttle_hz=ws_throttle_hz,
            historical_config=historical_config,
            data_root=Path(data_root),
            on_log=on_log,
            should_stop=should_stop,
            # Phase C: the worker only runs when NSE hours are open
            # (cron-driven respawn owns the wait path). Pass False
            # unconditionally so the recorder's wait branch is
            # never entered.
            wait_for_open=False,
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

        # End-of-session macro/flow gap-fill is deliberately detached
        # from this worker's critical path. ``run_recording_session``
        # used to run ``StockHistory().supplement_today(...)``
        # synchronously here, which could take many minutes (it walks
        # the yfinance/searxng/crawl4ai chain and may invoke the LLM
        # for some buckets). That blocked ``complete_job`` from firing
        # and left the SSE event stream showing ``status="running"``
        # for the whole supplement window, so the UI's Stop button
        # appeared non-functional. Spawning it as a separate
        # subprocess means the recorder exits within ~1s of Stop, the
        # SSE delivers the ``done`` event immediately, and the
        # supplement continues independently — including honouring the
        # same stop flag if the user clicks Stop again.
        try:
            from src.trade.recording_supplement_worker import (
                spawn_supplement_worker,
            )

            pid = spawn_supplement_worker(
                job_id, result.session_date, cycles=result.cycles
            )
            if pid is not None:
                jobs.append_log(
                    job_id,
                    {
                        "stage": "supplement",
                        "message": "spawned detached subprocess",
                        "level": "info",
                        "at": _now_iso(),
                    },
                )
        except Exception as exc:
            # Spawn failure must not flip the already-``done`` job back
            # to ``error`` — operators can see the failure in
            # supplement.log and re-run manually.
            logger.warning(
                "failed to spawn supplement subprocess (job=%s): %s",
                job_id,
                exc,
            )
            jobs.append_log(
                job_id,
                {
                    "stage": "supplement",
                    "message": f"spawn failed: {exc}",
                    "level": "warning",
                    "at": _now_iso(),
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
