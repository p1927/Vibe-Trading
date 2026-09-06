"""Middle East and Latin America hub news ingest jobs — the fifth and sixth
non-India market news sources (see .claude/backlog/items/2026-08-23-us-news-
ingestion-live.md).

Both markets skip searxng entirely (near-total junk live-tested 2026-08-25 —
Stack Overflow / Burger King noise for ME, Reddit content including
NSFW-adjacent results for LATAM) and rely on rss + a single precise Currents
keyword instead of the bare ticker (TASI/DFM/ADX/IBOVESPA are either too
ambiguous alone or, combined with generic words, dilute into noise).
"""

from __future__ import annotations

import pytest

from src.scheduled_research import index_jobs


@pytest.mark.unit
def test_register_default_index_jobs_includes_me_news_ingest(tmp_path):
    from src.scheduled_research.store import ScheduledResearchJobStore

    store = ScheduledResearchJobStore(tmp_path / "jobs.json")
    index_jobs.register_default_index_jobs(store)

    full = store.get("me-hub-news-ingest-full")
    assert full is not None
    assert full.config["job_type"] == index_jobs.JOB_TYPE_HUB_NEWS_INGEST
    assert full.config["ticker"] == "TASI"
    assert full.config["market"] == "ME"
    assert "searxng" not in full.config["sources"]
    assert full.config["currents_keywords"] == ["Tadawul"]

    light = store.get("me-hub-news-ingest-light")
    assert light is not None
    assert light.config["ticker"] == "TASI"
    assert light.config["market"] == "ME"

    assert store.get("me-hub-news-entity") is None


@pytest.mark.unit
def test_register_default_index_jobs_includes_latam_news_ingest(tmp_path):
    from src.scheduled_research.store import ScheduledResearchJobStore

    store = ScheduledResearchJobStore(tmp_path / "jobs.json")
    index_jobs.register_default_index_jobs(store)

    full = store.get("latam-hub-news-ingest-full")
    assert full is not None
    assert full.config["job_type"] == index_jobs.JOB_TYPE_HUB_NEWS_INGEST
    assert full.config["ticker"] == "IBOVESPA"
    assert full.config["market"] == "LATAM"
    assert "searxng" not in full.config["sources"]
    assert full.config["currents_keywords"] == ["IBOVESPA"]

    light = store.get("latam-hub-news-ingest-light")
    assert light is not None
    assert light.config["ticker"] == "IBOVESPA"
    assert light.config["market"] == "LATAM"

    assert store.get("latam-hub-news-entity") is None


@pytest.mark.unit
def test_run_hub_news_ingest_job_threads_me_market_and_keywords_through(monkeypatch):
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
            "ticker": "TASI",
            "market": "ME",
            "mode": "full",
            "sources": "rss,currents",
            "currents_keywords": ["Tadawul"],
        }
    )

    assert captured["ticker"] == "TASI"
    assert captured["market"] == "ME"
    assert captured["currents_keywords"] == ["Tadawul"]


@pytest.mark.unit
def test_run_hub_news_ingest_job_threads_latam_market_and_keywords_through(monkeypatch):
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
            "ticker": "IBOVESPA",
            "market": "LATAM",
            "mode": "full",
            "sources": "rss,currents",
            "currents_keywords": ["IBOVESPA"],
        }
    )

    assert captured["ticker"] == "IBOVESPA"
    assert captured["market"] == "LATAM"
    assert captured["currents_keywords"] == ["IBOVESPA"]
