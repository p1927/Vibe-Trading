"""Run one heavy scheduled job's sync dispatch in a child process (Trade D244, applying D204).

Fork-only sidecar (docs/FORK_CONVENTIONS.md). A job's sync dispatch normally runs on an
``asyncio.to_thread`` worker inside the API process (``run_log_buffer.run_logged``). A CPU-heavy one
there shares the GIL with the event loop and every other job: the DST prediction eval ran ~8x
slower beside other heavy jobs and hit its 2700 s timeout, and the timed-out thread kept running,
because nothing can stop a thread. So a heavy job type's dispatch module passes
``in_child(dispatch_sync)`` to ``run_logged`` instead of ``dispatch_sync``. The API worker thread
then only supervises (``trade_integrations.child_process.run_supervised``):

- the child runs ``dispatch_sync(job)`` itself (``main`` below) and writes back the job's config
  (where a dispatch leaves its ``LAST_RESULT_CONFIG_KEY`` summary) and its outcome;
- every line the child prints (its logging included) goes to the job's live log tail and to this
  process's log;
- the executor's dispatch budget (``trade_integrations.job_deadline``) is the child's timeout: at
  the deadline the child's whole process group is killed, so no work outlives a timed-out run. The
  child binds the same wall-clock deadline to its own ``job_deadline``, so its LLM retry ladders
  still bound themselves by what is left and degrade before the kill;
- a line the dispatch writes to the job's live log (``run_log_buffer.append_log``, e.g. its stage
  sink) is printed by the child and so reaches the job's log in this process;
- the child exits with ``os._exit`` once its result is written, and its process group is killed
  after it: a thread or subprocess it abandoned (a timed-out per-ticker worker, a browser a
  library started) ends with the run;
- a cancel aimed at the job (``pipeline_cancel``, a file flag both processes read) gets
  ``_CANCEL_GRACE_SECONDS`` for the child to stop by itself, then kills it;
- the child dies with the API process (``die_with_parent``);
- a failure in the child is raised here, so the executor records it like any other (D36, D103).
"""

from __future__ import annotations

import functools
import importlib
import json
import logging
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)

#: How long a cancelled child gets to stop at its own next cancel checkpoint before it is killed.
_CANCEL_GRACE_SECONDS = 10.0
#: The directory the API runs from (``stack_start_vibe_api``); ``src.*`` resolves from it.
_AGENT_DIR = Path(__file__).resolve().parents[2]


def in_child(dispatch_sync: Callable[[Any], None]) -> Callable[[Any], None]:
    """``dispatch_sync``, run in a supervised child process. Pass the result to ``run_logged``."""
    return functools.partial(run_in_child, dispatch_sync=dispatch_sync)


def run_in_child(job: Any, *, dispatch_sync: Callable[[Any], None]) -> None:
    """Supervise ``dispatch_sync(job)`` in a child process. Runs on the ``run_logged`` worker thread."""
    from src.scheduled_research.run_log_buffer import append_log
    from src.trade.hub_bridge import ensure_trade_stack_path

    ensure_trade_stack_path()
    from trade_integrations.child_process import CANCELLED_EXIT_CODE, TIMEOUT_EXIT_CODE, run_supervised
    from trade_integrations.dataflows.index_research.pipeline_cancel import (
        PipelineCancelledError,
        pipeline_job_scope,
        set_stage_sink,
    )
    from trade_integrations.job_deadline import remaining_seconds

    def _line(message: str) -> None:
        append_log(job.id, message)
        logger.info("[child %s] %s", job.id, message)

    target = f"{dispatch_sync.__module__}:{dispatch_sync.__qualname__}"
    timeout = remaining_seconds()
    # Wall-clock, not monotonic: the child's clock origin differs. "none" = no budget bound.
    deadline_epoch = "none" if timeout is None else repr(time.time() + timeout)
    with tempfile.TemporaryDirectory(prefix="job-child-") as tmp, pipeline_job_scope(job.id):
        job_in, result_out = Path(tmp) / "job.json", Path(tmp) / "result.json"
        job_in.write_text(json.dumps(job.to_dict()), encoding="utf-8")
        set_stage_sink(_line)
        try:
            rc, _stdout, stderr = run_supervised(
                [sys.executable, "-m", __name__, target, str(job_in), str(result_out), deadline_epoch],
                cwd=_AGENT_DIR,
                timeout_seconds=timeout,
                cancel_grace_seconds=_CANCEL_GRACE_SECONDS,
                own_process_group=True,
            )
        finally:
            set_stage_sink(None)
        result = json.loads(result_out.read_text(encoding="utf-8")) if result_out.is_file() else None
    if rc == TIMEOUT_EXIT_CODE:
        raise TimeoutError(f"child process killed at the dispatch budget ({timeout:.0f}s)")
    if result is not None:
        job.config.update(result["config"])
        if result["cancelled"]:
            raise PipelineCancelledError(result["cancelled"])
        if result["error"]:
            raise RuntimeError(result["error"])
        if rc == 0:
            return
    last = (stderr.strip().splitlines() or ["no output"])[-1]
    if rc == CANCELLED_EXIT_CODE:
        raise PipelineCancelledError(f"child process killed after cancel {last}")
    raise RuntimeError(f"child process for {target} exited {rc} without a result: {last}")


def main(argv: list[str] | None = None) -> int:
    """Child entry point:
    ``python -m src.scheduled_research.child_dispatch <module:fn> <job.json> <result.json> [<deadline epoch>|none]``.
    """
    args = list(argv if argv is not None else sys.argv[1:])
    target, job_path, result_path = args[:3]
    deadline_epoch = args[3] if len(args) > 3 else "none"
    from src.scheduled_research import run_log_buffer
    from src.scheduled_research.models import ScheduledResearchJob
    from src.trade.hub_bridge import ensure_trade_stack_path

    ensure_trade_stack_path()
    from trade_integrations.child_process import die_with_parent
    from trade_integrations.dataflows.index_research.pipeline_cancel import PipelineCancelledError
    from trade_integrations.job_deadline import job_deadline

    die_with_parent()
    logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="%(levelname)s %(name)s: %(message)s")
    run_log_buffer.print_instead_of_buffering()
    job = ScheduledResearchJob.from_dict(json.loads(Path(job_path).read_text(encoding="utf-8")))
    module, _, name = target.partition(":")
    dispatch_sync = getattr(importlib.import_module(module), name)
    budget = None if deadline_epoch == "none" else float(deadline_epoch) - time.time()
    result: dict[str, Any] = {"cancelled": None, "error": None}
    try:
        with job_deadline(budget):
            dispatch_sync(job)
    except PipelineCancelledError as exc:
        result["cancelled"] = exc.reason
    except Exception as exc:
        logger.exception("child dispatch %s failed for job %s", target, job.id)
        result["error"] = f"{type(exc).__name__}: {exc}"
    result["config"] = job.config
    Path(result_path).write_text(json.dumps(result, default=str), encoding="utf-8")
    return 1 if result["error"] else 0


if __name__ == "__main__":
    # os._exit, not SystemExit: interpreter shutdown joins every non-daemon thread (a
    # ThreadPoolExecutor worker a dispatch abandoned at its own timeout), which would keep the run
    # going past its result. The result file is already written; flush the pipes and go.
    code = main()
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(code)
