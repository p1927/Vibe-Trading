"""A dispatch-timeout cancel must survive the next job's dispatch.

See .claude/backlog/items/2026-09-07-dispatch-timeout-cancel-flag-cleared-by-next-job.md —
the cancel signal used to be a single process-wide flag that every dispatch cleared on
entry, so the next due job erased a timed-out job's cancel before that job reached its next
`check_pipeline_cancel()` checkpoint. Live-observed: `nifty-hub-news-ingest-full` was
recorded `failed` on its 90-minute timeout and was still running -- and still writing to the
shared production hub -- 24m44s later.

`asyncio.wait_for` cannot stop that work either: it runs in an `asyncio.to_thread` worker,
and cancelling the await abandons the thread rather than ending it. This flag is the only
mechanism that can stop it, which is why its being silently erased mattered so much.
"""

from __future__ import annotations

import asyncio
import threading
import time

import pytest
from src.scheduled_research import index_jobs
from src.scheduled_research.models import JobStatus, ScheduledResearchJob
from src.scheduled_research.staleness import _request_pipeline_cancel_on_dispatch_timeout
from trade_integrations.dataflows.index_research import pipeline_cancel


@pytest.fixture(autouse=True)
def _isolated_cancel_root(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Cancel flags are files under TRADE_STACK_ROOT/log/index_prediction_jobs. Pin that
    to tmp_path so these tests never touch the real repo's log dir or leak a stray flag
    into another test (see 2026-08-27-pipeline-cancel-test-pollution)."""
    monkeypatch.setenv("TRADE_STACK_ROOT", str(tmp_path))
    pipeline_cancel.set_pipeline_job_id(None)
    yield
    pipeline_cancel.set_pipeline_job_id(None)


def _job(job_id: str, job_type: str = "hub_news_ingest") -> ScheduledResearchJob:
    return ScheduledResearchJob(
        id=job_id,
        prompt="p",
        schedule="1000",
        next_run_at=0,
        status=JobStatus.PENDING,
        created_at=0,
        config={"job_type": job_type},
    )


def test_timeout_cancel_survives_another_jobs_dispatch(monkeypatch: pytest.MonkeyPatch) -> None:
    """The exact regression: job A times out, job B starts, A must still see its cancel."""
    _request_pipeline_cancel_on_dispatch_timeout("job-a", "hub_news_ingest")

    # Job B starts a dispatch. Under the old unscoped clear_pipeline_cancel() this wiped
    # job A's signal entirely.
    monkeypatch.setattr(index_jobs, "_dispatch_index_job_body", lambda job: None)
    index_jobs.dispatch_index_job_sync(_job("job-b"))

    # Now stand in job A's shoes at its next checkpoint.
    pipeline_cancel.set_pipeline_job_id("job-a")
    with pytest.raises(pipeline_cancel.PipelineCancelledError) as excinfo:
        pipeline_cancel.check_pipeline_cancel()
    assert excinfo.value.reason == "dispatch_timeout:job-a"


def test_a_jobs_cancel_does_not_stop_a_different_job(monkeypatch: pytest.MonkeyPatch) -> None:
    """The other half: scoping must not turn one job's timeout into everyone's."""
    _request_pipeline_cancel_on_dispatch_timeout("job-a", "hub_news_ingest")

    seen: list[str] = []
    monkeypatch.setattr(index_jobs, "_dispatch_index_job_body", lambda job: seen.append(job.id))
    index_jobs.dispatch_index_job_sync(_job("job-b"))
    assert seen == ["job-b"]

    pipeline_cancel.set_pipeline_job_id("job-b")
    pipeline_cancel.check_pipeline_cancel()  # must not raise


def test_running_job_sees_its_own_timeout_cancel_at_a_checkpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End to end within one dispatch: the timeout fires mid-run and the run stops."""
    checkpoints: list[int] = []

    def body(job: ScheduledResearchJob) -> None:
        for i in range(5):
            if i == 2:
                # The executor's timeout path, firing while this run is still going.
                _request_pipeline_cancel_on_dispatch_timeout(job.id, "hub_news_ingest")
            pipeline_cancel.check_pipeline_cancel()
            checkpoints.append(i)

    monkeypatch.setattr(index_jobs, "_dispatch_index_job_body", body)
    with pytest.raises(pipeline_cancel.PipelineCancelledError):
        index_jobs.dispatch_index_job_sync(_job("job-a"))
    # Stopped at the first checkpoint after the cancel, not at the end of the loop.
    assert checkpoints == [0, 1]


def test_dispatch_clears_a_stale_flag_from_its_own_previous_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A flag left behind by a process that died mid-run must not cancel every future run."""
    _request_pipeline_cancel_on_dispatch_timeout("job-a", "hub_news_ingest")

    ran: list[str] = []
    monkeypatch.setattr(index_jobs, "_dispatch_index_job_body", lambda job: ran.append(job.id))
    index_jobs.dispatch_index_job_sync(_job("job-a"))  # same job, next run
    assert ran == ["job-a"], "a stale own-flag cancelled the job's next run"


def test_dispatch_does_not_clear_the_global_stop_everything_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The global flag is a stop-everything lever no individual job owns."""
    pipeline_cancel.request_pipeline_cancel("server_reloading")

    monkeypatch.setattr(
        index_jobs, "_dispatch_index_job_body", lambda job: pipeline_cancel.check_pipeline_cancel()
    )
    with pytest.raises(pipeline_cancel.PipelineCancelledError):
        # The global flag is checked first, so this dispatch is itself stopped...
        index_jobs.dispatch_index_job_sync(_job("job-b"))

    # ...and, critically, it is still set afterwards for everyone else.
    with pytest.raises(pipeline_cancel.PipelineCancelledError) as excinfo:
        pipeline_cancel.check_pipeline_cancel()
    assert excinfo.value.reason == "server_reloading"


def test_concurrent_dispatches_do_not_share_a_bound_job_id() -> None:
    """`_current_job_id` is a ContextVar and dispatches run under `asyncio.to_thread`,
    which copies the context per call. Two concurrent dispatches must not see each other's
    binding, or a scoped cancel would hit the wrong job."""
    both_running = threading.Barrier(2, timeout=10)

    def body(job: ScheduledResearchJob) -> None:
        # Rendezvous so both dispatches are provably in flight together before
        # either cancel is issued -- otherwise this could pass by accident with
        # the two runs merely happening not to overlap.
        both_running.wait()
        if job.id == "job-a":
            # Issued from inside the run, the way the executor's timeout path
            # does it. Setting it *before* the dispatch would not work: entry
            # deliberately clears the job's own stale flag (see the test above).
            _request_pipeline_cancel_on_dispatch_timeout(job.id, "hub_news_ingest")
        for _ in range(20):
            pipeline_cancel.check_pipeline_cancel()
            time.sleep(0.005)

    async def scenario() -> tuple[BaseException | None, BaseException | None]:
        import src.scheduled_research.index_jobs as ij

        original = ij._dispatch_index_job_body
        ij._dispatch_index_job_body = body
        try:
            results = await asyncio.gather(
                asyncio.to_thread(ij.dispatch_index_job_sync, _job("job-a")),
                asyncio.to_thread(ij.dispatch_index_job_sync, _job("job-b")),
                return_exceptions=True,
            )
        finally:
            ij._dispatch_index_job_body = original
        return results[0], results[1]

    a_result, b_result = asyncio.run(scenario())
    assert isinstance(a_result, pipeline_cancel.PipelineCancelledError)
    assert b_result is None, f"job-b was cancelled by job-a's flag: {b_result!r}"
