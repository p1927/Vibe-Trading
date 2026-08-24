"""Russia market hub news ingest jobs — the fourth non-India market news source
(see .claude/backlog/items/2026-08-23-us-news-ingestion-live.md).

Unlike US/JP/CN, Russia's SearXNG results were near-total junk (Wikipedia
country pages, chicken-soup recipes, Fortnite downloads) regardless of query
phrasing, so this market skips searxng entirely and instead relies on
Currents' keyword search (currents_keywords) since Currents' plain country="ru"
query is empty, same as JP.
"""

from __future__ import annotations

import pytest

from src.scheduled_research import index_jobs


@pytest.mark.unit
def test_register_default_index_jobs_includes_ru_news_ingest(tmp_path):
    from src.scheduled_research.store import ScheduledResearchJobStore

    store = ScheduledResearchJobStore(tmp_path / "jobs.json")
    index_jobs.register_default_index_jobs(store)

    full = store.get("ru-hub-news-ingest-full")
    assert full is not None
    assert full.config["job_type"] == index_jobs.JOB_TYPE_HUB_NEWS_INGEST
    assert full.config["ticker"] == "MOEX"
    assert full.config["market"] == "RU"
    assert "currents" in full.config["sources"]
    assert "searxng" not in full.config["sources"]
    assert full.config["currents_keywords"]

    light = store.get("ru-hub-news-ingest-light")
    assert light is not None
    assert light.config["ticker"] == "MOEX"
    assert light.config["market"] == "RU"

    # No dedicated entity job for RU — nifty-hub-news-entity drains it too.
    assert store.get("ru-hub-news-entity") is None


@pytest.mark.unit
def test_run_hub_news_ingest_job_threads_ru_market_and_keywords_through(monkeypatch):
    captured = {}

    def _fake_run_hub_news_ingest(**kwargs):
        captured.update(kwargs)
        return {"totals": {"queued": 0}}

    monkeypatch.setattr(
        "trade_integrations.dataflows.news_hub_bridge.run_hub_news_ingest",
        _fake_run_hub_news_ingest,
    )

    index_jobs.run_hub_news_ingest_job(
        {
            "ticker": "MOEX",
            "market": "RU",
            "mode": "full",
            "sources": "rss,currents",
            "currents_keywords": ["MOEX", "Russia", "stock"],
        }
    )

    assert captured["ticker"] == "MOEX"
    assert captured["market"] == "RU"
    assert captured["currents_keywords"] == ["MOEX", "Russia", "stock"]


@pytest.mark.unit
def test_run_hub_news_ingest_job_defaults_currents_keywords_to_none(monkeypatch):
    captured = {}

    def _fake_run_hub_news_ingest(**kwargs):
        captured.update(kwargs)
        return {"totals": {"queued": 0}}

    monkeypatch.setattr(
        "trade_integrations.dataflows.news_hub_bridge.run_hub_news_ingest",
        _fake_run_hub_news_ingest,
    )

    index_jobs.run_hub_news_ingest_job({"ticker": "SPX", "market": "US", "mode": "full"})

    assert captured["currents_keywords"] is None
