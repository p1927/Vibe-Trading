"""US market hub news ingest jobs — the first non-India market news source
(see .claude/backlog/items/2026-08-22-market-news-impact-prediction-schema.md).

Piggybacks on the existing nifty-hub-news-entity job to drain SPX's staged
refs (news_entity_worker._tickers_with_pending_staging() already discovers
every ticker with pending staging, not just its own config "ticker"), so
there's no dedicated "us-hub-news-entity" job to test here — only the two
ingest jobs that queue SPX-tagged staging refs in the first place.
"""

from __future__ import annotations

import pytest

from src.scheduled_research import index_jobs


@pytest.mark.unit
def test_register_default_index_jobs_includes_us_news_ingest(tmp_path):
    from src.scheduled_research.store import ScheduledResearchJobStore

    store = ScheduledResearchJobStore(tmp_path / "jobs.json")
    index_jobs.register_default_index_jobs(store)

    full = store.get("us-hub-news-ingest-full")
    assert full is not None
    assert full.config["job_type"] == index_jobs.JOB_TYPE_HUB_NEWS_INGEST
    assert full.config["ticker"] == "SPX"
    assert full.config["market"] == "US"
    assert "moneycontrol" not in full.config["sources"]
    assert "searxng_sector" not in full.config["sources"]

    light = store.get("us-hub-news-ingest-light")
    assert light is not None
    assert light.config["ticker"] == "SPX"
    assert light.config["market"] == "US"

    # No dedicated entity job for US — nifty-hub-news-entity drains it too.
    assert store.get("us-hub-news-entity") is None


@pytest.mark.unit
def test_run_hub_news_ingest_job_threads_market_through(monkeypatch):
    captured = {}

    def _fake_run_hub_news_ingest(**kwargs):
        captured.update(kwargs)
        return {"totals": {"queued": 0}}

    monkeypatch.setattr(
        "trade_integrations.dataflows.news_hub_bridge.run_hub_news_ingest",
        _fake_run_hub_news_ingest,
    )

    index_jobs.run_hub_news_ingest_job(
        {"ticker": "SPX", "market": "US", "mode": "full", "sources": "rss,currents"}
    )

    assert captured["ticker"] == "SPX"
    assert captured["market"] == "US"


@pytest.mark.unit
def test_run_hub_news_ingest_job_defaults_market_to_india(monkeypatch):
    captured = {}

    def _fake_run_hub_news_ingest(**kwargs):
        captured.update(kwargs)
        return {"totals": {"queued": 0}}

    monkeypatch.setattr(
        "trade_integrations.dataflows.news_hub_bridge.run_hub_news_ingest",
        _fake_run_hub_news_ingest,
    )

    index_jobs.run_hub_news_ingest_job({"ticker": "NIFTY", "mode": "light"})

    assert captured["market"] == "IN"
