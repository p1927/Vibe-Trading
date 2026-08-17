"""Detached end-of-session supplement worker for stock-simulator recording.

Runs the macro/flow gap-fill (``StockHistory().supplement_today(...)``) in
its own subprocess so the recorder's main worker can return immediately
after ``complete_job`` writes ``status="done"`` to ``job.json``.

Why this lives in its own process
---------------------------------

The previous design called ``supplement_today`` *synchronously* at the
end of ``run_recording_session``. Supplement walks the
``yfinance -> searxng -> crawl4ai`` chain and may invoke the LLM for
some buckets, so it can take many minutes. Because it sat on the
recorder's critical path between "loop exited" and "return result",
``complete_job`` never fired and the SSE event stream kept showing
``status="running"`` for the entire supplement window — the UI's Stop
button appeared non-functional because the badge never flipped to
``done``. Moving supplement here turns the recorder worker into a fast
producer (``status="done"`` within a second of Stop) and the supplement
into a slow background consumer that cannot gate the lifecycle.

Lifecycle contract
-------------------

- Spawned by ``recording_worker.run_worker`` via
  ``spawn_supplement_worker(job_id, session_date)``.
- Owns a dedicated log file (``log/recording_jobs/<job_id>/supplement.log``)
  — never shares the recorder's ``worker.log``.
- Writes progress to the job log via ``recording_jobs.append_log``
  (tagged ``stage="supplement"``) but never mutates ``status`` /
  ``result`` — the job is already ``done`` and the SSE stream must
  not see a flip back to ``running``.
- Honours ``stop_flag_path(job_id)`` cooperatively between buckets so a
  stuck crawl4ai job can be aborted by the same user action that
  stopped the recorder.
- Isolated ``try/except`` around the whole run — a failing supplement
  appends a single error log entry and exits non-zero, never crashes the
  recording job (which is already done).

Runs as ``python -m src.trade.recording_supplement_worker <job_id>
<session_date>``.
"""

from __future__ import annotations

import logging
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _log_path(job_id: str) -> Path:
    """Mirror the recording worker's log layout so each job's logs are
    co-located on disk for post-mortem."""
    from src.trade.recording_jobs import _job_dir

    return _job_dir(job_id) / "supplement.log"


def run_supplement(job_id: str, session_date: str) -> None:
    """Blocking end-of-session supplement. Called as ``__main__``.

    ``session_date`` is the ISO date string the recorder used
    (e.g. ``"2026-08-17"``). Passed as a CLI arg so the subprocess has
    no in-memory dependency on the recording worker's Python objects —
    a hot-reloaded API process restarting this subprocess still works.
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    from src.trade.hub_bridge import ensure_trade_stack_path
    from src.trade import recording_jobs as jobs

    job = jobs.get_job(job_id)
    if job is None:
        # The job was pruned (1h TTL) before the supplement finished.
        # Nothing useful we can write to its logs — log to our own
        # log file and exit so the parent watchdog doesn't escalate.
        logger.warning(
            "supplement worker: job_id=%s no longer in store (pruned?) — exiting",
            job_id,
        )
        return

    def log(entry: dict[str, Any]) -> None:
        jobs.append_log(job_id, entry)

    log(
        {
            "stage": "supplement",
            "message": f"start (session_date={session_date})",
            "level": "info",
            "at": _now_iso(),
        }
    )

    def should_abort() -> bool:
        """Honour the same stop flag the recorder watches — a single
        user "Stop" gesture applies to both the recorder and any
        post-session work that's still in flight."""
        return jobs.stop_flag_path(job_id).is_file()

    try:
        ensure_trade_stack_path()
        from trade_integrations.stock_history.api import StockHistory

        # Defensive parse so a malformed session_date (e.g. "today"
        # leaking from a buggy caller) doesn't blow up the subprocess.
        try:
            parsed = date.fromisoformat(session_date)
        except ValueError:
            parsed = datetime.now().astimezone().date()

        if should_abort():
            log(
                {
                    "stage": "supplement",
                    "message": "aborted before start (stop flag set)",
                    "level": "warning",
                    "at": _now_iso(),
                }
            )
            return

        summary = StockHistory().supplement_today(session_date=parsed)

        log(
            {
                "stage": "supplement",
                "message": (
                    f"complete: had_errors={summary.had_errors} "
                    f"ok={summary.ok_count} "
                    f"failed={summary.failed_count} "
                    f"skipped={summary.skipped_count}"
                ),
                "level": "info" if not summary.had_errors else "warning",
                "at": _now_iso(),
            }
        )
    except Exception as exc:
        # Isolated: the recording job is already "done" on disk — a
        # failing supplement must NEVER flip it back. Just append a
        # tagged log entry so operators can see what went wrong without
        # the UI churning through state changes.
        logger.exception("supplement worker failed (job=%s)", job_id)
        log(
            {
                "stage": "supplement",
                "message": f"failed: {exc}",
                "level": "error",
                "at": _now_iso(),
            }
        )


def _agent_dir() -> Path:
    here = Path(__file__).resolve()
    return here.parents[1]


def spawn_supplement_worker(job_id: str, session_date: str) -> int | None:
    """Launch the supplement as its own detached subprocess.

    Mirrors ``recording_jobs.spawn_worker``:
      - ``start_new_session=True`` so the supplement survives the
        recording worker exiting and isn't killed by Ctrl-C sent to
        the parent agent process.
      - stdout/stderr redirected to ``supplement.log`` so a hung
        crawl4ai call is debuggable after the fact.
      - the returned PID is informational only; callers should not
        wait on it (the supplement is fire-and-forget).
    """
    agent_dir = _agent_dir()
    log_path = _log_path(job_id)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_handle = log_path.open("ab")

    import os
    import subprocess

    env = os.environ.copy()
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "src.trade.recording_supplement_worker",
            job_id,
            session_date,
        ],
        cwd=str(agent_dir),
        env=env,
        start_new_session=True,
        stdout=log_handle,
        stderr=subprocess.STDOUT,
    )
    log_handle.close()
    return proc.pid


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(
            "usage: python -m src.trade.recording_supplement_worker <job_id> <session_date>",
            file=sys.stderr,
        )
        sys.exit(2)
    run_supplement(sys.argv[1], sys.argv[2])