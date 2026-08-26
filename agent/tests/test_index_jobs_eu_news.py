"""Europe hub news ingest jobs — the eighth and last registry market, filed as
[[2026-08-27-eu-news-pipeline-gap]] and closed same day (see .claude/backlog/items/
2026-08-22-market-news-impact-prediction-schema.md's 2026-08-27 CN/JP/ME/LATAM entry for the
pattern this repeats).

Skips searxng entirely, same reasoning as every other non-searxng market: not live-tested this
pass since a clean rss + Currents combo was already found. Currents' plain country="de" query
was near-empty/off-topic (2 articles, one unrelated); keywords=("DAX", "stocks") live-tested
2026-08-27 returned 10/10 clean, real European-market articles.
"""

from __future__ import annotations

import pytest

from src.scheduled_research import index_jobs


@pytest.mark.unit
def test_register_default_index_jobs_includes_eu_news_ingest(tmp_path):
    from src.scheduled_research.store import ScheduledResearchJobStore

    store = ScheduledResearchJobStore(tmp_path / "jobs.json")
    index_jobs.register_default_index_jobs(store)

    full = store.get("eu-hub-news-ingest-full")
    assert full is not None
    assert full.config["job_type"] == index_jobs.JOB_TYPE_HUB_NEWS_INGEST
    assert full.config["ticker"] == "EURO_STOXX_50"
    assert full.config["market"] == "EU"
    assert "searxng" not in full.config["sources"]
    assert full.config["currents_keywords"] == ["DAX", "stocks"]

    light = store.get("eu-hub-news-ingest-light")
    assert light is not None
    assert light.config["ticker"] == "EURO_STOXX_50"
    assert light.config["market"] == "EU"

    assert store.get("eu-hub-news-entity") is None


@pytest.mark.unit
def test_run_hub_news_ingest_job_threads_eu_market_and_keywords_through(monkeypatch):
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
            "ticker": "EURO_STOXX_50",
            "market": "EU",
            "mode": "full",
            "sources": "rss,currents",
            "currents_keywords": ["DAX", "stocks"],
        }
    )

    assert captured["ticker"] == "EURO_STOXX_50"
    assert captured["market"] == "EU"
    assert captured["currents_keywords"] == ["DAX", "stocks"]
