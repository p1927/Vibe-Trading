"""Recording-session job store (stock-simulator day recording).

``job.json`` under ``log/recording_jobs/{job_id}/``, a detached subprocess worker, PID liveness and zombie
reconciliation, SSE-friendly and hot-reload-safe. The lifecycle (disk-first mutation, reconcile, prune,
claim) is the shared :class:`src.trade.job_store.JobStore`, the same one the index-prediction,
external-predictions and forecast-engine stores use (D306). This module keeps what is recording-specific:
the ``waiting_for_open`` status (a cron wake spawns the worker later, so it has no pid), the run age
measured from ``run_started_at``, the cross-process creation lock and disk scan (one live session in
total), the supplement claim, the stop flag, the wake path and the snapshot shape.

Only one recording session is meaningful at a time, so the active index uses one fixed scope.
"""

from __future__ import annotations

import json
import logging
import uuid
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

try:
    import fcntl

    _HAS_FCNTL = True
except ImportError:  # pragma: no cover - Windows / minimal builds
    fcntl = None  # type: ignore[assignment]
    _HAS_FCNTL = False

from src.config.accessor import get_env_config
from src.trade import detached_worker, job_runner
from src.trade.job_store import (
    JobStore,
    _parse_iso_timestamp,
    job_age_seconds as _job_age_seconds,
    now_iso as _now_iso,
)

logger = logging.getLogger(__name__)

RECORDING_JOBS: dict[str, dict[str, Any]] = {}
_ACTIVE_KEY = "recording"
_ACTIVE_BY_SCOPE: dict[str, str] = {}

_ACTIVE_STATUSES = frozenset({"queued", "waiting_for_open", "running"})
_WALL_CLOCK_SECONDS = int(get_env_config().trade.recording_run_wall_clock_seconds)
_QUEUED_NO_PID_SECONDS = int(get_env_config().trade.recording_queued_no_pid_seconds)


def _is_pid_alive(pid: int | None) -> bool:
    return detached_worker.is_alive(pid)


def _jobs_root() -> Path:
    from src.trade.hub_bridge import trade_repo_root

    root = trade_repo_root()
    if root is None:
        root = Path.cwd()
    return root / "log" / "recording_jobs"


def _job_dir(job_id: str) -> Path:
    return _jobs_root() / job_id


def _stop_flag_file(job_id: str) -> Path:
    return _job_dir(job_id) / "stop.flag"


def stop_flag_path(job_id: str) -> Path:
    """Public accessor so the worker process can check the same path."""
    return _stop_flag_file(job_id)


def _serialize_job(job: dict[str, Any]) -> dict[str, Any]:
    return {
        "job_id": job["job_id"],
        "status": job.get("status"),
        "underlyings": job.get("underlyings"),
        "equities": job.get("equities"),
        "poll_interval_s": job.get("poll_interval_s"),
        "category_intervals": job.get("category_intervals"),
        "equity_intervals": job.get("equity_intervals"),
        "ws_throttle_hz": job.get("ws_throttle_hz"),
        "historical_config": job.get("historical_config"),
        "wait_for_open": bool(job.get("wait_for_open")),
        "next_open_at": job.get("next_open_at"),
        "created_at": job.get("created_at"),
        "run_started_at": job.get("run_started_at"),
        "logs": list(job.get("logs") or []),
        "session_date": job.get("session_date"),
        "result": job.get("result"),
        "error": job.get("error"),
        "worker_pid": job.get("worker_pid"),
        "_finished_at": job.get("_finished_at"),
    }


def _run_age_seconds(job: dict[str, Any]) -> float:
    """Elapsed time since the job actually started *running*.

    Unlike :func:`_job_age_seconds` (measured from ``created_at``, stamped
    once at job creation), this measures from ``run_started_at`` — stamped
    by :func:`mark_running` when the job transitions into ``running``.
    For a ``wait_for_open`` job, ``created_at`` can be hours to days before
    the deferred wait actually releases, so the wall-clock-budget check
    (which is about "how long has this recording actually been running")
    must use this instead. Falls back to ``created_at`` for jobs written
    before ``run_started_at`` existed, or if it was somehow never stamped.
    """
    started = _parse_iso_timestamp(str(job.get("run_started_at") or ""))
    if started is not None:
        return max(0.0, datetime.now(timezone.utc).timestamp() - started)
    return _job_age_seconds(job)


def _active_job_lock_file() -> Path:
    return _jobs_root() / "_active.lock"


@contextmanager
def _active_job_creation_lock():
    """Cross-process exclusive lock guarding recording-job creation.

    ``_ACTIVE_BY_SCOPE`` is only an in-process cache; without this lock, two
    separate process invocations of ``start_job`` (two ad-hoc scripts, or
    a persistent server plus a one-off script) can each observe "no
    active job in my memory" and both create one, since neither can see
    the other's in-memory state. Reproduced live 2026-08-25: 5
    concurrently-running recording jobs for the identical underlyings,
    each started from a process that had no idea about the others.
    """
    lock_path = _active_job_lock_file()
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    if not _HAS_FCNTL:
        yield
        return
    with open(lock_path, "a+", encoding="utf-8") as lockf:
        fcntl.flock(lockf.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lockf.fileno(), fcntl.LOCK_UN)


# Module-level lookups (not captured values) so tests can monkeypatch ``_jobs_root`` /
# ``_WALL_CLOCK_SECONDS`` / ``_QUEUED_NO_PID_SECONDS`` after import.
_STORE = JobStore(
    jobs_ref=lambda: RECORDING_JOBS,
    active_ref=lambda: _ACTIVE_BY_SCOPE,
    jobs_root=lambda: _jobs_root(),
    serialize=_serialize_job,
    scope_for=lambda job: _ACTIVE_KEY,
    wall_clock_seconds=lambda job: _WALL_CLOCK_SECONDS,
    queued_no_pid_seconds=lambda: _QUEUED_NO_PID_SECONDS,
    result_key="result",
    active_statuses=_ACTIVE_STATUSES,
    # ``waiting_for_open`` legitimately has no worker_pid: the cron-driven wake path spawns the worker
    # only when the scheduled wake fires. Without it every wait-for-open recording was failed as a dead
    # worker seconds after arming (reconcile runs on every SSE tick and every get_active_job).
    startable_statuses=frozenset({"queued", "waiting_for_open"}),
    pidless_alive_statuses=frozenset({"queued", "waiting_for_open"}),
    run_age_seconds=lambda job: _run_age_seconds(job),
    on_running=lambda job: job.__setitem__("run_started_at", _now_iso()),
    creation_lock=lambda: _active_job_creation_lock(),
)

_JOBS_LOCK = _STORE.jobs_lock
_mutate_job_on_disk = _STORE.mutate
_write_job_to_disk = _STORE.write
_job_file = _STORE.job_file
_get_job_record = _STORE.get_record
hydrate_jobs_from_disk = _STORE.hydrate
worker_alive = _STORE.alive
reconcile_zombie_job = _STORE.reconcile_zombie
reconcile_queued_job = _STORE.reconcile_queued
reconcile_stale_job = _STORE.reconcile_stale
reconcile_job = _STORE.reconcile
reconcile_all_active_jobs = _STORE.reconcile_all_active
mark_running = _STORE.mark_running
fail_job = _STORE.fail
job_id_valid = job_runner.job_id_valid


def _supplement_marker_path(session_date: str) -> Path:
    return _jobs_root() / "_supplement" / f"{session_date}.json"


def claim_supplement_run(session_date: str, job_id: str) -> bool:
    """Atomically claim the right to run the end-of-session supplement
    (``StockHistory().supplement_today``) for ``session_date``.

    Returns ``True`` if this caller won the claim (no job has already run
    or is already running today's supplement), ``False`` otherwise. Guards
    against the fan-out where several recording jobs for the same calendar
    date — e.g. repeated fast-failing Auto Record restarts — each
    independently spawn the same expensive macro/flow gap-fill scrape.
    One marker per calendar date, so it resets naturally the next day.
    """
    marker = _supplement_marker_path(session_date)
    marker.parent.mkdir(parents=True, exist_ok=True)
    if not _HAS_FCNTL:
        if marker.is_file():
            return False
        marker.write_text(
            json.dumps({"job_id": job_id, "claimed_at": _now_iso()}), encoding="utf-8"
        )
        return True
    lock_path = marker.with_suffix(".lock")
    with open(lock_path, "a+", encoding="utf-8") as lockf:
        fcntl.flock(lockf.fileno(), fcntl.LOCK_EX)
        try:
            if marker.is_file():
                return False
            marker.write_text(
                json.dumps({"job_id": job_id, "claimed_at": _now_iso()}), encoding="utf-8"
            )
            return True
        finally:
            fcntl.flock(lockf.fileno(), fcntl.LOCK_UN)


def _job_snapshot(job: dict[str, Any], *, include_logs: bool = True) -> dict[str, Any]:
    logs = list(job.get("logs") or [])
    out: dict[str, Any] = {
        "job_id": job["job_id"],
        "status": job["status"],
        "underlyings": job.get("underlyings"),
        "equities": job.get("equities"),
        "poll_interval_s": job.get("poll_interval_s"),
        "category_intervals": job.get("category_intervals"),
        "equity_intervals": job.get("equity_intervals"),
        "ws_throttle_hz": job.get("ws_throttle_hz"),
        "historical_config": job.get("historical_config"),
        "wait_for_open": bool(job.get("wait_for_open")),
        # Phase C: surface the scheduled wake deadline (ISO string) so the
        # UI can render "Next open at HH:MM IST" even for jobs whose log
        # stream is still empty (the cron-driven respawn path doesn't emit
        # a "waiting" log entry until after the wake fires).
        "next_open_at": job.get("next_open_at"),
        "created_at": job.get("created_at"),
        "run_started_at": job.get("run_started_at"),
        "session_date": job.get("session_date"),
        "error": job.get("error"),
    }
    if logs:
        last = logs[-1]
        out["last_log_at"] = last.get("at")
        out["last_log_message"] = last.get("message")
    out["session_pct_complete"] = _session_pct_complete(job)
    if include_logs:
        out["logs"] = logs
    if job.get("status") == "done" and job.get("result") is not None:
        out["result"] = job["result"]
    return out


def _session_pct_complete(job: dict[str, Any]) -> float | None:
    """Elapsed fraction of the 09:15-15:30 IST trading session (for the UI progress bar)."""
    from datetime import time as dtime
    from zoneinfo import ZoneInfo

    created_at = _parse_iso_timestamp(str(job.get("created_at") or ""))
    if created_at is None:
        return None
    ist = ZoneInfo("Asia/Kolkata")
    now_ist = datetime.now(ist)
    open_dt = datetime.combine(now_ist.date(), dtime(9, 15), tzinfo=ist)
    close_dt = datetime.combine(now_ist.date(), dtime(15, 30), tzinfo=ist)
    total = (close_dt - open_dt).total_seconds()
    if total <= 0:
        return None
    elapsed = (now_ist - open_dt).total_seconds()
    if job.get("status") in ("done", "error"):
        finished = job.get("_finished_at")
        if finished:
            elapsed = min(elapsed, finished - open_dt.timestamp())
    return round(max(0.0, min(1.0, elapsed / total)), 4)


def get_job(job_id: str) -> dict[str, Any] | None:
    job = _get_job_record(job_id)
    if job is None:
        return None
    return _job_snapshot(job)


def get_active_job() -> dict[str, Any] | None:
    """The live recording job's snapshot, reconciled first (a dead worker, a never-spawned queued job or
    an over-budget run is failed here). A job this very call failed is returned once, as ``error``."""
    with _JOBS_LOCK:
        job_id = _ACTIVE_BY_SCOPE.get(_ACTIVE_KEY)
    if not job_id:
        return None
    reconcile_job(job_id)
    job = _get_job_record(job_id)
    if job is None or job.get("status") not in _ACTIVE_STATUSES:
        return _job_snapshot(job) if job is not None and job.get("status") == "error" else None
    return _job_snapshot(job)


def start_job(
    *,
    underlyings: list[str],
    poll_interval_s: int = 10,
    category_intervals: dict[str, int] | None = None,
    ws_throttle_hz: float | None = None,
    wait_for_open: bool = False,
    equities: list[str] | None = None,
    equity_intervals: dict[str, int] | None = None,
    historical_config: dict[str, Any] | None = None,
) -> tuple[str, bool]:
    """Create or reuse the active recording job. Returns (job_id, reused).

    ``category_intervals``: per-REST-category cadence in seconds. ``None``
    (or empty) keeps the legacy behaviour: all three categories share
    ``poll_interval_s``. The worker applies this directly; this function
    only persists it so the snapshot reflects what the user configured.

    ``ws_throttle_hz``: max recorded WS LTP ticks per second. ``None``
    or ``<= 0`` means unlimited. The worker passes the value through to
    the recorder's ``MaxRateLimiter``.

    ``equities``: NIFTY50 trading symbols to record per-equity polls /
    historical candles for. Empty list means no per-equity work.

    ``equity_intervals``: same shape as ``category_intervals`` but for
    the three equity REST polls.

    ``historical_config``: ``None`` (skip) or a dict with Indmoney
    interval + lookback days. Pulled once per equity at session start.

    The check-then-create is atomic across processes (``JobStore.claim`` under the creation lock, which
    also finds a live job another process created): two ad-hoc scripts, or a server plus a script, cannot
    each create one (reproduced live 2026-08-25: five concurrent jobs for the same underlyings).
    """

    def _make() -> dict[str, Any]:
        return {
            "job_id": uuid.uuid4().hex,
            "status": "queued",
            "underlyings": list(underlyings),
            "equities": list(equities or []),
            "poll_interval_s": int(poll_interval_s),
            # Persist as None for legacy callers so the snapshot truthfully
            # reports "no per-category config" rather than a synthesised map.
            "category_intervals": (
                {k: int(v) for k, v in category_intervals.items()} if category_intervals else None
            ),
            "equity_intervals": (
                {k: int(v) for k, v in equity_intervals.items()} if equity_intervals else None
            ),
            "ws_throttle_hz": float(ws_throttle_hz) if ws_throttle_hz and ws_throttle_hz > 0 else None,
            "historical_config": (
                {"interval": str(historical_config["interval"]),
                 "lookback_days": int(historical_config["lookback_days"])}
                if historical_config
                and "interval" in historical_config
                and "lookback_days" in historical_config
                else None
            ),
            "wait_for_open": bool(wait_for_open),
            "created_at": _now_iso(),
            "run_started_at": None,
            "logs": [],
            "session_date": None,
            "result": None,
            "error": None,
            "worker_pid": None,
            "_finished_at": None,
        }

    job, reused = _STORE.claim(_ACTIVE_KEY, _make)
    return str(job["job_id"]), reused


def kick_recording(
    *,
    underlyings: list[str],
    equities: list[str] | None = None,
    poll_interval_s: int = 10,
    category_intervals: dict[str, int] | None = None,
    equity_intervals: dict[str, int] | None = None,
    ws_throttle_hz: float | None = None,
    historical_config: dict[str, Any] | None = None,
    wait_for_open: bool = False,
) -> tuple[str, str, bool]:
    """Create/reuse a recording job and either spawn a worker now or, when
    ``wait_for_open=True`` and the market is closed, register a deferred
    wake instead (Phase C — see :mod:`src.trade.recording_wait_scheduler`).

    Shared core behind both the ``POST /trade/recording/start`` HTTP
    handler (``_kick_recording`` in ``src.api.trade_routes``, which layers
    request validation + underlying/equity normalisation on top) and the
    auto-record poller, which calls this directly with an already-normalised
    config template — no HTTP layer involved.

    Returns ``(job_id, job_status, reused)``.
    """
    job_id, reused = start_job(
        underlyings=underlyings,
        equities=equities or [],
        poll_interval_s=poll_interval_s,
        category_intervals=category_intervals,
        equity_intervals=equity_intervals,
        ws_throttle_hz=ws_throttle_hz,
        historical_config=historical_config,
        wait_for_open=wait_for_open,
    )

    # Phase C: when ``wait_for_open=True`` and the market is closed,
    # do NOT spawn a worker — register a deferred wake instead.
    # The recorder never enters an in-process wait loop; the
    # recording-wake poller wakes it at ``next_open_at`` via
    # :func:`wake_recording_job`.
    market_closed = False
    if wait_for_open:
        try:
            from datetime import datetime as _datetime
            from zoneinfo import ZoneInfo as _ZoneInfo

            from nautilus_openalgo_bridge.market_hours import (
                is_real_nse_market_open,
                next_real_nse_open_at,
            )
            from trade_integrations.execution.openalgo_client import OpenAlgoClient

            # Holiday-aware: `is_real_nse_market_open()` alone is weekday+time-window
            # only, so a manual/auto trigger during market hours on a trading holiday
            # would otherwise proceed unguarded. Fetch holidays first (best-effort —
            # vendor call, fall back to weekday-only on failure) so both the
            # market_closed decision and next_open_at use the same holiday set.
            try:
                _holidays_raw = OpenAlgoClient().get_market_holidays()
                holidays = frozenset({
                    date.fromisoformat(str(row["date"])[:10])
                    for row in _holidays_raw
                    if isinstance(row, dict)
                    and str(row.get("holiday_type") or "").upper() == "TRADING_HOLIDAY"
                    and row.get("date")
                })
            except Exception:
                holidays = frozenset()
            today_ist = _datetime.now(_ZoneInfo("Asia/Kolkata")).date()
            market_closed = (not is_real_nse_market_open()) or today_ist in holidays
            if market_closed:
                next_open_at = next_real_nse_open_at(holidays=holidays)
                from src.trade.recording_wait_scheduler import schedule_recording_wake

                set_waiting_for_open_at(job_id, next_open_at=next_open_at)
                schedule_recording_wake(recording_job_id=job_id, next_open_at=next_open_at)
        except Exception as exc:
            # Internal failure (not the vendor holiday fetch, which has its own fallback above): spawning
            # a worker now would record a closed market and read as a started recording. Fail the job we
            # just created and surface the error; a reused live job is left alone.
            logger.exception("kick_recording: wait_for_open scheduling failed for %s", job_id)
            if not reused:
                fail_job(job_id, f"wait_for_open scheduling failed: {exc}")
            raise

    if market_closed:
        # Don't spawn — the wake scheduler owns the lifecycle now.
        pass
    elif not reused:
        spawn_worker(job_id)
    else:
        existing = _get_job_record(job_id)
        if existing is not None and not worker_alive(existing):
            spawn_worker(job_id)

    snap = get_job(job_id) or {}
    return job_id, str(snap.get("status") or "queued"), reused


def set_waiting_for_open_at(job_id: str, *, next_open_at: datetime) -> bool:
    """Persist ``next_open_at`` on a ``queued`` job and flip status to
    ``waiting_for_open``. Used by the cron-driven respawn path (Phase C)
    when the API entry receives ``wait_for_open=True`` and the market is
    closed: the API persists the wake deadline here and registers a
    ``ScheduledResearchJob`` separately; no worker subprocess is spawned.

    No-op from any other status (idempotent). Returns ``True`` on
    transition, ``False`` otherwise.
    """

    def mutator(job: dict[str, Any]) -> bool:
        if str(job.get("status") or "") != "queued":
            return False
        job["status"] = "waiting_for_open"
        job["next_open_at"] = (
            next_open_at.isoformat() if isinstance(next_open_at, datetime) else str(next_open_at)
        )
        return True

    return _mutate_job_on_disk(job_id, mutator) is not None


def wake_recording_job(job_id: str) -> bool:
    """Called by the scheduled-research executor when a recording's
    deferred wake deadline fires. Flips status to ``running`` and
    spawns a fresh worker.

    Idempotent: returns ``False`` if the job is no longer in a wakeable
    state (already running / done / errored) or if a worker is somehow
    already alive (concurrent dispatch race).

    Returns ``True`` on successful worker spawn.
    """
    job = _get_job_record(job_id)
    if job is None:
        return False
    status = str(job.get("status") or "")
    if status != "waiting_for_open":
        # Already running, done, or errored — skip (idempotent).
        return False
    if _is_pid_alive(job.get("worker_pid")):
        # Defensive: if a worker is somehow already alive (concurrent
        # dispatch race), skip the spawn.
        return False
    # Flip status BEFORE spawning so the UI reflects the transition
    # before the worker subprocess starts.
    mark_running(job_id)
    spawn_worker(job_id)
    return True


def append_log(job_id: str, entry: dict[str, Any]) -> None:
    _STORE.append(job_id, dict(entry))


def complete_job(job_id: str, *, result: dict[str, Any]) -> None:
    _STORE.complete(job_id, result=result, session_date=result.get("session_date"))


def request_stop(job_id: str) -> None:
    """Write the cooperative stop flag the worker polls each cycle."""
    path = _stop_flag_file(job_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_now_iso(), encoding="utf-8")
    append_log(
        job_id,
        {"stage": "control", "message": "stop requested", "level": "info", "at": _now_iso()},
    )


def stop_active_job_cooperatively(*, reason: str = "stopped") -> str | None:
    """Stop whatever recording job is currently active, however it's active.

    Shared by the manual ``POST /trade/recording/{job_id}/stop`` route and
    the API process's own shutdown hook, so both paths handle the same two
    cases identically:

    - ``waiting_for_open``: no worker subprocess exists yet to poll the
      cooperative stop flag (Phase C — the recorder never spawns during
      the wait), so cancel the scheduled wake and end the job directly.
    - ``queued``/``running``: write the cooperative stop flag. The worker
      is a detached subprocess (survives API restarts by design — see
      ``spawn_worker``'s docstring), so it keeps polling that flag and
      exits on its own even after this API process has already gone away;
      the caller does not need to wait for it here.

    Returns the job id that was stopped, or ``None`` if nothing was active.
    """
    job = get_active_job()
    if job is None:
        return None
    job_id = str(job["job_id"])
    status = str(job.get("status") or "")
    if status == "waiting_for_open":
        try:
            from src.trade.recording_wait_scheduler import cancel_recording_wake

            cancel_recording_wake(recording_job_id=job_id)
        except Exception:
            logger.exception("failed to cancel recording wake for %s on stop", job_id)
        fail_job(job_id, reason)
        return job_id
    request_stop(job_id)
    return job_id


def _agent_dir() -> Path:
    # Off-by-one regression fixed here: for .../vibetrading/agent/src/trade/recording_jobs.py,
    # parents[0] is .../src/trade, parents[1] is .../src (has no "src/" subfolder of its own),
    # parents[2] is .../agent (the actual vibetrading/agent root, which does). The old
    # parents[1] value made `-m src.trade.recording_worker` unable to resolve `src` via this
    # cwd at all, so it silently fell through to whatever *other* `vibetrading/agent` happened
    # to be on PYTHONPATH (e.g. a stale checkout from a different worktree) -- see
    # .claude/backlog/items/2026-09-11-recording-job-workers-die-before-run.md.
    return job_runner.agent_dir_from_here(__file__)


def _mutate_recording_env(env: dict[str, str]) -> None:
    # NSE_REPLAY_DATA_ROOT is configured as a path relative to *this*
    # process's cwd (typically the repo root). The worker runs with
    # cwd=agent_dir instead (needed for `-m src.trade.recording_worker` to
    # resolve), so the same relative string would land two directories off
    # — silently writing every recorded session into a brand-new, orphaned
    # `vibetrading/agent/data/...` tree that no reader (replay calendar,
    # chart, coverage panel) ever looks at. Resolve it against our own cwd
    # before handing it to the child so both processes agree on one path.
    data_root_raw = env.get("NSE_REPLAY_DATA_ROOT")
    if data_root_raw and not Path(data_root_raw).is_absolute():
        env["NSE_REPLAY_DATA_ROOT"] = str(Path(data_root_raw).resolve())


def spawn_worker(job_id: str) -> None:
    """Launch the recorder in a detached subprocess (survives API hot-reload)."""
    _STORE.spawn(
        job_id,
        worker_module="src.trade.recording_worker",
        agent_dir=_agent_dir(),
        env_mutator=_mutate_recording_env,
    )
