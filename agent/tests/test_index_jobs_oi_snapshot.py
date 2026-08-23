"""oi_snapshot scheduled job (module 2 step 4 of the options-profitability
prediction platform — forward-only OI/max-pain accumulation)."""
from __future__ import annotations

import pytest

from src.scheduled_research import index_jobs


@pytest.mark.unit
def test_run_oi_snapshot_job_delegates_to_capture_and_append(monkeypatch):
    calls = {}

    def _fake_capture(*, underlying, exchange, expiry_date):
        calls["underlying"] = underlying
        calls["exchange"] = exchange
        calls["expiry_date"] = expiry_date
        return {"status": "ok", "rows_added": 1, "max_pain_distance": 42.0}

    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.oi_snapshot_store.capture_and_append_oi_snapshot",
        _fake_capture,
    )

    result = index_jobs.run_oi_snapshot_job({"underlying": "NIFTY", "exchange": "NSE_INDEX"})

    assert calls["underlying"] == "NIFTY"
    assert calls["exchange"] == "NSE_INDEX"
    assert calls["expiry_date"] is None
    assert result["status"] == "ok"
    assert result["max_pain_distance"] == 42.0


@pytest.mark.unit
def test_run_oi_snapshot_job_defaults_config(monkeypatch):
    calls = {}

    def _fake_capture(*, underlying, exchange, expiry_date):
        calls["underlying"] = underlying
        calls["exchange"] = exchange
        return {"status": "ok", "rows_added": 0}

    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.oi_snapshot_store.capture_and_append_oi_snapshot",
        _fake_capture,
    )

    index_jobs.run_oi_snapshot_job(None)

    assert calls["underlying"] == "NIFTY"
    assert calls["exchange"] == "NSE_INDEX"


@pytest.mark.unit
def test_oi_snapshot_job_type_registered():
    assert index_jobs.JOB_TYPE_OI_SNAPSHOT in index_jobs.INDEX_JOB_TYPES
