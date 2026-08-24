"""Japan market hub news ingest jobs — the second non-India market news source
(see .claude/backlog/items/2026-08-23-us-news-ingestion-live.md).

Same pattern as test_index_jobs_us_news.py: piggybacks on nifty-hub-news-entity
to drain NIKKEI225's staged refs, so there's no dedicated "jp-hub-news-entity"
job to test — only the two ingest jobs that queue NIKKEI225-tagged refs.
"""

from __future__ import annotations

import pytest

from src.scheduled_research import index_jobs


@pytest.mark.unit
def test_register_default_index_jobs_includes_jp_news_ingest(tmp_path):
    from src.scheduled_research.store import ScheduledResearchJobStore

    store = ScheduledResearchJobStore(tmp_path / "jobs.json")
    index_jobs.register_default_index_jobs(store)

    full = store.get("jp-hub-news-ingest-full")
    assert full is not None
    assert full.config["job_type"] == index_jobs.JOB_TYPE_HUB_NEWS_INGEST
    assert full.config["ticker"] == "NIKKEI225"
    assert full.config["market"] == "JP"
    # No currents/marketaux — Currents' country="jp" is empty, live-tested 2026-08-25.
    assert "currents" not in full.config["sources"]
    assert "marketaux" not in full.config["sources"]
    assert "moneycontrol" not in full.config["sources"]

    light = store.get("jp-hub-news-ingest-light")
    assert light is not None
    assert light.config["ticker"] == "NIKKEI225"
    assert light.config["market"] == "JP"

    # No dedicated entity job for JP — nifty-hub-news-entity drains it too.
    assert store.get("jp-hub-news-entity") is None


@pytest.mark.unit
def test_run_hub_news_ingest_job_threads_jp_market_through(monkeypatch):
    captured = {}

    def _fake_run_hub_news_ingest(**kwargs):
        captured.update(kwargs)
        return {"totals": {"queued": 0}}

    monkeypatch.setattr(
        "trade_integrations.dataflows.news_hub_bridge.run_hub_news_ingest",
        _fake_run_hub_news_ingest,
    )

    index_jobs.run_hub_news_ingest_job(
        {"ticker": "NIKKEI225", "market": "JP", "mode": "full", "sources": "rss,searxng"}
    )

    assert captured["ticker"] == "NIKKEI225"
    assert captured["market"] == "JP"
