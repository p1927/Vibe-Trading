"""reinference_tick scheduled job (module 3 step 5 of the options-profitability
prediction platform — event/heartbeat-triggered fusion-forecast re-inference)."""
from __future__ import annotations

import pytest

from src.scheduled_research import index_jobs


@pytest.mark.unit
def test_run_reinference_tick_job_delegates_with_config(monkeypatch):
    calls = {}

    def _fake_tick(*, ticker, price_materiality_pct, news_materiality_threshold, heartbeat_minutes):
        calls["ticker"] = ticker
        calls["price_materiality_pct"] = price_materiality_pct
        calls["news_materiality_threshold"] = news_materiality_threshold
        calls["heartbeat_minutes"] = heartbeat_minutes
        return {"status": "ok", "triggered": True}

    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.prediction_algorithms.reinference_trigger.run_reinference_tick",
        _fake_tick,
    )

    result = index_jobs.run_reinference_tick_job(
        {
            "ticker": "NIFTY",
            "price_materiality_pct": 0.75,
            "news_materiality_threshold": 4.0,
            "heartbeat_minutes": 30,
        }
    )

    assert calls["ticker"] == "NIFTY"
    assert calls["price_materiality_pct"] == 0.75
    assert calls["news_materiality_threshold"] == 4.0
    assert calls["heartbeat_minutes"] == 30
    assert result["status"] == "ok"


@pytest.mark.unit
def test_run_reinference_tick_job_defaults_config(monkeypatch):
    calls = {}

    def _fake_tick(*, ticker, price_materiality_pct, news_materiality_threshold, heartbeat_minutes):
        calls.update(
            ticker=ticker,
            price_materiality_pct=price_materiality_pct,
            news_materiality_threshold=news_materiality_threshold,
            heartbeat_minutes=heartbeat_minutes,
        )
        return {"status": "ok", "triggered": False}

    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.prediction_algorithms.reinference_trigger.run_reinference_tick",
        _fake_tick,
    )

    index_jobs.run_reinference_tick_job(None)

    assert calls["ticker"] == "NIFTY"
    assert calls["price_materiality_pct"] == 0.5
    assert calls["news_materiality_threshold"] == 3.0
    assert calls["heartbeat_minutes"] == 60.0


@pytest.mark.unit
def test_reinference_tick_job_type_registered():
    assert index_jobs.JOB_TYPE_REINFERENCE_TICK in index_jobs.INDEX_JOB_TYPES
