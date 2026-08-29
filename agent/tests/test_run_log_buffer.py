"""Tests for the bounded per-job log buffer feeding the Scheduler tab's
live-log-tail (see .claude/backlog/items/2026-08-29-unified-scheduler-registry.md,
step 6). Covers append/since/clear semantics, eviction under the maxlen cap,
and the ``run_logged`` wrapper both 7 dispatch modules call from their async
entry point.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.scheduled_research import run_log_buffer as buf


@pytest.fixture(autouse=True)
def _isolate_buffers():
    yield
    buf._BUFFERS.clear()
    buf._SEQ_COUNTERS.clear()


def test_append_and_get_all():
    buf.append_log("job-1", "hello")
    buf.append_log("job-1", "world")
    entries = buf.get_logs_since("job-1")
    assert [e["message"] for e in entries] == ["hello", "world"]
    assert [e["seq"] for e in entries] == [1, 2]


def test_get_logs_since_only_returns_new_entries():
    buf.append_log("job-1", "a")
    first = buf.get_logs_since("job-1")
    buf.append_log("job-1", "b")
    second = buf.get_logs_since("job-1", since_seq=first[-1]["seq"])
    assert [e["message"] for e in second] == ["b"]


def test_unknown_job_returns_empty():
    assert buf.get_logs_since("nonexistent") == []


def test_buffers_are_isolated_per_job():
    buf.append_log("job-1", "one")
    buf.append_log("job-2", "two")
    assert [e["message"] for e in buf.get_logs_since("job-1")] == ["one"]
    assert [e["message"] for e in buf.get_logs_since("job-2")] == ["two"]


def test_clear_logs_resets_buffer_and_sequence():
    buf.append_log("job-1", "a")
    buf.append_log("job-1", "b")
    buf.clear_logs("job-1")
    assert buf.get_logs_since("job-1") == []
    buf.append_log("job-1", "fresh")
    # Sequence restarts at 1 rather than continuing from where it left off —
    # a stale `since_seq` from a previous run's SSE client would otherwise
    # incorrectly suppress this first new entry.
    assert buf.get_logs_since("job-1")[0]["seq"] == 1


def test_eviction_past_maxlen_does_not_break_since_seq_filtering():
    for i in range(buf._MAX_LOGS_PER_JOB + 50):
        buf.append_log("job-1", f"line-{i}")
    all_entries = buf.get_logs_since("job-1")
    assert len(all_entries) == buf._MAX_LOGS_PER_JOB
    # The earliest surviving entry's seq reflects eviction (not 1), and
    # filtering by a seq from before eviction still returns everything left.
    earliest_seq = all_entries[0]["seq"]
    assert earliest_seq == 51
    assert buf.get_logs_since("job-1", since_seq=0) == all_entries


@pytest.mark.asyncio
async def test_run_logged_success_records_start_and_complete():
    job = SimpleNamespace(id="job-1", config={"job_type": "index_research"})

    async def fake_dispatch(j):
        buf.append_log(j.id, "did the work")

    await buf.run_logged(job, fake_dispatch, run_in_thread=False)

    messages = [e["message"] for e in buf.get_logs_since("job-1")]
    assert messages == ["starting (index_research)", "did the work", "completed"]


@pytest.mark.asyncio
async def test_run_logged_reraises_and_logs_failure():
    job = SimpleNamespace(id="job-1", config={})

    async def failing_dispatch(_j):
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        await buf.run_logged(job, failing_dispatch, run_in_thread=False)

    messages = [e["message"] for e in buf.get_logs_since("job-1")]
    assert messages == ["starting", "failed: boom"]


@pytest.mark.asyncio
async def test_run_logged_runs_sync_dispatch_in_a_thread():
    job = SimpleNamespace(id="job-1", config={"job_type": "options_plan_refresh"})
    calls = []

    def sync_dispatch(j):
        calls.append(j.id)

    await buf.run_logged(job, sync_dispatch, run_in_thread=True)

    assert calls == ["job-1"]
    messages = [e["message"] for e in buf.get_logs_since("job-1")]
    assert messages == ["starting (options_plan_refresh)", "completed"]


@pytest.mark.asyncio
async def test_run_logged_clears_previous_run_logs():
    job = SimpleNamespace(id="job-1", config={})
    buf.append_log("job-1", "stale from a previous run")

    async def fake_dispatch(_j):
        pass

    await buf.run_logged(job, fake_dispatch, run_in_thread=False)

    messages = [e["message"] for e in buf.get_logs_since("job-1")]
    assert "stale from a previous run" not in messages
