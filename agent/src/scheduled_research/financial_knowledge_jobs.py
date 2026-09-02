"""Scheduled financial-knowledge corpus curator job (Part B of
.claude/backlog/items/2026-09-02-wiki-lifecycle-knowledge-bridge.md).

Own cadence, separate from the news-hub maintainer — books/research don't arrive
daily the way news does. Mirrors ``hub_calibration_jobs.py``'s self-contained
sidecar shape (own job_type, own cron env, own dispatch, own default registration).
"""

from __future__ import annotations

import logging
import time
from typing import Any

from src.config.accessor import get_env_config
from src.scheduled_research.models import JobStatus, ScheduledResearchJob, validate_schedule
from src.scheduled_research.store import ScheduledResearchJobStore
from src.trade.hub_bridge import ensure_trade_stack_path

logger = logging.getLogger(__name__)

FINANCIAL_KNOWLEDGE_ENABLE_SCHEDULER_ENV = "FINANCIAL_KNOWLEDGE_ENABLE_SCHEDULER"
FINANCIAL_KNOWLEDGE_CURATOR_CRON_ENV = "FINANCIAL_KNOWLEDGE_CURATOR_CRON"
DEFAULT_FINANCIAL_KNOWLEDGE_CURATOR_CRON = "0 7 * * *"

JOB_TYPE_FINANCIAL_KNOWLEDGE_CURATOR = "financial_knowledge_curator"

FINANCIAL_KNOWLEDGE_JOB_TYPES = frozenset({JOB_TYPE_FINANCIAL_KNOWLEDGE_CURATOR})

_TRUE_VALUES = {"1", "true", "yes", "on"}


def is_financial_knowledge_scheduler_enabled(value: str | None = None) -> bool:
    if value is not None:
        return value.strip().lower() in _TRUE_VALUES
    explicit = get_env_config().trade.financial_knowledge_enable_scheduler.strip().lower()
    if explicit in _TRUE_VALUES:
        return True
    if explicit in {"0", "false", "no", "off"}:
        return False
    from src.scheduled_research.index_jobs import is_index_scheduler_enabled

    return is_index_scheduler_enabled()


def _ensure_trade_integrations_on_path() -> None:
    ensure_trade_stack_path()


def run_financial_knowledge_curator_job(config: dict[str, Any] | None = None) -> dict[str, Any]:
    _ensure_trade_integrations_on_path()
    from trade_integrations.knowledge_engine.curator import run_financial_knowledge_curation

    cfg = config or {}
    batch_size = int(cfg.get("batch_size") or 15)
    report = run_financial_knowledge_curation(batch_size=batch_size)
    logger.info(
        "financial-knowledge curator: ok=%s skipped=%s",
        report.get("ok"),
        report.get("skipped", False),
    )
    return report


def dispatch_financial_knowledge_job_sync(job: ScheduledResearchJob) -> None:
    job_type = str(job.config.get("job_type") or "")
    if job_type == JOB_TYPE_FINANCIAL_KNOWLEDGE_CURATOR:
        run_financial_knowledge_curator_job(job.config)
        return
    raise ValueError(f"unsupported financial_knowledge job_type: {job_type!r}")


async def dispatch_financial_knowledge_job(job: ScheduledResearchJob) -> None:
    from src.scheduled_research.run_log_buffer import run_logged

    await run_logged(job, dispatch_financial_knowledge_job_sync)


def register_default_financial_knowledge_jobs(store: ScheduledResearchJobStore) -> int:
    if not is_financial_knowledge_scheduler_enabled():
        return 0

    now_ms = int(time.time() * 1000)
    cron = get_env_config().trade.financial_knowledge_curator_cron.strip()
    validate_schedule(cron)
    job_id = "financial-knowledge-curator"
    if store.get(job_id) is not None:
        return 0

    store.upsert(
        ScheduledResearchJob(
            id=job_id,
            prompt="Curate financial-knowledge corpus: source quality, distillation flags, ingest trigger, size/growth",
            schedule=cron,
            next_run_at=now_ms,
            status=JobStatus.PENDING,
            created_at=now_ms,
            config={"job_type": JOB_TYPE_FINANCIAL_KNOWLEDGE_CURATOR, "batch_size": 15},
        )
    )
    logger.info("registered financial-knowledge curator job %s (%s)", job_id, cron)
    return 1
