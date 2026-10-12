"""Scheduled-run failures that used to be silent, and the stage trail a timed-out run leaves.

Backlog: 2026-10-12-index-plan-refresh-swallows-errors-ledger-flood (the poll job reported success
when its refresh raised), 2026-10-12-no-live-spot-run-records-nothing-silently (a run with no spot
finished "with warnings", wrote no ledger row and counted as a normal run) and
2026-10-12-scheduled-run-stage-log-not-retained (a timed-out run's stage stream lived only in memory).
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from src.scheduled_research import index_jobs
from src.scheduled_research.executor import ScheduledResearchExecutor
from src.scheduled_research.models import JobStatus, ScheduledResearchJob
from src.scheduled_research.run_log_buffer import append_log, clear_logs
from src.scheduled_research.run_outcome import JobRunHadErrorsError
from src.scheduled_research.stage_log import MAX_STAGE_RECORDS, build_stage_log
from src.scheduled_research.store import ScheduledResearchJobStore
from src.trade import index_prediction_run_jobs as run_jobs

pytestmark = pytest.mark.unit


def _line(at: int, message: str) -> dict:
    return {"seq": at, "at": at, "message": message}


# --- stage log -------------------------------------------------------------------------------------


def test_stage_log_pairs_start_and_end_lines_in_both_duration_units() -> None:
    log = build_stage_log(
        [
            _line(1000, "starting (index_research)"),  # not a stage line
            _line(1000, "stage a: starting"),
            _line(4000, "stage a: done in 3.0s"),
            _line(4000, "stage b: starting"),
            _line(4500, "stage b: failed after 500ms: boom: with a colon"),
            _line(5000, "stage c: done in 250ms"),  # an end line with no start line
            _line(5000, "completed"),
        ],
        ended_at_ms=9000,
    )
    assert [(r["stage"], r["status"], r["duration_ms"]) for r in log] == [
        ("a", "ok", 3000), ("b", "failed", 500), ("c", "ok", 250),
    ]
    assert log[0]["started_at"] == 1000 and log[0]["ended_at"] == 4000
    assert log[1]["error"] == "boom: with a colon"
    assert log[2]["started_at"] == 4750  # placed by its duration
    assert log[0]["error"] is None


def test_stage_log_marks_a_stage_with_no_end_line_unfinished_up_to_the_run_end() -> None:
    log = build_stage_log(
        [_line(0, "stage a: starting"), _line(10, "stage a: done in 10ms"), _line(20, "stage b: starting")],
        ended_at_ms=1800020,
    )
    assert log[-1]["stage"] == "b" and log[-1]["status"] == "unfinished"
    assert log[-1]["ended_at"] == 1800020 and log[-1]["duration_ms"] == 1800000


def test_stage_log_repeated_stage_names_pair_in_order_and_the_log_is_capped() -> None:
    entries = []
    for i in range(MAX_STAGE_RECORDS + 10):
        entries += [_line(i * 10, "stage persist: starting"), _line(i * 10 + 5, "stage persist: done in 5ms")]
    log = build_stage_log(entries, ended_at_ms=10**9)
    assert len(log) == MAX_STAGE_RECORDS
    assert all(r["status"] == "ok" and r["duration_ms"] == 5 for r in log)
    assert log[-1]["started_at"] == (MAX_STAGE_RECORDS + 9) * 10  # the newest are kept
    assert build_stage_log([], ended_at_ms=1) == []
    assert build_stage_log([_line(0, "stage: nonsense"), _line(1, "stage x: done in abc")], ended_at_ms=2) == []


def _job(job_id: str, config: dict) -> ScheduledResearchJob:
    return ScheduledResearchJob(id=job_id, prompt="p", schedule="1000", next_run_at=10, config=config)


def test_a_timed_out_run_leaves_its_stage_trail_on_the_persisted_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TRADE_STACK_ROOT", str(tmp_path))
    monkeypatch.setenv("STACK_PROFILE", "release")
    store = ScheduledResearchJobStore(path=tmp_path / "jobs.json")
    store.upsert(_job("slow", {"job_type": "index_plan_refresh", "dispatch_timeout_ms": 80}))
    clear_logs("slow")

    async def dispatch(job: ScheduledResearchJob) -> None:
        append_log(job.id, "stage snapshot_first: starting")
        append_log(job.id, "stage snapshot_first: done in 0.0s")
        append_log(job.id, "stage constituents: starting")  # the stage the time goes into
        await asyncio.sleep(0.5)

    async def scenario() -> None:
        await ScheduledResearchExecutor(store, dispatch, tick_interval_ms=1).tick(100)

    asyncio.run(scenario())

    saved = ScheduledResearchJobStore(path=tmp_path / "jobs.json").get("slow")  # a fresh read of the file
    assert saved is not None and "timed out" in (saved.last_error or "")
    assert [(r["stage"], r["status"]) for r in saved.last_stage_log] == [
        ("snapshot_first", "ok"), ("constituents", "unfinished"),
    ]
    assert saved.last_stage_log[-1]["duration_ms"] >= 0
    # and the API shape carries it
    assert saved.to_flat_dict()["last_stage_log"] == saved.last_stage_log


def test_a_run_that_reports_no_stages_leaves_no_stage_log(tmp_path: Path) -> None:
    store = ScheduledResearchJobStore(path=tmp_path / "jobs.json")
    store.upsert(_job("quiet", {}))
    clear_logs("quiet")

    async def dispatch(job: ScheduledResearchJob) -> None:
        append_log(job.id, "just a line")

    asyncio.run(ScheduledResearchExecutor(store, dispatch).tick(100))
    assert store.get("quiet").last_stage_log is None


def test_records_written_before_the_field_existed_still_load(tmp_path: Path) -> None:
    old = _job("old", {}).to_dict()
    del old["state"]["last_stage_log"]
    assert ScheduledResearchJob.from_dict(old).last_stage_log is None


# --- no_live_spot is a retry-able failure, any other error auto-pauses at the threshold ------------------


@pytest.mark.parametrize(
    "error, auto_paused",
    [
        (lambda: __import__(
            "trade_integrations.dataflows.index_research.spot_fetch", fromlist=["NoLiveSpotError"]
        ).NoLiveSpotError("OpenAlgo quote unavailable"), False),
        # the text of the same failure after a child process boundary (child_dispatch re-raises it so)
        (lambda: RuntimeError("NoLiveSpotError: no_live_spot: OpenAlgo quote unavailable"), False),
        (lambda: RuntimeError("some other failure"), True),
    ],
)
def test_no_live_spot_failures_are_recorded_and_retried_never_auto_paused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, error, auto_paused: bool
) -> None:
    monkeypatch.setenv("STACK_PROFILE", "release")
    store = ScheduledResearchJobStore(path=tmp_path / "jobs.json")
    store.upsert(_job("poll", {"job_type": "index_plan_refresh"}))

    async def dispatch(job: ScheduledResearchJob) -> None:
        raise error()

    async def scenario() -> None:
        executor = ScheduledResearchExecutor(
            store, dispatch, max_consecutive_failures=2, retry_base_delay_ms=1, retry_max_delay_ms=1
        )
        for now in (100, 10_000, 20_000, 30_000):
            await executor.tick(now)

    asyncio.run(scenario())
    saved = store.get("poll")
    # counted on every failure; an auto-paused job stops at the threshold (2), a retried one keeps going
    assert saved.failure_kind == "dispatch" and saved.consecutive_failures >= (2 if auto_paused else 3)
    assert saved.paused is auto_paused
    assert saved.status == JobStatus.PENDING


# --- the scheduled job bodies --------------------------------------------------------------------------


def test_plan_refresh_job_propagates_a_failed_refresh(monkeypatch: pytest.MonkeyPatch) -> None:
    """It used to catch everything and return {"reason": "error"}: a normal completed run."""
    monkeypatch.setattr(
        "src.trade.index_prediction_run_jobs.get_active_job", lambda ticker: None
    )

    def boom(*_a, **_k):
        raise RuntimeError("macro snapshot exploded")

    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.light_refresh.run_index_light_refresh", boom
    )
    with pytest.raises(RuntimeError, match="macro snapshot exploded"):
        index_jobs.run_index_plan_refresh_job({"ticker": "NIFTY"})


def test_plan_refresh_job_reports_superseded_and_unchanged_as_not_refreshed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("src.trade.index_prediction_run_jobs.get_active_job", lambda ticker: None)
    for reason in ("unchanged", "superseded_by_full_run"):
        monkeypatch.setattr(
            "trade_integrations.dataflows.index_research.light_refresh.run_index_light_refresh",
            lambda *_a, _reason=reason, **_k: (None, _reason),
        )
        out = index_jobs.run_index_plan_refresh_job({"ticker": "NIFTY"})
        assert out == {"skipped": False, "ticker": "NIFTY", "reason": reason, "refreshed": False}


@pytest.fixture
def _isolated_run_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(run_jobs, "INDEX_PREDICTION_RUN_JOBS", {})
    monkeypatch.setattr(run_jobs, "_ACTIVE_BY_TICKER", {})
    monkeypatch.setattr("src.trade.hub_bridge.trade_repo_root", lambda: tmp_path)
    monkeypatch.setenv("TRADE_STACK_ROOT", str(tmp_path))
    monkeypatch.setenv("TRADE_STACK_HUB_DIR", str(tmp_path / "hub"))


def test_a_scheduled_index_research_run_with_no_live_spot_fails_with_the_reason(
    _isolated_run_store, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The quote door closed: the manual-run record the job starts ends `error` with `no_live_spot`, and
    the scheduled run FAILS with that reason (it was recorded `completed`), which the executor then
    retries rather than auto-pauses (the aggregator's own raise is tested in test_index_aggregator.py)."""
    from trade_integrations.dataflows.index_research.spot_fetch import (
        SpotFetchResult,
        is_no_live_spot_error,
    )

    # the real run_index_research reaches its spot stage; everything before it is a vendor/data door
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.spot_fetch.fetch_index_spot",
        lambda _t: SpotFetchResult(0.0, "unavailable", "OpenAlgo quote unavailable"),
    )

    def to_spot_stage(*_a, **_k):
        # run_index_research up to its spot stage is covered by tests/test_index_aggregator.py; here the
        # aggregator is replaced by the one thing under test: what the job layer does with its failure
        from trade_integrations.dataflows.index_research.spot_fetch import NoLiveSpotError, fetch_index_spot

        raise NoLiveSpotError(fetch_index_spot("NIFTY").error)

    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.aggregator.run_index_research", to_spot_stage
    )
    job = _job("nifty-index-research", {"job_type": index_jobs.JOB_TYPE_INDEX_RESEARCH, "ticker": "NIFTY"})
    with pytest.raises(JobRunHadErrorsError, match="no_live_spot: OpenAlgo quote unavailable") as err:
        index_jobs._dispatch_index_job_body(job)
    assert is_no_live_spot_error(err.value)
    records = [r for r in run_jobs.INDEX_PREDICTION_RUN_JOBS.values() if r.get("ticker") == "NIFTY"]
    assert records and records[0]["status"] == "error"
    assert str(records[0]["error"]).startswith("no_live_spot: ")


def _target_stages_then_hangs(job: ScheduledResearchJob) -> None:
    import time

    from src.scheduled_research.run_log_buffer import append_log
    from trade_integrations.dataflows.index_research.pipeline_cancel import set_stage_sink
    from trade_integrations.dataflows.index_research.pipeline_log import PipelineLogger

    # what `index_jobs.dispatch_index_job_sync` binds around every index job's body
    set_stage_sink(lambda message: append_log(job.id, message))
    log = PipelineLogger()
    with log.stage_timer("history", "Nifty OHLCV tail sync"):
        pass
    with log.stage_timer("constituents", "Loading constituent signals"):
        time.sleep(300)


def test_a_child_run_killed_at_its_budget_names_the_stage_it_was_in(monkeypatch: pytest.MonkeyPatch) -> None:
    """End to end through the real child process and the real pipeline stage timer (the one every
    index-research stage goes through): the supervisor kills the hung run at the dispatch budget, and
    the stage trail built from the job's log shows the stage that ate the time as unfinished."""
    import time

    from src.scheduled_research import child_dispatch
    from src.scheduled_research.stage_log import stage_log_for_run
    from trade_integrations.job_deadline import job_deadline

    monkeypatch.setenv("TRADE_INTEGRATIONS_SKIP_APPLY", "1")
    job = _job("stage-trail-child", {"job_type": "index_research"})
    clear_logs(job.id)
    with job_deadline(25), pytest.raises(TimeoutError, match="dispatch budget"):
        child_dispatch.in_child(_target_stages_then_hangs)(job)
    log = stage_log_for_run(job.id, ended_at_ms=int(time.time() * 1000))
    assert [(r["stage"], r["status"]) for r in log] == [("history", "ok"), ("constituents", "unfinished")]
    assert log[-1]["duration_ms"] > 1000  # the hang is accounted to the stage it happened in
