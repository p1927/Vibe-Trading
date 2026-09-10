"""Hub news-pipeline settings as the one source for India's settings-managed news jobs.

Fork-only sidecar for ``index_jobs.py`` (an upstream file), per docs/FORK_CONVENTIONS.md.

India's four hub-news jobs (``nifty-hub-news-ingest-full``, ``-ingest-light``, ``-entity``,
``-entity-maintenance``) are written by two things: ``register_default_index_jobs``'s code
defaults, and the Hub news-pipeline settings (``news_pipeline_config`` — env defaults merged with
``reports/hub/_data/news_pipeline/config.json``, edited from the Hub UI and applied by
``sync_scheduled_jobs_from_config``). On every boot the sync applied the settings and then the
defaults loop re-created a job the settings had switched off and reconciled the code's schedule
and config back over the settings, so a settings edit lasted only until the next restart
(``.claude/backlog/items/2026-09-11-light-ingest-toggle-undone-on-boot.md``).

This module applies the settings to the code defaults BEFORE the defaults loop runs, so both
writers agree (docs/DECISIONS.md D13: one rule, one source). It touches only the fields the
settings own; timeouts, prompts and every other market's jobs stay as the code defines them.
"""

from __future__ import annotations

import dataclasses
import logging
from typing import Any, Iterable

from src.scheduled_research.models import ScheduledResearchJob, validate_schedule

logger = logging.getLogger(__name__)

FULL_JOB_ID = "nifty-hub-news-ingest-full"
LIGHT_JOB_ID = "nifty-hub-news-ingest-light"
ENTITY_JOB_ID = "nifty-hub-news-entity"
MAINTENANCE_JOB_ID = "nifty-hub-news-entity-maintenance"


def load_pipeline_settings() -> dict[str, Any] | None:
    """The Hub news-pipeline settings, read through the ``news_hub_bridge`` facade.

    ``None`` only when ``trade_integrations`` cannot be imported — the same condition under
    which the boot-time sync is skipped, so the code defaults then stand on their own.
    """
    try:
        from trade_integrations.dataflows.news_hub_bridge import get_pipeline_config
    except ImportError as exc:
        logger.warning("hub news pipeline settings unavailable; using code defaults: %s", exc)
        return None
    return get_pipeline_config()


def _settings_owned_fields(settings: dict[str, Any]) -> dict[str, tuple[str, dict[str, Any]]]:
    """job id -> (schedule, config keys) exactly as ``sync_scheduled_jobs_from_config`` writes them."""
    ticker = settings["ticker"]
    return {
        FULL_JOB_ID: (
            settings["full_ingest_cron"],
            {
                "ticker": ticker,
                "sources": settings["full_ingest_sources"],
                "lookback_days": settings["full_lookback_days"],
            },
        ),
        LIGHT_JOB_ID: (
            settings["light_ingest_cron"],
            {
                "ticker": ticker,
                "sources": settings["light_ingest_sources"],
                "lookback_days": settings["light_lookback_days"],
            },
        ),
        ENTITY_JOB_ID: (
            settings["entity_drain_cron"],
            {"ticker": ticker, "batch_size": settings["entity_batch_size"]},
        ),
        MAINTENANCE_JOB_ID: (
            settings["entity_maintenance_cron"],
            {"ticker": ticker, "batch_size": settings["entity_batch_size"]},
        ),
    }


def apply_pipeline_settings(
    defaults: Iterable[ScheduledResearchJob], settings: dict[str, Any] | None
) -> list[ScheduledResearchJob]:
    """Return *defaults* with the settings-owned fields of India's news jobs taken from *settings*.

    Raises ``ValueError`` (from ``validate_schedule``) when a settings cron is malformed, before
    anything is changed.
    """
    if settings is None:
        return list(defaults)
    owned = _settings_owned_fields(settings)
    for schedule, _ in owned.values():
        validate_schedule(schedule)
    out: list[ScheduledResearchJob] = []
    for job in defaults:
        fields = owned.get(job.id)
        if fields is None:
            out.append(job)
            continue
        schedule, overrides = fields
        out.append(dataclasses.replace(job, schedule=schedule, config={**job.config, **overrides}))
    return out

