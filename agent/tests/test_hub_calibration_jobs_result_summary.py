"""`hub_evening_maintenance`/`hub_morning_calibration` dispatch must surface their result
summary on the job the same way `index_jobs.py`'s dispatch already does, so an operator can
see it via `job.last_result_summary` (the executor pops `job.config[LAST_RESULT_CONFIG_KEY]`
into that field after a successful dispatch — see `executor.py`'s `_dispatch`).

Before this fix, `dispatch_hub_calibration_job_sync` called `run_hub_evening_maintenance_job`/
`run_hub_morning_calibration_job` and discarded the return value entirely, so a batch that
completed without raising -- including one that silently dropped events into its `errors`
list, e.g. `scenario_autogen` hitting its stale-pipeline-snapshot rebind cap -- left no trace
anywhere an operator would look. See
.claude/backlog/items/2026-09-07-scenario-autogen-stale-pipeline-bind.md.
"""

from __future__ import annotations

import pytest

from src.scheduled_research import hub_calibration_jobs
from src.scheduled_research.models import JobStatus, ScheduledResearchJob


def _job(job_type: str) -> ScheduledResearchJob:
    return ScheduledResearchJob(
        id=f"test-{job_type}",
        prompt="test",
        schedule="0 6 * * *",
        status=JobStatus.PENDING,
        config={"job_type": job_type},
    )


@pytest.mark.unit
def test_evening_maintenance_dispatch_attaches_result_summary(monkeypatch):
    fake_summary = {
        "phase": "evening",
        "status": "partial",
        "steps": {
            "scenario_autogen": {
                "status": "partial",
                "by_ticker": {
                    "NIFTY": {
                        "generated": [],
                        "skipped": [],
                        "errors": [
                            {"event": {"event": "Sept FOMC"}, "error": "stale snapshot, rebind cap hit"}
                        ],
                    }
                },
            }
        },
    }
    monkeypatch.setattr(
        hub_calibration_jobs, "run_hub_evening_maintenance_job", lambda config=None: fake_summary
    )

    job = _job(hub_calibration_jobs.JOB_TYPE_HUB_EVENING_MAINTENANCE)
    hub_calibration_jobs.dispatch_hub_calibration_job_sync(job)

    recorded = job.config.get(hub_calibration_jobs.LAST_RESULT_CONFIG_KEY)
    assert recorded == fake_summary
    # The specific thing this whole fix is about: a silently-dropped-events error must be
    # reachable from the recorded summary, not just from a WARNING log line.
    scenario_errors = recorded["steps"]["scenario_autogen"]["by_ticker"]["NIFTY"]["errors"]
    assert scenario_errors[0]["error"] == "stale snapshot, rebind cap hit"


@pytest.mark.unit
def test_morning_calibration_dispatch_attaches_result_summary(monkeypatch):
    fake_summary = {"phase": "morning", "status": "ok", "steps": {}}
    monkeypatch.setattr(
        hub_calibration_jobs, "run_hub_morning_calibration_job", lambda config=None: fake_summary
    )

    job = _job(hub_calibration_jobs.JOB_TYPE_HUB_MORNING_CALIBRATION)
    hub_calibration_jobs.dispatch_hub_calibration_job_sync(job)

    assert job.config.get(hub_calibration_jobs.LAST_RESULT_CONFIG_KEY) == fake_summary


@pytest.mark.unit
def test_dispatch_unsupported_job_type_still_raises(monkeypatch):
    job = _job("not_a_real_job_type")
    with pytest.raises(ValueError, match="unsupported hub_calibration job_type"):
        hub_calibration_jobs.dispatch_hub_calibration_job_sync(job)
