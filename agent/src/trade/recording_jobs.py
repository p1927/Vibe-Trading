"""In-memory + file-backed job store for stock-simulator day recording.

Mirrors `index_prediction_run_jobs.py`'s pattern (job.json under
`log/recording_jobs/{job_id}/job.json`, detached subprocess worker, PID
liveness/zombie reconciliation) so recording jobs get the same SSE-friendly,
hot-reload-safe control surface without inventing a new mechanism.

Unlike index-prediction runs (keyed per ticker), only one recording session is
meaningful at a time, so active-job lookup uses a single fixed key rather than
a per-ticker map.
"""

from __future__ import annotations

import json
import logging
import os
import re
import signal
import subprocess
import sys
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable

try:
    import fcntl

    _HAS_FCNTL = True
except ImportError:  # pragma: no cover - Windows / minimal builds
    fcntl = None  # type: ignore[assignment]
    _HAS_FCNTL = False

from src.config.accessor import get_env_config

logger = logging.getLogger(__name__)

RECORDING_JOBS: dict[str, dict[str, Any]] = {}
_ACTIVE_KEY = "recording"
_ACTIVE_JOB_ID: str | None = None
_JOBS_LOCK = threading.RLock()

_JOB_TTL_SECONDS = 60 * 60
_JOB_ID_RE = re.compile(r"^[a-f0-9]{32}$")
_ACTIVE_STATUSES = frozenset({"queued", "waiting_for_open", "running"})
_WALL_CLOCK_SECONDS = int(get_env_config().trade.recording_run_wall_clock_seconds)
_QUEUED_NO_PID_SECONDS = int(get_env_config().trade.recording_queued_no_pid_seconds)


def _is_pid_alive(pid: int | None) -> bool:
    if pid is None or pid <= 0:
        return False
    try:
        os.kill(int(pid), 0)
    except OSError:
        return False
    return True


def worker_alive(job: dict[str, Any] | None) -> bool:
    if job is None:
        return False
    status = str(job.get("status") or "")
    pid = job.get("worker_pid")
    # A ``waiting_for_open`` job legitimately has no worker_pid — the
    # Phase C cron-driven wake path (``set_waiting_for_open_at`` /
    # ``schedule_recording_wake``) never spawns a worker subprocess
    # until the scheduled wake fires; the job just sits with a
    # deferred-wake schedule and no PID. Without this branch,
    # ``reconcile_zombie_job`` (invoked on every SSE tick and every
    # ``get_active_job()`` poll) treated that as "worker died" and
    # immediately failed the job — killing every wait-for-open
    # recording seconds after arming it, well before market open.
    if status in ("queued", "waiting_for_open") and pid is None:
        return True
    if pid is None:
        return status not in _ACTIVE_STATUSES
    return _is_pid_alive(int(pid))


def reconcile_zombie_job(job_id: str) -> bool:
    job = _get_job_record(job_id)
    if job is None or job.get("status") not in _ACTIVE_STATUSES:
        return False
    if worker_alive(job):
        return False
    fail_job(job_id, "worker process exited unexpectedly")
    return True


def _parse_iso_timestamp(ts: str | None) -> float | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _job_age_seconds(job: dict[str, Any]) -> float:
    created = _parse_iso_timestamp(str(job.get("created_at") or ""))
    if created is None:
        return 0.0
    return max(0.0, datetime.now(timezone.utc).timestamp() - created)


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


def reconcile_queued_job(job_id: str) -> bool:
    job = _get_job_record(job_id)
    if job is None:
        return False
    status = str(job.get("status") or "")
    if status == "waiting_for_open":
        # Worker is sleeping in the deferred-wake loop (Phase 2 of the
        # wait-for-open plan). Leave it alone — the recorder handles
        # its own liveness and exits with `stopped_reason="wait_timeout"`
        # if the wait exceeds `RECORDING_WAIT_MAX_HOURS`.
        return False
    if status != "queued":
        return False
    if job.get("worker_pid") is not None:
        return False
    if _job_age_seconds(job) <= _QUEUED_NO_PID_SECONDS:
        return False
    fail_job(job_id, f"worker never spawned after {_QUEUED_NO_PID_SECONDS}s", terminate_worker=True)
    return True


def reconcile_stale_job(job_id: str) -> bool:
    job = _get_job_record(job_id)
    if job is None or job.get("status") != "running":
        return False
    if not worker_alive(job):
        return False
    wall_age = _run_age_seconds(job)
    if wall_age > _WALL_CLOCK_SECONDS:
        fail_job(
            job_id,
            f"recording exceeded wall-clock budget ({int(wall_age)}s > {_WALL_CLOCK_SECONDS}s)",
            terminate_worker=True,
        )
        return True
    return False


def reconcile_job(job_id: str) -> bool:
    if reconcile_zombie_job(job_id):
        return True
    if reconcile_queued_job(job_id):
        return True
    if reconcile_stale_job(job_id):
        return True
    return False


def reconcile_all_active_jobs() -> int:
    """Run reconciliation over every job currently marked active.

    Reconciliation otherwise only runs when something calls ``get_active_job``
    — a crashed worker for a job nobody is polling would sit reporting
    "running" forever. Returns the number of jobs terminalized.
    """
    with _JOBS_LOCK:
        job_ids = [
            job_id
            for job_id, job in RECORDING_JOBS.items()
            if job.get("status") in _ACTIVE_STATUSES
        ]
    return sum(1 for job_id in job_ids if reconcile_job(job_id))


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _jobs_root() -> Path:
    from src.trade.hub_bridge import trade_repo_root

    root = trade_repo_root()
    if root is None:
        root = Path.cwd()
    return root / "log" / "recording_jobs"


def _job_dir(job_id: str) -> Path:
    return _jobs_root() / job_id


def _job_file(job_id: str) -> Path:
    return _job_dir(job_id) / "job.json"


def _stop_flag_file(job_id: str) -> Path:
    return _job_dir(job_id) / "stop.flag"


def stop_flag_path(job_id: str) -> Path:
    """Public accessor so the worker process can check the same path."""
    return _stop_flag_file(job_id)


def _job_lock_file(job_id: str) -> Path:
    return _job_dir(job_id) / ".job.lock"


@contextmanager
def _job_file_lock(job_id: str, *, exclusive: bool = True):
    if not job_id_valid(job_id):
        yield
        return
    lock_path = _job_lock_file(job_id)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    if not _HAS_FCNTL:
        yield
        return
    with open(lock_path, "a+", encoding="utf-8") as lockf:
        flag = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
        fcntl.flock(lockf.fileno(), flag)
        try:
            yield
        finally:
            fcntl.flock(lockf.fileno(), fcntl.LOCK_UN)


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


def _write_job_to_disk_unsafe(job: dict[str, Any]) -> None:
    job_id = str(job.get("job_id") or "")
    if not job_id_valid(job_id):
        return
    path = _job_file(job_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(_serialize_job(job), ensure_ascii=False, default=str), encoding="utf-8")
    tmp.replace(path)


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


def _read_job_from_disk_unsafe(job_id: str) -> dict[str, Any] | None:
    path = _job_file(job_id)
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(payload, dict) or not job_id_valid(str(payload.get("job_id") or job_id)):
        return None
    payload.setdefault("job_id", job_id)
    payload.setdefault("logs", [])
    return payload


def _write_job_to_disk(job: dict[str, Any]) -> None:
    job_id = str(job.get("job_id") or "")
    with _job_file_lock(job_id, exclusive=True):
        _write_job_to_disk_unsafe(job)


def _read_job_from_disk(job_id: str) -> dict[str, Any] | None:
    with _job_file_lock(job_id, exclusive=False):
        return _read_job_from_disk_unsafe(job_id)


def _mutate_job_on_disk(
    job_id: str,
    mutator: Callable[[dict[str, Any]], bool],
) -> dict[str, Any] | None:
    with _job_file_lock(job_id, exclusive=True):
        job = _read_job_from_disk_unsafe(job_id)
        if job is None:
            return None
        job = dict(job)
        job.setdefault("logs", [])
        if not mutator(job):
            return None
        _write_job_to_disk_unsafe(job)
        return job


def _merge_job_from_disk(job_id: str, memory: dict[str, Any] | None) -> dict[str, Any] | None:
    disk = _read_job_from_disk(job_id)
    if disk is None:
        return memory
    if memory is None:
        return disk
    mem_logs = len(memory.get("logs") or [])
    disk_logs = len(disk.get("logs") or [])
    mem_status = str(memory.get("status") or "")
    disk_status = str(disk.get("status") or "")
    if disk_logs > mem_logs or disk_status != mem_status:
        merged = dict(disk)
        merged.setdefault("job_id", job_id)
        return merged
    return memory


def _get_job_record(job_id: str) -> dict[str, Any] | None:
    global _ACTIVE_JOB_ID
    with _JOBS_LOCK:
        memory = RECORDING_JOBS.get(job_id)
    merged = _merge_job_from_disk(job_id, memory)
    if merged is not None:
        with _JOBS_LOCK:
            RECORDING_JOBS[job_id] = merged
            if merged.get("status") in _ACTIVE_STATUSES:
                _ACTIVE_JOB_ID = job_id
            elif _ACTIVE_JOB_ID == job_id:
                _ACTIVE_JOB_ID = None
    return merged


def hydrate_jobs_from_disk() -> None:
    global _ACTIVE_JOB_ID
    root = _jobs_root()
    if not root.is_dir():
        return
    for path in root.iterdir():
        if not path.is_dir():
            continue
        job_id = path.name
        if not job_id_valid(job_id):
            continue
        job = _read_job_from_disk(job_id)
        if job is None:
            continue
        with _JOBS_LOCK:
            RECORDING_JOBS[job_id] = job
            if job.get("status") in _ACTIVE_STATUSES:
                _ACTIVE_JOB_ID = job_id


def _prune_old_jobs() -> None:
    cutoff = time.time() - _JOB_TTL_SECONDS
    with _JOBS_LOCK:
        stale = [
            job_id
            for job_id, job in RECORDING_JOBS.items()
            if job.get("status") in ("done", "error") and job.get("_finished_at", 0) < cutoff
        ]
        for job_id in stale:
            RECORDING_JOBS.pop(job_id, None)


def job_id_valid(job_id: str | None) -> bool:
    return bool(job_id and _JOB_ID_RE.fullmatch(job_id))


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
    with _JOBS_LOCK:
        job_id = _ACTIVE_JOB_ID
    if not job_id:
        return None
    job = _get_job_record(job_id)
    if job is None or job.get("status") not in _ACTIVE_STATUSES:
        return None
    if not worker_alive(job):
        reconcile_job(job_id)
        job = _get_job_record(job_id)
        if job is not None and job.get("status") == "error":
            return _job_snapshot(job)
        return None
    reconcile_job(job_id)
    job = _get_job_record(job_id)
    if job is None or job.get("status") not in _ACTIVE_STATUSES:
        if job is not None and job.get("status") == "error":
            return _job_snapshot(job)
        return None
    return _job_snapshot(job)


def _active_job_lock_file() -> Path:
    return _jobs_root() / "_active.lock"


@contextmanager
def _active_job_creation_lock():
    """Cross-process exclusive lock guarding recording-job creation.

    ``_ACTIVE_JOB_ID`` is only an in-process cache; without this lock, two
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


def _find_live_active_job_on_disk() -> dict[str, Any] | None:
    """Ground truth scan across every job dir for one whose worker is
    actually alive — what an in-memory-only check can't see when called
    from a process that just started. Along the way, reconciles (fails)
    any job still marked active on disk whose worker has actually died,
    so a stale row left by a crashed process can't wedge the lock
    forever. Must be called while holding ``_active_job_creation_lock``.
    """
    root = _jobs_root()
    if not root.is_dir():
        return None
    live: dict[str, Any] | None = None
    for path in sorted(root.iterdir()):
        if not path.is_dir() or not job_id_valid(path.name):
            continue
        job = _read_job_from_disk(path.name)
        if job is None or job.get("status") not in _ACTIVE_STATUSES:
            continue
        if worker_alive(job):
            if live is None:
                live = job
        else:
            fail_job(str(job.get("job_id")), "worker process exited unexpectedly")
    return live


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
    (or empty) keeps the legacy behaviour — all three categories share
    ``poll_interval_s``. The worker applies this directly; this function
    only persists it so the snapshot reflects what the user configured.

    ``ws_throttle_hz``: max recorded WS LTP ticks per second. ``None``
    or ``<= 0`` means unlimited. The worker passes the value through to
    the recorder's ``MaxRateLimiter``.

    ``equities``: NIFTY50 trading symbols to record per-equity polls /
    historical candles for. Empty list → no per-equity work.

    ``equity_intervals``: same shape as ``category_intervals`` but for
    the three equity REST polls.

    ``historical_config``: ``None`` (skip) or a dict with Indmoney
    interval + lookback days. Pulled once per equity at session start.
    """
    global _ACTIVE_JOB_ID
    _prune_old_jobs()

    # Cross-process critical section: everything from "is anything else
    # active" through "persist the new job" must be atomic w.r.t. any
    # other process calling start_job concurrently, or two processes can
    # each see "nothing active" and both create one (see docstring on
    # ``_active_job_creation_lock``).
    with _active_job_creation_lock():
        with _JOBS_LOCK:
            existing_id = _ACTIVE_JOB_ID
            existing_mem = RECORDING_JOBS.get(existing_id) if existing_id else None

        if existing_id:
            existing = existing_mem or _read_job_from_disk(existing_id)
            if existing and existing.get("status") in _ACTIVE_STATUSES:
                if existing.get("worker_pid") is not None and not worker_alive(existing):
                    fail_job(existing_id, "worker process exited unexpectedly")
                else:
                    with _JOBS_LOCK:
                        if existing_id not in RECORDING_JOBS:
                            RECORDING_JOBS[existing_id] = existing
                    return existing_id, True

        # This process's memory had nothing active — but another process
        # may have created a still-live job we've simply never heard of.
        # Ground-truth that against disk before deciding it's safe to
        # create a new one.
        disk_active = _find_live_active_job_on_disk()
        if disk_active is not None:
            disk_job_id = str(disk_active["job_id"])
            with _JOBS_LOCK:
                RECORDING_JOBS[disk_job_id] = disk_active
                _ACTIVE_JOB_ID = disk_job_id
            return disk_job_id, True

        job_id = uuid.uuid4().hex
        job = {
            "job_id": job_id,
            "status": "queued",
            "underlyings": list(underlyings),
            "equities": list(equities or []),
            "poll_interval_s": int(poll_interval_s),
            # Persist as None for legacy callers so the snapshot truthfully
            # reports "no per-category config" rather than a synthesised map.
            "category_intervals": (
                {k: int(v) for k, v in category_intervals.items()}
                if category_intervals
                else None
            ),
            "equity_intervals": (
                {k: int(v) for k, v in equity_intervals.items()}
                if equity_intervals
                else None
            ),
            "ws_throttle_hz": (
                float(ws_throttle_hz) if ws_throttle_hz and ws_throttle_hz > 0 else None
            ),
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
        with _JOBS_LOCK:
            RECORDING_JOBS[job_id] = job
            _ACTIVE_JOB_ID = job_id
        _write_job_to_disk(job)
        return job_id, False


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
        except Exception:
            logger.exception("kick_recording: wait_for_open scheduling failed for %s", job_id)
            market_closed = False

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


def mark_running(job_id: str) -> None:
    def mutator(job: dict[str, Any]) -> bool:
        # Allow transition from ``waiting_for_open`` as well as
        # ``queued`` — the recorder enters ``waiting_for_open`` during
        # the deferred-wake wait loop and bumps back to ``running``
        # once the wait releases.
        status = str(job.get("status") or "")
        if status not in _ACTIVE_STATUSES:
            return False
        if status in {"queued", "waiting_for_open"}:
            job["status"] = "running"
            job["run_started_at"] = _now_iso()
        return True

    job = _mutate_job_on_disk(job_id, mutator)
    if job is None:
        return
    with _JOBS_LOCK:
        RECORDING_JOBS[job_id] = job


def mark_waiting_for_open(job_id: str) -> bool:
    """Transition ``queued`` → ``waiting_for_open``. No-op from any other
    status (including ``waiting_for_open`` itself — the recorder calls
    this on its first sleep slice and the call must be idempotent if
    the disk round-trip races the in-memory cache).

    Returns ``True`` if the job transitioned on disk, ``False`` if the
    job was missing or already past ``queued``.
    """
    def mutator(job: dict[str, Any]) -> bool:
        if str(job.get("status") or "") != "queued":
            return False
        job["status"] = "waiting_for_open"
        return True

    job = _mutate_job_on_disk(job_id, mutator)
    if job is None:
        return False
    with _JOBS_LOCK:
        RECORDING_JOBS[job_id] = job
    return True


def set_waiting_for_open_at(
    job_id: str, *, next_open_at: datetime
) -> bool:
    """Persist ``next_open_at`` on a ``queued`` job and flip status to
    ``waiting_for_open``. Used by the cron-driven respawn path (Phase C)
    when the API entry receives ``wait_for_open=True`` and the market is
    closed — the API persists the wake deadline here and registers a
    ``ScheduledResearchJob`` separately; no worker subprocess is
    spawned.

    No-op from any other status (idempotent). Returns ``True`` on
    transition, ``False`` otherwise.
    """
    def mutator(job: dict[str, Any]) -> bool:
        if str(job.get("status") or "") != "queued":
            return False
        job["status"] = "waiting_for_open"
        job["next_open_at"] = (
            next_open_at.isoformat()
            if isinstance(next_open_at, datetime)
            else str(next_open_at)
        )
        return True

    job = _mutate_job_on_disk(job_id, mutator)
    if job is None:
        return False
    with _JOBS_LOCK:
        RECORDING_JOBS[job_id] = job
    return True


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
    def mutator(job: dict[str, Any]) -> bool:
        if str(job.get("status") or "") not in _ACTIVE_STATUSES:
            return False
        if job.get("status") == "queued":
            job["status"] = "running"
            job["run_started_at"] = _now_iso()
        job.setdefault("logs", []).append(dict(entry))
        return True

    job = _mutate_job_on_disk(job_id, mutator)
    if job is None:
        return
    with _JOBS_LOCK:
        RECORDING_JOBS[job_id] = job


def complete_job(job_id: str, *, result: dict[str, Any]) -> None:
    def mutator(job: dict[str, Any]) -> bool:
        if str(job.get("status") or "") not in _ACTIVE_STATUSES:
            return False
        job["status"] = "done"
        job["result"] = result
        job["session_date"] = result.get("session_date")
        job["_finished_at"] = time.time()
        return True

    job = _mutate_job_on_disk(job_id, mutator)
    if job is None:
        return
    with _JOBS_LOCK:
        RECORDING_JOBS[job_id] = job
        global _ACTIVE_JOB_ID
        if _ACTIVE_JOB_ID == job_id:
            _ACTIVE_JOB_ID = None


def fail_job(job_id: str, message: str, *, terminate_worker: bool = False) -> None:
    job_for_term = _get_job_record(job_id)
    if job_for_term is None:
        return
    if terminate_worker:
        _terminate_worker(job_for_term)

    def mutator(job: dict[str, Any]) -> bool:
        if str(job.get("status") or "") not in _ACTIVE_STATUSES:
            return False
        job["status"] = "error"
        job["error"] = message
        job["_finished_at"] = time.time()
        return True

    job = _mutate_job_on_disk(job_id, mutator)
    if job is None:
        return
    with _JOBS_LOCK:
        RECORDING_JOBS[job_id] = job
        global _ACTIVE_JOB_ID
        if _ACTIVE_JOB_ID == job_id:
            _ACTIVE_JOB_ID = None


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


def _terminate_worker(job: dict[str, Any] | None) -> None:
    if job is None:
        return
    pid = job.get("worker_pid")
    if pid is None:
        return
    try:
        pid_int = int(pid)
    except (TypeError, ValueError):
        return
    if not _is_pid_alive(pid_int) or pid_int == os.getpid():
        return
    try:
        os.kill(pid_int, signal.SIGTERM)
    except OSError:
        pass


def _agent_dir() -> Path:
    here = Path(__file__).resolve()
    return here.parents[1]


def spawn_worker(job_id: str) -> None:
    """Launch the recorder in a detached subprocess (survives API hot-reload)."""
    agent_dir = _agent_dir()
    worker_log = _job_dir(job_id) / "worker.log"
    worker_log.parent.mkdir(parents=True, exist_ok=True)
    log_handle = worker_log.open("ab")

    env = os.environ.copy()
    # NSE_REPLAY_DATA_ROOT is configured as a path relative to *this*
    # process's cwd (typically the repo root). The worker below runs with
    # cwd=agent_dir instead (needed for `-m src.trade.recording_worker` to
    # resolve), so the same relative string would land two directories off
    # — silently writing every recorded session into a brand-new, orphaned
    # `vibetrading/agent/data/...` tree that no reader (replay calendar,
    # chart, coverage panel) ever looks at. Resolve it against our own cwd
    # before handing it to the child so both processes agree on one path.
    data_root_raw = env.get("NSE_REPLAY_DATA_ROOT")
    if data_root_raw and not Path(data_root_raw).is_absolute():
        env["NSE_REPLAY_DATA_ROOT"] = str(Path(data_root_raw).resolve())
    proc = subprocess.Popen(
        [sys.executable, "-m", "src.trade.recording_worker", job_id],
        cwd=str(agent_dir),
        env=env,
        start_new_session=True,
        stdout=log_handle,
        stderr=subprocess.STDOUT,
    )
    log_handle.close()

    def mutator(job: dict[str, Any]) -> bool:
        job["worker_pid"] = proc.pid
        return True

    job = _mutate_job_on_disk(job_id, mutator)
    if job is not None:
        with _JOBS_LOCK:
            RECORDING_JOBS[job_id] = job
