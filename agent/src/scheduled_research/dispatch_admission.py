"""Shortest-expected-runtime-first admission into the executor's dispatch slots.

Fork-only sidecar for ``executor.py`` (an upstream file), per docs/FORK_CONVENTIONS.md:
the executor keeps one :class:`DispatchPool` per dispatch loop and hands it the due jobs
each tick; everything about *which* waiting job gets a free slot lives here.

Implements docs/DECISIONS.md D11 — "when scheduled jobs contend for the same concurrency
slots, the job expected to finish soonest goes first" — through three mechanics:

(a) **The tick never blocks on a dispatch.** The pool outlives any one tick. A tick offers
    its due jobs and returns; a slot freed by a finishing dispatch is refilled at once from
    the waiters. A job already waiting or running is never offered twice, so it can never
    be dispatched twice. Before this, ``tick()`` awaited the whole gathered set, so a job
    that became due while two 90-minute ingests ran was not even *seen* until both
    finished (measured 2026-09-10: a ``*/15`` job waited 48+ minutes).

(b) **Shortest expected runtime first, and the last slot is reserved.** Waiters are ordered
    by :func:`expected_runtime_ms` (the job's own recent measured durations, falling back
    to its ``dispatch_timeout_ms`` budget). A job whose expected runtime is *long* — see
    :func:`long_runtime_threshold_ms`, one rule derived from the job's own type, never from
    job names — may not take the **last** free slot of its per-type cap, so a short job of
    that type always has one.

(c) **Ageing.** A waiter overdue by at least :data:`AGEING_CADENCE_MULTIPLE` of its own
    cadence is *aged*: it sorts ahead of everything that is not, and may take the reserved
    slot. That is what keeps shortest-first from starving a long job forever.

The per-type caps themselves (``staleness._JOB_TYPE_DISPATCH_CONCURRENCY``) are unchanged:
D11 fixes a fairness problem, it does not buy capacity.
"""

from __future__ import annotations

import asyncio
import logging
import statistics
import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Iterable

from src.scheduled_research.models import ScheduledResearchJob
from src.scheduled_research.staleness import dispatch_concurrency_for_job_type, dispatch_timeout_ms_for

logger = logging.getLogger(__name__)

#: ``job.config`` scratch key holding the job's most recent dispatch durations (ms), oldest
#: first. Lives in ``config`` for the same reason ``_timed_out``/``_last_result_summary`` do:
#: the executor already persists ``config`` with every completion, and default-job
#: registration merges (rather than replaces) ``config``, so the history survives restarts
#: without a store/model schema change.
DURATION_HISTORY_CONFIG_KEY = "_recent_durations_ms"
#: How many recent durations are kept. The median of these is the expected runtime, so one
#: outlier run (a timeout, a cold cache) does not reclassify a job.
DURATION_HISTORY_LEN = 5
#: A job is *long* when its expected runtime exceeds this multiple of the median expected
#: runtime of all jobs of its type. For ``hub_news_ingest`` today that puts every ``-full``
#: variant (50-90 min measured, 90 min budget) above the line and every ``-light``/``-tight``
#: (1-14 min measured, 20 min budget) below it, with margin either side — and it says so
#: from runtimes alone, with no knowledge of those names.
LONG_RUNTIME_FACTOR = 3.0
#: A waiter overdue by at least this many of its own cadence intervals is *aged*: it has
#: missed a whole run it was owed, which is the point where waiting stops being fair.
AGEING_CADENCE_MULTIPLE = 1.0

RunFn = Callable[[ScheduledResearchJob, int], Awaitable[None]]
NextDueFn = Callable[[str, int, "str | None"], int]


def _job_type(job: ScheduledResearchJob) -> str:
    return str((job.config or {}).get("job_type") or "")


def record_dispatch_duration(job: ScheduledResearchJob, elapsed_ms: int) -> None:
    """Append one measured dispatch duration to *job*'s history (mutates ``job.config``).

    Every attempt counts, a timed-out one included: a timeout means the job ran for its
    whole budget, which is a true lower bound on what it costs a slot.
    """
    raw = job.config.get(DURATION_HISTORY_CONFIG_KEY)
    history = [int(v) for v in raw if isinstance(v, int) and v >= 0] if isinstance(raw, list) else []
    history.append(max(0, int(elapsed_ms)))
    job.config[DURATION_HISTORY_CONFIG_KEY] = history[-DURATION_HISTORY_LEN:]


def expected_runtime_ms(job: ScheduledResearchJob) -> int:
    """Median of the job's recent measured durations, else its ``dispatch_timeout_ms`` budget."""
    raw = (job.config or {}).get(DURATION_HISTORY_CONFIG_KEY)
    if isinstance(raw, list):
        history = [v for v in raw if isinstance(v, int) and not isinstance(v, bool) and v >= 0]
        if history:
            return int(statistics.median(history))
    return dispatch_timeout_ms_for(job)


def long_runtime_threshold_ms(population: Iterable[ScheduledResearchJob], job_type: str) -> float | None:
    """Expected runtime above which a job of *job_type* counts as long, or ``None`` if unknown.

    ``LONG_RUNTIME_FACTOR`` x the median expected runtime of every job of that type in
    *population*. Relative to the type's own jobs because the slots being contended for are
    that type's slots: "long" means "long compared with what else needs these slots".
    """
    runtimes = [expected_runtime_ms(j) for j in population if _job_type(j) == job_type]
    if not runtimes:
        return None
    return LONG_RUNTIME_FACTOR * statistics.median(runtimes)


def cadence_ms(job: ScheduledResearchJob, next_due_fn: NextDueFn) -> int | None:
    """The job's own cadence: the gap from the slot it is due for to the following one."""
    try:
        following = next_due_fn(job.schedule, int(job.next_run_at), job.timezone)
    except Exception:
        # Not swallowed: an unadvanceable schedule is surfaced as a FAILED job by
        # `_run_job` the moment it is admitted. Here it only means "cannot be aged".
        return None
    gap = following - int(job.next_run_at)
    return gap if gap > 0 else None


def is_aged(job: ScheduledResearchJob, now_ms: int, next_due_fn: NextDueFn) -> bool:
    """Whether *job* has waited at least ``AGEING_CADENCE_MULTIPLE`` of its cadence past due."""
    cadence = cadence_ms(job, next_due_fn)
    if cadence is None:
        return False
    return now_ms - int(job.next_run_at) >= AGEING_CADENCE_MULTIPLE * cadence


@dataclass
class _Waiter:
    job: ScheduledResearchJob
    run: RunFn
    enqueued_now_ms: int
    enqueued_monotonic: float
    done: list[asyncio.Future] = field(default_factory=list)

    @property
    def job_type(self) -> str:
        return _job_type(self.job)

    def now_ms(self) -> int:
        """The tick clock advanced by real elapsed time since this job was offered.

        Admission is stamped with *this*, not with the offering tick's ``now``: a job that
        waited 48 minutes for a slot must not start with a ``last_run_at`` 48 minutes old,
        or the stale-running watchdog recovers it mid-run (its threshold is measured from
        ``last_run_at``). Anchored on the tick's own ``now`` so injected test clocks hold.
        """
        return self.enqueued_now_ms + int((time.monotonic() - self.enqueued_monotonic) * 1000)

    def resolve(self) -> None:
        for fut in self.done:
            if not fut.done():
                fut.set_result(None)
        self.done.clear()


class DispatchPool:
    """Persistent slot accounting and admission for one dispatch loop (D11).

    One per executor loop (main and operational), so each keeps the global cap it always
    had. All state is touched only from the event loop, with no ``await`` between a slot
    check and the admission it guards — the same single-loop atomicity the store relies on.
    """

    def __init__(
        self,
        *,
        global_cap: Callable[[], int],
        stopping: Callable[[], bool],
        next_due_fn: NextDueFn,
        type_cap: Callable[[str], int | None] = dispatch_concurrency_for_job_type,
    ) -> None:
        self._global_cap = global_cap
        self._stopping = stopping
        self._next_due_fn = next_due_fn
        self._type_cap = type_cap
        self._waiting: dict[str, _Waiter] = {}
        self._running: dict[str, _Waiter] = {}
        self._tasks: set[asyncio.Task] = set()
        self._long_threshold: dict[str, float | None] = {}

    # -- introspection (liveness, tests) -------------------------------------------------
    @property
    def waiting_ids(self) -> list[str]:
        return sorted(self._waiting)

    @property
    def running_ids(self) -> list[str]:
        return sorted(self._running)

    def is_long(self, job: ScheduledResearchJob) -> bool:
        threshold = self._long_threshold.get(_job_type(job))
        return threshold is not None and expected_runtime_ms(job) > threshold

    # -- the one entry point the executor calls -----------------------------------------
    async def submit(
        self,
        jobs: Iterable[ScheduledResearchJob],
        now_ms: int,
        *,
        run: RunFn,
        population: Iterable[ScheduledResearchJob],
        wait: bool,
    ) -> None:
        """Offer this tick's due jobs, admit what fits, and optionally await the offered ones.

        *jobs* is the complete due set for this loop right now, so a waiter absent from it
        is no longer due (paused, deleted, or already run) and is dropped. *population* is
        every job in the store, used only to derive each type's "long" threshold.

        ``wait=False`` is what the background loops use: the tick returns immediately and
        admitted jobs run as their own tasks. ``wait=True`` keeps the original contract for
        direct callers — return once every job offered here has finished.
        """
        population = list(population)
        types = {_job_type(j) for j in population}
        self._long_threshold = {t: long_runtime_threshold_ms(population, t) for t in types}

        loop = asyncio.get_running_loop()
        due = {str(j.id): j for j in jobs}
        for job_id in [jid for jid in self._waiting if jid not in due]:
            self._waiting.pop(job_id).resolve()

        futures: list[asyncio.Future] = []
        mono = time.monotonic()
        for job_id, job in due.items():
            tracked = self._running.get(job_id) or self._waiting.get(job_id)
            if tracked is None:
                tracked = _Waiter(job=job, run=run, enqueued_now_ms=now_ms, enqueued_monotonic=mono)
                self._waiting[job_id] = tracked
            elif job_id in self._waiting:
                # Fresh snapshot for ordering, and re-anchor its clock on this tick's `now`
                # (the freshest reading). Ageing is measured from `job.next_run_at`, not
                # from when it was first offered, so nothing is lost by re-anchoring.
                tracked.job = job
                tracked.enqueued_now_ms = now_ms
                tracked.enqueued_monotonic = mono
            if wait:
                fut = loop.create_future()
                tracked.done.append(fut)
                futures.append(fut)

        self._pump()
        if futures:
            await asyncio.gather(*futures)

    # -- admission ---------------------------------------------------------------------
    def _priority(self, waiter: _Waiter) -> tuple:
        now = waiter.now_ms()
        job = waiter.job
        if is_aged(job, now, self._next_due_fn):
            # Longest-overdue first among the aged.
            return (0, -(now - int(job.next_run_at)), str(job.id))
        return (1, expected_runtime_ms(job), int(job.next_run_at), str(job.id))

    def _pump(self) -> None:
        """Fill free slots from the waiters in priority order. Synchronous by design."""
        if self._stopping() or not self._waiting:
            return
        global_cap = max(1, int(self._global_cap()))
        for waiter in sorted(self._waiting.values(), key=self._priority):
            if len(self._running) >= global_cap:
                return
            job_type = waiter.job_type
            cap = self._type_cap(job_type)
            if cap is not None:
                active = sum(1 for w in self._running.values() if w.job_type == job_type)
                free = cap - active
                if free <= 0:
                    continue
                # (b): the last slot of a type is kept for a short job. Only meaningful with
                # at least two slots — with one, reserving it would starve every long job.
                if (
                    free == 1
                    and cap >= 2
                    and self.is_long(waiter.job)
                    and not is_aged(waiter.job, waiter.now_ms(), self._next_due_fn)
                ):
                    continue
            self._admit(waiter)

    def _admit(self, waiter: _Waiter) -> None:
        job_id = str(waiter.job.id)
        del self._waiting[job_id]
        self._running[job_id] = waiter
        task = asyncio.get_running_loop().create_task(
            self._run_admitted(waiter, waiter.now_ms()), name=f"scheduled-dispatch:{job_id}"
        )
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _run_admitted(self, waiter: _Waiter, admit_now_ms: int) -> None:
        job_id = str(waiter.job.id)
        try:
            await waiter.run(waiter.job, admit_now_ms)
        finally:
            self._running.pop(job_id, None)
            waiter.resolve()
            # Refill the slot now, not on the next tick.
            self._pump()

    # -- shutdown ----------------------------------------------------------------------
    async def shutdown(self) -> None:
        """Drop every waiter and cancel every running dispatch, then wait for them to unwind.

        Called by ``stop()`` after its bounded drain grace. A cancelled dispatch writes no
        completion (``_dispatch_one`` re-raises ``CancelledError``), so it is still RUNNING
        and ``stop()``'s recovery pass handles it exactly as before.
        """
        for waiter in list(self._waiting.values()):
            waiter.resolve()
        self._waiting.clear()
        tasks = list(self._tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def join(self) -> None:
        """Wait until nothing is running or waiting (tests; never called on a live loop)."""
        while self._tasks or (self._waiting and not self._stopping()):
            if self._tasks:
                await asyncio.gather(*list(self._tasks), return_exceptions=True)
            else:
                # Waiters but nothing running: nothing will free a slot, so do not spin.
                return
