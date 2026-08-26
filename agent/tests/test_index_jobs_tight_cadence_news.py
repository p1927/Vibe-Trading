"""Tight-cadence per-market news ingest jobs.

See .claude/backlog/items/2026-08-27-market-news-timeline-recording.md — design decision:
one new "-hub-news-ingest-tight" job per market, derived from that market's existing
"-hub-news-ingest-light" job (same config, RSS-only by default via hub_news_light_sources),
just a much shorter interval, so a recorded/replayed session has denser news alongside
denser price ticks.
"""

from __future__ import annotations

import pytest

from src.scheduled_research import index_jobs

_LIGHT_MARKET_PREFIXES = ("nifty", "us", "jp", "cn", "ru", "me", "latam", "eu")


@pytest.mark.unit
def test_register_default_index_jobs_creates_tight_variant_per_market(tmp_path):
    from src.scheduled_research.store import ScheduledResearchJobStore

    store = ScheduledResearchJobStore(tmp_path / "jobs.json")
    index_jobs.register_default_index_jobs(store)

    for prefix in _LIGHT_MARKET_PREFIXES:
        light = store.get(f"{prefix}-hub-news-ingest-light")
        tight = store.get(f"{prefix}-hub-news-ingest-tight")
        assert light is not None
        assert tight is not None
        assert tight.config == light.config
        assert tight.config["job_type"] == index_jobs.JOB_TYPE_HUB_NEWS_INGEST
        assert tight.schedule != light.schedule
        assert "Tight-cadence" in tight.prompt


@pytest.mark.unit
def test_tight_cadence_cron_env_var_overrides_default(tmp_path, monkeypatch):
    from src.scheduled_research.store import ScheduledResearchJobStore

    monkeypatch.setenv("HUB_NEWS_TIGHT_INGEST_CRON", "*/2 * * * *")
    store = ScheduledResearchJobStore(tmp_path / "jobs.json")
    index_jobs.register_default_index_jobs(store)

    tight = store.get("us-hub-news-ingest-tight")
    assert tight is not None
    assert tight.schedule == "*/2 * * * *"


@pytest.mark.unit
def test_tight_job_config_is_independent_copy_not_aliased(tmp_path):
    """Mutating one job's config dict must never leak into the other's."""
    from src.scheduled_research.store import ScheduledResearchJobStore

    store = ScheduledResearchJobStore(tmp_path / "jobs.json")
    index_jobs.register_default_index_jobs(store)

    light = store.get("us-hub-news-ingest-light")
    tight = store.get("us-hub-news-ingest-tight")
    light.config["lookback_days"] = 999
    assert tight.config["lookback_days"] != 999
