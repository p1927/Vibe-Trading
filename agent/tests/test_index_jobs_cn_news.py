"""China market hub news ingest jobs — the third non-India market news source
(see .claude/backlog/items/2026-08-23-us-news-ingestion-live.md).

Same pattern as test_index_jobs_us_news.py / test_index_jobs_jp_news.py:
piggybacks on nifty-hub-news-entity to drain CSI300's staged refs, so there's
no dedicated "cn-hub-news-entity" job to test — only the two ingest jobs that
queue CSI300-tagged refs.
"""

from __future__ import annotations

import pytest

from src.scheduled_research import index_jobs


@pytest.mark.unit
def test_register_default_index_jobs_includes_cn_news_ingest(tmp_path):
    from src.scheduled_research.store import ScheduledResearchJobStore

    store = ScheduledResearchJobStore(tmp_path / "jobs.json")
    index_jobs.register_default_index_jobs(store)

    full = store.get("cn-hub-news-ingest-full")
    assert full is not None
    assert full.config["job_type"] == index_jobs.JOB_TYPE_HUB_NEWS_INGEST
    assert full.config["ticker"] == "CSI300"
    assert full.config["market"] == "CN"
    # Currents included (unlike JP) — live-tested to return real China business
    # news, unlike JP's empty country="jp" query.
    assert "currents" in full.config["sources"]
    assert "marketaux" not in full.config["sources"]
    assert "moneycontrol" not in full.config["sources"]

    light = store.get("cn-hub-news-ingest-light")
    assert light is not None
    assert light.config["ticker"] == "CSI300"
    assert light.config["market"] == "CN"

    # No dedicated entity job for CN — nifty-hub-news-entity drains it too.
    assert store.get("cn-hub-news-entity") is None


@pytest.mark.unit
def test_run_hub_news_ingest_job_threads_cn_market_through(monkeypatch):
    captured = {}

    def _fake_run_hub_news_ingest(**kwargs):
        captured.update(kwargs)
        return {"totals": {"queued": 0}}

    monkeypatch.setattr(
        "trade_integrations.dataflows.news_hub_bridge.run_hub_news_ingest",
        _fake_run_hub_news_ingest,
    )

    index_jobs.run_hub_news_ingest_job(
        {"ticker": "CSI300", "market": "CN", "mode": "full", "sources": "rss,searxng,currents"}
    )

    assert captured["ticker"] == "CSI300"
    assert captured["market"] == "CN"
