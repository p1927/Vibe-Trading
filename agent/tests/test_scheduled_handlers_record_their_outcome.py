"""Handlers that computed a summary and dropped it, or never read it for errors (review vibe-scheduler).

Each used to leave ``last_result_summary: null`` on the record, and a run whose summary said it
failed was recorded ``completed``. They now end through ``run_outcome.finish_run``.
"""

from __future__ import annotations

import asyncio

import pytest

from src.scheduled_research import (
    autonomous_agent_jobs,
    capture_jobs,
    decision_grading_jobs,
    execution_advisor_jobs,
    financial_knowledge_jobs,
    hub_calibration_jobs,
    recording_wake_jobs,
    trade_data_jobs,
)
from src.scheduled_research.models import ScheduledResearchJob
from src.scheduled_research.run_outcome import LAST_RESULT_CONFIG_KEY, JobRunHadErrorsError


def _job(job_type: str, **config) -> ScheduledResearchJob:
    return ScheduledResearchJob(
        id=f"{job_type}-job", prompt="p", schedule="60000", config={"job_type": job_type, **config}
    )


def test_one_definition_of_the_summary_key() -> None:
    from src.scheduled_research import executor, index_jobs

    assert executor.LAST_RESULT_CONFIG_KEY is LAST_RESULT_CONFIG_KEY
    assert index_jobs.LAST_RESULT_CONFIG_KEY is LAST_RESULT_CONFIG_KEY
    assert hub_calibration_jobs.LAST_RESULT_CONFIG_KEY is LAST_RESULT_CONFIG_KEY


def test_decision_grading_sweep_leaves_its_summary(monkeypatch) -> None:
    summary = {"sweep": {"scored": 0, "unscored": 33, "already_done": 0}, "proposals": {"aa_1": "none"}}
    monkeypatch.setattr(decision_grading_jobs, "run_decision_grading_sweep", lambda: summary)
    job = _job(decision_grading_jobs.JOB_TYPE_DECISION_GRADING_SWEEP)
    decision_grading_jobs.dispatch_decision_grading_job_sync(job)
    assert job.config[LAST_RESULT_CONFIG_KEY] == summary


def test_execution_advisor_sweep_leaves_its_summary(monkeypatch) -> None:
    summary = {"positions": 2, "actions": {"hold": 2}}
    monkeypatch.setattr(execution_advisor_jobs, "run_execution_advisor_sweep", lambda: summary)
    job = _job(execution_advisor_jobs.JOB_TYPE_EXECUTION_ADVISOR_SWEEP)
    execution_advisor_jobs.dispatch_execution_advisor_job_sync(job)
    assert job.config[LAST_RESULT_CONFIG_KEY] == summary


@pytest.mark.parametrize(
    "job_type, runner",
    [
        (trade_data_jobs.JOB_TYPE_TRADE_FILLS_EXPORT, "run_trade_fills_export_job"),
        (trade_data_jobs.JOB_TYPE_RESEARCH_HISTORY_ARCHIVE, "run_research_history_archive_job"),
        (trade_data_jobs.JOB_TYPE_NSE_MACRO_REFRESH, "run_nse_macro_refresh_job"),
        (trade_data_jobs.JOB_TYPE_NSE_REPO_CONSISTENCY, "run_nse_repo_consistency_job"),
    ],
)
def test_trade_data_jobs_leave_their_summary_and_fail_on_error(monkeypatch, job_type, runner) -> None:
    monkeypatch.setattr(trade_data_jobs, runner, lambda config=None: {"status": "ok", "rows": 3})
    job = _job(job_type)
    trade_data_jobs.dispatch_trade_data_job_sync(job)
    assert job.config[LAST_RESULT_CONFIG_KEY] == {"status": "ok", "rows": 3}

    monkeypatch.setattr(trade_data_jobs, runner, lambda config=None: {"status": "error", "error": "boom"})
    with pytest.raises(JobRunHadErrorsError, match="boom"):
        trade_data_jobs.dispatch_trade_data_job_sync(_job(job_type))


def test_intraday_capture_with_a_failed_entity_fails_the_run(monkeypatch) -> None:
    partial = {
        "status": "partial",
        "entities": {"NIFTY": {"status": "error", "errors": ["chain fetch failed"]}, "SPX": {"status": "skipped"}},
    }
    monkeypatch.setattr(capture_jobs, "run_hub_capture_intraday_job", lambda config=None: partial)
    job = _job(capture_jobs.JOB_TYPE_HUB_CAPTURE_INTRADAY)
    with pytest.raises(JobRunHadErrorsError, match="NIFTY"):
        capture_jobs.dispatch_hub_capture_job_sync(job)
    assert job.config[LAST_RESULT_CONFIG_KEY]["entities"]["NIFTY"]["status"] == "error"


def test_intraday_capture_that_only_skipped_is_not_a_failure(monkeypatch) -> None:
    quiet = {"status": "partial", "entities": {"NIFTY": {"status": "skipped", "reason": "gate_closed"}}}
    monkeypatch.setattr(capture_jobs, "run_hub_capture_intraday_job", lambda config=None: quiet)
    job = _job(capture_jobs.JOB_TYPE_HUB_CAPTURE_INTRADAY)
    capture_jobs.dispatch_hub_capture_job_sync(job)
    assert job.config[LAST_RESULT_CONFIG_KEY] == quiet


@pytest.mark.parametrize(
    "report, fails",
    [
        ({"ok": True, "sources": {}}, False),
        ({"ok": True, "skipped": True, "reason": "no_new_sources"}, False),
        ({"ok": False, "skipped": True, "reason": "no_generative_adapter_configured"}, False),
        ({"ok": False, "error": "curation blew up"}, True),
        ({"ok": True, "ingest": {"ok": False, "error": "wiki rescan refused"}}, True),
    ],
)
def test_curator_report_decides_the_run(monkeypatch, report, fails) -> None:
    monkeypatch.setattr(financial_knowledge_jobs, "run_financial_knowledge_curator_job", lambda config=None: dict(report))
    job = _job(financial_knowledge_jobs.JOB_TYPE_FINANCIAL_KNOWLEDGE_CURATOR)
    if fails:
        with pytest.raises(JobRunHadErrorsError):
            financial_knowledge_jobs.dispatch_financial_knowledge_job_sync(job)
    else:
        financial_knowledge_jobs.dispatch_financial_knowledge_job_sync(job)
    assert LAST_RESULT_CONFIG_KEY in job.config


def test_a_rolled_up_calibration_error_fails_the_run(monkeypatch) -> None:
    bad = {"phase": "evening", "status": "error", "steps": {"archive": {"status": "error", "error": "disk full"}}}
    monkeypatch.setattr(hub_calibration_jobs, "run_hub_evening_maintenance_job", lambda config=None: bad)
    job = _job(hub_calibration_jobs.JOB_TYPE_HUB_EVENING_MAINTENANCE)
    with pytest.raises(JobRunHadErrorsError, match="disk full"):
        hub_calibration_jobs.dispatch_hub_calibration_job_sync(job)
    assert job.config[LAST_RESULT_CONFIG_KEY] == bad


def test_a_wake_job_without_a_recording_id_fails() -> None:
    job = _job(recording_wake_jobs.JOB_TYPE_RECORDING_WAKE)
    with pytest.raises(ValueError, match="recording_job_id"):
        asyncio.run(recording_wake_jobs._dispatch_recording_wake_job_inner(job))


def test_an_autonomous_job_without_an_agent_or_with_an_unknown_type_fails() -> None:
    with pytest.raises(ValueError, match="autonomous_agent_id"):
        asyncio.run(autonomous_agent_jobs._dispatch_autonomous_job_inner(_job("autonomous_agent_watch")))
    with pytest.raises(ValueError, match="unsupported autonomous job_type"):
        asyncio.run(
            autonomous_agent_jobs._dispatch_autonomous_job_inner(
                _job("autonomous_agent_nonsense", autonomous_agent_id="aa_1")
            )
        )


def test_every_dispatchable_job_type_has_a_description() -> None:
    from src.scheduled_research import job_dispatch_registry
    from src.scheduled_research.job_details import GENERIC_DESCRIPTION, job_type_detail

    undescribed = sorted(
        job_type
        for loader in job_dispatch_registry._DISPATCH_LOADERS
        for job_type in loader()[0]
        if job_type_detail(job_type).description == GENERIC_DESCRIPTION
    )
    assert not undescribed, f"Scheduler tab shows no description for: {undescribed}"
