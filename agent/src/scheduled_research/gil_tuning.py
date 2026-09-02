"""GIL-switch-interval tuning for the scheduled-research executor.

Fork-only sidecar, called once at process startup from
``api_server.py``'s ``_run_startup_preflight`` (an upstream file) —
not itself part of the scheduler's own dispatch logic.

Background: the scheduler runs sync dispatch bodies off the event loop via
``asyncio.to_thread`` (see ``run_log_buffer.run_logged``), which is normally
enough for I/O-bound work to interleave cleanly with the main asyncio event
loop. But a job type whose dispatch body does sustained CPU-bound Python/
numpy work (live-observed: repeated ``shap.LinearExplainer`` calls reached
via ``explain_macro_factors`` — see
[[2026-09-02-shap-blocks-scheduler-event-loop]]) still competes with the
main thread for the GIL. Python's default switch interval (5ms,
``sys.getswitchinterval()``) is tuned for general-purpose fairness, not for
"a CPU-heavy worker thread must not starve an event loop that's mostly
servicing I/O-bound siblings" — live-verified 2026-09-02: a due, unpaused
job sat undispatched for 5+ minutes while one such CPU-heavy job type ran,
because the main thread rarely got a GIL slice long enough to advance its
own scheduling.
"""

from __future__ import annotations

import logging
import sys

logger = logging.getLogger(__name__)

# Default is 0.005s (5ms). Lowering it makes CPython attempt a GIL handoff
# far more often, giving the main thread (running the asyncio event loop)
# many more chances to actually get scheduled while a CPU-bound worker
# thread runs — small aggregate throughput cost to the busy worker, in
# exchange for the main thread no longer being starved for minutes at a
# time. This is a mitigation for GIL fairness, not a fix for the CPU cost
# itself — a structural fix (bounding how much CPU-heavy work one dispatch
# does per tick, or process-pool isolation for the SHAP call specifically)
# remains open, see [[2026-09-02-shap-blocks-scheduler-event-loop]].
_TUNED_SWITCH_INTERVAL_SECONDS = 0.001


def tune_gil_switch_interval_for_scheduler() -> None:
    """Lower the interpreter's GIL switch interval, once, at process startup.

    Idempotent: safe to call more than once (e.g. in tests) — always sets
    the same value rather than accumulating adjustments.
    """
    previous = sys.getswitchinterval()
    sys.setswitchinterval(_TUNED_SWITCH_INTERVAL_SECONDS)
    logger.info(
        "GIL switch interval tuned for scheduler fairness: %.4fs -> %.4fs",
        previous,
        _TUNED_SWITCH_INTERVAL_SECONDS,
    )
