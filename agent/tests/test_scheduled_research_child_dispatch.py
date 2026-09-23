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
