"""pump_dump_proxy scheduled job (module 2 step 3 of the options-profitability
prediction platform — forward-only pump-and-dump-proxy accumulation)."""
from __future__ import annotations

import pytest

from src.scheduled_research import index_jobs


@pytest.mark.unit
def test_run_pump_dump_proxy_job_delegates_to_capture_and_append(monkeypatch):
    calls = {}

    def _fake_capture(*, symbol, exchange):
        calls["symbol"] = symbol
        calls["exchange"] = exchange
        return {"status": "ok", "rows_added": 1, "pump_dump_proxy_score": 0.42}

    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.volume_concentration.capture_and_append_pump_dump_snapshot",
        _fake_capture,
    )

    result = index_jobs.run_pump_dump_proxy_job({"symbol": "NIFTY", "exchange": "NSE_INDEX"})

    assert calls["symbol"] == "NIFTY"
    assert calls["exchange"] == "NSE_INDEX"
    assert result["status"] == "ok"
    assert result["pump_dump_proxy_score"] == 0.42


@pytest.mark.unit
def test_run_pump_dump_proxy_job_defaults_config(monkeypatch):
    calls = {}

    def _fake_capture(*, symbol, exchange):
        calls["symbol"] = symbol
        calls["exchange"] = exchange
        return {"status": "no_data", "rows_added": 1}

    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.volume_concentration.capture_and_append_pump_dump_snapshot",
        _fake_capture,
    )

    index_jobs.run_pump_dump_proxy_job(None)

    assert calls["symbol"] == "NIFTY"
    assert calls["exchange"] == "NSE_INDEX"


@pytest.mark.unit
def test_pump_dump_proxy_job_type_registered():
    assert index_jobs.JOB_TYPE_PUMP_DUMP_PROXY in index_jobs.INDEX_JOB_TYPES


@pytest.mark.unit
def test_pump_dump_proxy_cron_default_configured():
    from src.config.accessor import get_env_config

    cron = get_env_config().trade.pump_dump_proxy_cron
    assert cron == "50 15 * * 1-5"
