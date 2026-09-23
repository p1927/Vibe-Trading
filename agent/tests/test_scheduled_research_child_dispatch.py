"""Heavy scheduled jobs run in a supervised child process (Trade D244): real children, no mocks.

The targets below are imported by the child as ``tests.test_scheduled_research_child_dispatch``.
"""

from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path

import pytest
from src.scheduled_research import child_dispatch, dst_eval_jobs
from src.scheduled_research.models import JobStatus, ScheduledResearchJob


def _target_ok(job: ScheduledResearchJob) -> None:
    print("working in the child", flush=True)
    job.config["_last_result"] = {"status": "ok", "pid": os.getpid()}


def _target_fails(job: ScheduledResearchJob) -> None:
    raise ValueError("eval blew up")


def _target_hangs(job: ScheduledResearchJob) -> None:
    Path(job.config["pid_file"]).write_text(str(os.getpid()))
    time.sleep(300)


def _job(**config) -> ScheduledResearchJob:
    return ScheduledResearchJob(
        id="child-dispatch-test", prompt="p", schedule="0 2 * * *", next_run_at=0,
        status=JobStatus.PENDING, created_at=0, config={"job_type": "prediction_eval", **config},
    )


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("TRADE_STACK_ROOT", str(Path(__file__).resolve().parents[3]))
    monkeypatch.setenv("TRADE_INTEGRATIONS_SKIP_APPLY", "1")  # D213 (2)


def test_dst_eval_jobs_dispatch_in_a_child(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = {}

    async def _fake_run_logged(job, dispatch):
        seen["dispatch"] = dispatch

    monkeypatch.setattr("src.scheduled_research.run_log_buffer.run_logged", _fake_run_logged)
    asyncio.run(dst_eval_jobs.dispatch_dst_eval_job(_job()))
    assert seen["dispatch"].func is child_dispatch.run_in_child
    assert seen["dispatch"].keywords["dispatch_sync"] is dst_eval_jobs.dispatch_dst_eval_job_sync


def test_child_runs_the_dispatch_and_returns_its_result() -> None:
    from src.scheduled_research.run_log_buffer import get_logs_since

    job = _job()
    child_dispatch.in_child(_target_ok)(job)
    assert job.config["_last_result"]["status"] == "ok"
    assert job.config["_last_result"]["pid"] != os.getpid()
    assert any("working in the child" in e["message"] for e in get_logs_since(job.id))


def test_child_failure_is_raised() -> None:
    with pytest.raises(RuntimeError, match="ValueError: eval blew up"):
        child_dispatch.in_child(_target_fails)(_job())


def test_timeout_kills_the_child(tmp_path) -> None:
    from trade_integrations.job_deadline import job_deadline

    pid_file = tmp_path / "pid"
    started = time.monotonic()
    with job_deadline(20), pytest.raises(TimeoutError, match="dispatch budget"):
        child_dispatch.in_child(_target_hangs)(_job(pid_file=str(pid_file)))
    assert time.monotonic() - started < 30
    pid = int(pid_file.read_text())  # the child got as far as the hanging work
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


# --- The heavy job types beyond dst_eval (heavy-job-types-child-process-migration) ---------------


def _target_reports(job: ScheduledResearchJob) -> None:
    from src.scheduled_research.run_log_buffer import append_log
    from trade_integrations.child_process import in_supervised_child
    from trade_integrations.job_deadline import remaining_seconds

    append_log(job.id, "stage x: done")  # what a dispatch's stage sink does
    job.config["_seen"] = {"in_child": in_supervised_child(), "budget": remaining_seconds()}


def _target_leaves_work_behind(job: ScheduledResearchJob) -> None:
    import subprocess
    import threading

    proc = subprocess.Popen(["sleep", "300"])  # a browser a library started, say
    Path(job.config["pid_file"]).write_text(str(proc.pid))
    threading.Thread(target=time.sleep, args=(300,)).start()  # non-daemon: an abandoned worker


def test_child_gets_the_budget_the_log_and_the_child_marker() -> None:
    from src.scheduled_research.run_log_buffer import get_logs_since
    from trade_integrations.job_deadline import job_deadline

    job = _job()
    with job_deadline(120):
        child_dispatch.in_child(_target_reports)(job)
    assert job.config["_seen"]["in_child"] is True
    assert 60 < job.config["_seen"]["budget"] <= 120
    assert any(e["message"] == "stage x: done" for e in get_logs_since(job.id))


def test_a_cancel_at_the_deadline_gets_no_grace_past_it(tmp_path) -> None:
    """The executor writes the job's cancel flag when its dispatch budget runs out; the child's
    10 s cancel grace must not extend the run past that same budget (it used to: +10 s)."""
    import threading

    from trade_integrations.dataflows.index_research.pipeline_cancel import (
        PipelineCancelledError,
        request_pipeline_cancel,
    )
    from trade_integrations.job_deadline import job_deadline

    job = _job(pid_file=str(tmp_path / "pid"))
    timer = threading.Timer(4.5, request_pipeline_cancel, args=("dispatch_timeout",), kwargs={"job_id": job.id})
    started = time.monotonic()
    timer.start()
    try:
        with job_deadline(5), pytest.raises((TimeoutError, PipelineCancelledError)):
            child_dispatch.in_child(_target_hangs)(job)
    finally:
        timer.cancel()
    assert time.monotonic() - started < 7


def test_nothing_the_child_started_outlives_its_run(tmp_path) -> None:
    pid_file = tmp_path / "pid"
    started = time.monotonic()
    child_dispatch.in_child(_target_leaves_work_behind)(_job(pid_file=str(pid_file)))
    assert time.monotonic() - started < 60  # the non-daemon thread did not hold the child open
    pid = int(pid_file.read_text())
    for _ in range(50):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.1)
    else:
        pytest.fail(f"grandchild {pid} outlived the child")


@pytest.mark.parametrize(
    ("module", "job_type", "heavy"),
    [
        ("index_jobs", "hub_news_ingest", True),
        ("index_jobs", "hub_news_entity", True),
        ("index_jobs", "news_quality_eval", True),
        ("index_jobs", "news_dedup_quality_eval", True),
        ("index_jobs", "index_research", True),
        ("index_jobs", "index_calibration", True),
        ("index_jobs", "index_prediction_post_close", True),
        ("index_jobs", "forecast_platform_retrain", True),
        ("index_jobs", "oi_snapshot", False),
        ("index_jobs", "stock_history_coverage_sweep", False),
        ("hub_calibration_jobs", "hub_morning_calibration", True),
        ("hub_calibration_jobs", "hub_evening_maintenance", True),
        ("financial_knowledge_jobs", "financial_knowledge_curator", True),
        ("options_jobs", "options_plan_refresh", True),
        ("options_jobs", "options_position_monitor", False),
        ("trade_data_jobs", "nse_macro_refresh", True),
        ("trade_data_jobs", "nse_repo_consistency", True),
        ("trade_data_jobs", "trade_fills_export", False),
    ],
)
def test_heavy_job_types_dispatch_in_a_child(monkeypatch, module, job_type, heavy) -> None:
    import importlib

    mod = importlib.import_module(f"src.scheduled_research.{module}")
    family = module.removesuffix("_jobs")
    dispatch, sync = getattr(mod, f"dispatch_{family}_job"), getattr(mod, f"dispatch_{family}_job_sync")
    seen = {}

    async def _fake_run_logged(job, fn):
        seen["dispatch"] = fn

    monkeypatch.setattr("src.scheduled_research.run_log_buffer.run_logged", _fake_run_logged)
    asyncio.run(dispatch(_job(job_type=job_type)))
    if heavy:
        assert seen["dispatch"].func is child_dispatch.run_in_child
        assert seen["dispatch"].keywords["dispatch_sync"] is sync
    else:
        assert seen["dispatch"] is sync


@pytest.mark.parametrize(
    ("job_type", "heavy"),
    [
        ("autonomous_agent_quant", True),
        ("autonomous_agent_watch", False),
        ("autonomous_agent_news", False),
    ],
)
def test_autonomous_quant_tick_dispatches_in_a_child(monkeypatch, job_type, heavy) -> None:
    """Trade backlog 2026-09-23-autonomous-agent-quant-long-thread-runs: the quant tick (quant
    review rebuild) runs in the child; the child's entry runs the inner dispatch itself, so it
    never re-enters `in_child`."""
    from src.scheduled_research import autonomous_agent_jobs as mod

    seen = {}

    async def _fake_run_logged(job, fn, **kwargs):
        seen["dispatch"] = fn

    monkeypatch.setattr("src.scheduled_research.run_log_buffer.run_logged", _fake_run_logged)
    asyncio.run(mod.dispatch_autonomous_job(_job(job_type=job_type, autonomous_agent_id="aa_x")))
    if heavy:
        assert seen["dispatch"].func is child_dispatch.run_in_child
        assert seen["dispatch"].keywords["dispatch_sync"] is mod.dispatch_autonomous_job_sync
        ran = []

        async def _inner(job):
            ran.append(job.id)

        monkeypatch.setattr(mod, "_dispatch_autonomous_job_inner", _inner)
        mod.dispatch_autonomous_job_sync(_job(job_type=job_type))
        assert len(ran) == 1 and "dispatch" in seen
    else:
        assert seen["dispatch"] is mod._dispatch_autonomous_job_inner
