"""max_pain_bhavcopy scheduled job (module 2 step 4's retroactive complement —
NSE bhavcopy-based max-pain reconstruction, now the primary forward-going
source since it needs no broker session)."""
from __future__ import annotations

import pytest

from src.scheduled_research import index_jobs


@pytest.mark.unit
def test_run_max_pain_bhavcopy_job_delegates_to_refresh(monkeypatch):
    """The job runs `refresh_max_pain_history`, which also retries recently missed sessions."""
    calls = {}

    def _fake_refresh(trading_day, *, symbol):
        calls["trading_day"] = trading_day
        calls["symbol"] = symbol
        return {"status": "ok", "days_ok": 1}

    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.oi_bhavcopy_history.refresh_max_pain_history",
        _fake_refresh,
    )

    result = index_jobs.run_max_pain_bhavcopy_job({"symbol": "NIFTY", "trading_day": "2026-08-24"})

    assert calls == {"trading_day": "2026-08-24", "symbol": "NIFTY"}
    assert result["status"] == "ok"


@pytest.mark.unit
def test_run_max_pain_bhavcopy_job_defaults_trading_day_to_today_ist(monkeypatch):
    calls = {}

    def _fake_refresh(trading_day, *, symbol):
        calls["trading_day"] = trading_day
        return {"status": "ok"}

    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.oi_bhavcopy_history.refresh_max_pain_history",
        _fake_refresh,
    )

    index_jobs.run_max_pain_bhavcopy_job(None)

    # The exact date depends on when the test runs: just a well-formed ISO date.
    assert len(calls["trading_day"]) == 10


@pytest.mark.unit
def test_max_pain_bhavcopy_job_type_registered():
    assert index_jobs.JOB_TYPE_MAX_PAIN_BHAVCOPY in index_jobs.INDEX_JOB_TYPES


@pytest.mark.unit
def test_max_pain_bhavcopy_cron_default_configured():
    from src.config.accessor import get_env_config

    cron = get_env_config().trade.max_pain_bhavcopy_cron
    assert cron == "30 17 * * 1-5"


@pytest.mark.unit
def test_register_default_index_jobs_includes_max_pain_bhavcopy_with_ist_timezone(tmp_path):
    from src.scheduled_research.store import ScheduledResearchJobStore

    store = ScheduledResearchJobStore(tmp_path / "jobs.json")
    index_jobs.register_default_index_jobs(store)

    job = store.get("nifty-max-pain-bhavcopy")
    assert job is not None
    assert job.config["job_type"] == index_jobs.JOB_TYPE_MAX_PAIN_BHAVCOPY
    # Unlike nifty-oi-snapshot/nifty-pump-dump-proxy (no timezone set, so
    # their cron strings are evaluated in UTC despite "just after close"
    # IST framing in their docstrings), this job sets timezone explicitly
    # so its 17:30 cron value actually means 17:30 IST.
    assert job.timezone == "Asia/Kolkata"
