"""futures_positioning scheduled job (Phase 2 step 9 of
[[2026-09-02-futures-positioning-factor-pipeline]] — daily post-close raw
futures-OI persistence, same shape as pump_dump_proxy/max_pain_bhavcopy)."""
from __future__ import annotations

import pytest

from src.scheduled_research import index_jobs


@pytest.mark.unit
def test_run_futures_positioning_job_delegates_to_persist_futures_day(monkeypatch):
    calls = {}

    def _fake_persist(trade_date):
        calls["trade_date"] = trade_date
        return {"status": "ok", "trading_day": trade_date, "rows_written": 647}

    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.futures_positioning_store.persist_futures_day",
        _fake_persist,
    )

    result = index_jobs.run_futures_positioning_job({"trading_day": "2026-08-28"})

    assert calls["trade_date"] == "2026-08-28"
    assert result["status"] == "ok"
    assert result["rows_written"] == 647


@pytest.mark.unit
def test_run_futures_positioning_job_defaults_to_ist_today(monkeypatch):
    calls = {}

    def _fake_persist(trade_date):
        calls["trade_date"] = trade_date
        return {"status": "no_data", "trading_day": trade_date, "rows_written": 0}

    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.futures_positioning_store.persist_futures_day",
        _fake_persist,
    )

    index_jobs.run_futures_positioning_job(None)

    # Just confirm a trading_day was actually supplied (real ISO date string,
    # not left as None) — the exact "today" value is environment-dependent.
    assert calls["trade_date"]
    assert len(calls["trade_date"]) == 10  # YYYY-MM-DD


@pytest.mark.unit
def test_futures_positioning_job_type_registered():
    assert index_jobs.JOB_TYPE_FUTURES_POSITIONING in index_jobs.INDEX_JOB_TYPES


@pytest.mark.unit
def test_futures_positioning_cron_default_configured():
    from src.config.accessor import get_env_config

    cron = get_env_config().trade.futures_positioning_cron
    assert cron == "30 17 * * 1-5"


@pytest.mark.unit
def test_dispatch_index_job_sync_resolves_futures_positioning(monkeypatch):
    """Smoke check for the scheduler wiring: JOB_TYPE_FUTURES_POSITIONING
    resolves through dispatch_index_job_sync and a real ScheduledResearchJob
    object can be constructed with this job_type, without touching a live
    scheduler process."""
    from src.scheduled_research.models import JobStatus, ScheduledResearchJob

    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.futures_positioning_store.persist_futures_day",
        lambda trade_date: {"status": "ok", "trading_day": trade_date, "rows_written": 1},
    )

    job = ScheduledResearchJob(
        id="test-futures-positioning",
        prompt="test",
        schedule="30 17 * * 1-5",
        next_run_at=0,
        status=JobStatus.PENDING,
        created_at=0,
        timezone="Asia/Kolkata",
        config={"job_type": index_jobs.JOB_TYPE_FUTURES_POSITIONING, "trading_day": "2026-08-28"},
    )

    # Must not raise — this is the exact dispatch path a real cron tick uses.
    index_jobs.dispatch_index_job_sync(job)
    assert job.config.get(index_jobs.LAST_RESULT_CONFIG_KEY, {}).get("status") == "ok"


@pytest.mark.unit
def test_register_default_index_jobs_includes_futures_positioning_with_ist_timezone(tmp_path):
    """register_default_index_jobs must create the new job without raising —
    exercises validate_schedule + the ScheduledResearchJob construction for
    the new entry, same wiring path pump_dump_proxy/max_pain_bhavcopy use."""
    from src.scheduled_research.store import ScheduledResearchJobStore

    store = ScheduledResearchJobStore(tmp_path / "jobs.json")
    index_jobs.register_default_index_jobs(store)

    job = store.get("nifty-futures-positioning")
    assert job is not None
    assert job.config["job_type"] == index_jobs.JOB_TYPE_FUTURES_POSITIONING
    assert job.timezone == "Asia/Kolkata"
