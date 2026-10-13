"""The job store must neither lose a write nor quarantine a healthy file (review vibe-scheduler).

Two verified defects, one runnable check each:

* ``load()`` quarantined (renamed away) a perfectly good store on any ``OSError`` from the read,
  e.g. ``EMFILE`` under the load-60 conditions the release host runs at, and the next registration
  then recreated a store holding only the default jobs.
* ``upsert``/``update_run_state``/``delete`` are read-modify-write with no lock, and the routes call
  them from ``asyncio.to_thread`` workers while the executor writes on the loop thread, so a write
  from one thread was silently overwritten by another's.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from src.scheduled_research.models import JobStatus, ScheduledResearchJob
from src.scheduled_research.pause_control import cancel_running_job, set_job_enabled, trigger_job_now
from src.scheduled_research.store import CorruptStoreError, ScheduledResearchJobStore


def _job(job_id: str, **kw) -> ScheduledResearchJob:
    now = int(time.time() * 1000)
    return ScheduledResearchJob(
        id=job_id, prompt="p", schedule="60000", next_run_at=now + 60_000, created_at=now, **kw
    )


def test_a_read_oserror_never_quarantines_a_healthy_store(tmp_path: Path, monkeypatch) -> None:
    store = ScheduledResearchJobStore(tmp_path / "jobs.json")
    store.upsert(_job("a"))

    real_read_text = Path.read_text

    def flaky(self, *args, **kwargs):
        if self == store.path:
            raise OSError(24, "Too many open files")
        return real_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", flaky)
    with pytest.raises(OSError):
        store.load()
    monkeypatch.undo()

    assert store.path.exists(), "a transient read error moved the real store aside"
    assert not list(tmp_path.glob("*.corrupt-*"))
    assert set(store.load()) == {"a"}


def test_a_truncated_store_is_still_quarantined(tmp_path: Path) -> None:
    store = ScheduledResearchJobStore(tmp_path / "jobs.json")
    store.upsert(_job("a"))
    store.path.write_text('{"jobs": [', encoding="utf-8")
    with pytest.raises(CorruptStoreError):
        store.load()
    assert list(tmp_path.glob("*.corrupt-*"))


def test_concurrent_writers_lose_nothing(tmp_path: Path) -> None:
    store = ScheduledResearchJobStore(tmp_path / "jobs.json")
    threads_n, per_thread = 8, 12

    def writer(n: int) -> None:
        for i in range(per_thread):
            store.upsert(_job(f"t{n}-{i}"))

    threads = [threading.Thread(target=writer, args=(n,)) for n in range(threads_n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(store.load()) == threads_n * per_thread


def test_transaction_spans_a_caller_held_snapshot(tmp_path: Path) -> None:
    """The executor's load, mutate, save sites hold a snapshot; a transaction keeps a second writer out."""
    store = ScheduledResearchJobStore(tmp_path / "jobs.json")
    store.upsert(_job("a"))
    entered = threading.Event()
    done = threading.Event()

    def other() -> None:
        entered.set()
        store.upsert(_job("b"))
        done.set()

    with store.transaction():
        jobs = store.load()
        t = threading.Thread(target=other)
        t.start()
        entered.wait(2)
        time.sleep(0.2)
        assert not done.is_set(), "a second writer got into the transaction"
        jobs["a"].last_error = "x"
        store.save(jobs)  # re-entrant: the holder may call the store's own locked methods
    t.join(5)
    assert set(store.load()) == {"a", "b"}
    assert store.get("a").last_error == "x"


def test_pause_resume_trigger_cancel_touch_only_their_own_fields(tmp_path: Path) -> None:
    """They used to write the whole record back, reverting whatever the executor wrote in between."""
    store = ScheduledResearchJobStore(tmp_path / "jobs.json")
    store.upsert(_job("a", status=JobStatus.RUNNING))

    stale = store.get("a")  # what a caller read before the run finished
    store.update_run_state("a", status=JobStatus.COMPLETED, consecutive_failures=0, last_error=None)

    real_get = store.get
    store.get = lambda job_id: stale if job_id == "a" else real_get(job_id)  # type: ignore[method-assign]
    set_job_enabled("a", False, store=store)
    store.get = real_get  # type: ignore[method-assign]

    after = store.get("a")
    assert after.paused is True
    assert after.status is JobStatus.COMPLETED, "pausing reverted the executor's completion write"

    store.update_run_state("a", status=JobStatus.RUNNING)
    cancelled = cancel_running_job("a", store=store)
    assert cancelled.status is JobStatus.CANCELLED and store.get("a").config["_cancel_requested"] is True
    store.update_run_state("a", status=JobStatus.PENDING, paused=False)
    assert trigger_job_now("a", store=store).next_run_at <= int(time.time() * 1000) + 1000
