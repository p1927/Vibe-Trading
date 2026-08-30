"""Scheduled local runs of the DST-lite/MLflow-eval tiers that never got CI wiring.

Fills the gap described in
``.claude/backlog/items/2026-08-27-dst-eval-nightly-ci.md``: ``recorder_dst``,
``prediction_eval``, ``index_research_eval`` and ``autonomous_agents_eval`` only ever ran when a
human typed the pytest marker by hand. GitHub Actions has no provider API key configured for the
eval tiers, but this machine's own ``.env`` already does — so rather than wait on repo-owner
secrets, these run locally through the same scheduler as ``hub_calibration_jobs.py``, which also
gives them a "Trigger run" button in the Scheduled UI for free.

``news_eval`` is not here: it already has a scheduled job
(``index_jobs.py``'s ``JOB_TYPE_NEWS_QUALITY_EVAL``, calling
``news_hub_bridge.run_news_golden_eval`` directly). ``recorder_dst``'s Hypothesis property tests
have no non-pytest callable, so that one job shells out to pytest; the eval tiers call their
golden-eval functions directly, matching ``run_news_quality_eval_job``'s pattern.

Non-blocking throughout: every job function catches its own errors and returns a summary dict
instead of raising, matching each tier's own "report-only" docstring intent.
"""

from __future__ import annotations

import logging
import subprocess
import sys
import time
from typing import Any

from src.config.accessor import get_env_config
from src.scheduled_research.models import JobStatus, ScheduledResearchJob, validate_schedule
from src.scheduled_research.store import ScheduledResearchJobStore
from src.trade.hub_bridge import ensure_trade_stack_path, trade_repo_root

logger = logging.getLogger(__name__)

DST_EVAL_ENABLE_SCHEDULER_ENV = "DST_EVAL_ENABLE_SCHEDULER"

JOB_TYPE_RECORDER_DST = "recorder_dst"
JOB_TYPE_PREDICTION_EVAL = "prediction_eval"
JOB_TYPE_INDEX_RESEARCH_EVAL = "index_research_eval"
JOB_TYPE_AUTONOMOUS_AGENTS_EVAL = "autonomous_agents_eval"

DST_EVAL_JOB_TYPES = frozenset({
    JOB_TYPE_RECORDER_DST,
    JOB_TYPE_PREDICTION_EVAL,
    JOB_TYPE_INDEX_RESEARCH_EVAL,
    JOB_TYPE_AUTONOMOUS_AGENTS_EVAL,
})

_TRUE_VALUES = {"1", "true", "yes", "on"}


def is_dst_eval_scheduler_enabled(value: str | None = None) -> bool:
    if value is not None:
        return value.strip().lower() in _TRUE_VALUES
    raw = get_env_config().trade.dst_eval_enable_scheduler.strip().lower()
    return raw in _TRUE_VALUES


def run_recorder_dst_job(config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Shell out to pytest: the ``@given(...)`` Hypothesis tests have no direct callable."""
    root = trade_repo_root()
    if root is None:
        return {"status": "error", "error": "trade repo root not found", "had_errors": True}
    try:
        proc = subprocess.run(
            [
                sys.executable, "-m", "pytest", "tests/test_recorder_dst_lite.py",
                "-m", "recorder_dst", "-q", "--timeout=120",
            ],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=300,
        )
    except Exception as exc:
        logger.exception("recorder_dst run failed to launch")
        return {"status": "error", "error": str(exc), "had_errors": True}
    had_errors = proc.returncode != 0
    summary: dict[str, Any] = {
        "status": "error" if had_errors else "ok",
        "returncode": proc.returncode,
        "stdout_tail": proc.stdout[-4000:],
        "had_errors": had_errors,
    }
    if had_errors:
        summary["stderr_tail"] = proc.stderr[-2000:]
    logger.info("recorder_dst run: returncode=%s", proc.returncode)
    return summary


def run_prediction_eval_job(config: dict[str, Any] | None = None) -> dict[str, Any]:
    ensure_trade_stack_path()
    from trade_integrations.dataflows.index_research.prediction_algorithms.evaluator.golden_backtest_eval import (
        run_golden_backtest_eval,
    )

    try:
        return run_golden_backtest_eval(ticker="NIFTY", days=150, min_train_rows=40, eval_step=5)
    except Exception as exc:
        logger.exception("prediction_eval golden eval failed")
        return {"status": "error", "error": str(exc), "had_errors": True}


def run_index_research_eval_job(config: dict[str, Any] | None = None) -> dict[str, Any]:
    ensure_trade_stack_path()
    from trade_integrations.dataflows.index_research.external_predictions.extractor_golden_eval import (
        run_extractor_golden_eval,
    )
    from trade_integrations.dataflows.index_research.prediction_ledger_golden_eval import (
        run_prediction_ledger_golden_eval,
    )
    from trade_integrations.dataflows.index_research.external_predictions.financial_expert_agent_golden_eval import (
        run_financial_expert_agent_golden_eval,
    )

    results: dict[str, Any] = {}
    had_errors = False
    for name, fn in (
        ("extractor", run_extractor_golden_eval),
        ("prediction_ledger", run_prediction_ledger_golden_eval),
        ("financial_expert_agent", run_financial_expert_agent_golden_eval),
    ):
        try:
            results[name] = fn()
        except Exception as exc:
            logger.exception("index_research_eval sub-eval %s failed", name)
            results[name] = {"status": "error", "error": str(exc)}
            had_errors = True
    return {"status": "error" if had_errors else "ok", "had_errors": had_errors, "results": results}


def run_autonomous_agents_eval_job(config: dict[str, Any] | None = None) -> dict[str, Any]:
    ensure_trade_stack_path()
    from trade_integrations.autonomous_agents.intent_extractor_golden_eval import (
        run_intent_extractor_golden_eval,
    )
    from trade_integrations.autonomous_agents.outcome_ledger_golden_eval import (
        run_outcome_ledger_golden_eval,
    )

    results: dict[str, Any] = {}
    had_errors = False
    for name, fn in (
        ("intent_extractor", run_intent_extractor_golden_eval),
        ("outcome_ledger", run_outcome_ledger_golden_eval),
    ):
        try:
            results[name] = fn()
        except Exception as exc:
            logger.exception("autonomous_agents_eval sub-eval %s failed", name)
            results[name] = {"status": "error", "error": str(exc)}
            had_errors = True
    return {"status": "error" if had_errors else "ok", "had_errors": had_errors, "results": results}


def dispatch_dst_eval_job_sync(job: ScheduledResearchJob) -> None:
    job_type = str(job.config.get("job_type") or "")
    if job_type == JOB_TYPE_RECORDER_DST:
        run_recorder_dst_job(job.config)
        return
    if job_type == JOB_TYPE_PREDICTION_EVAL:
        run_prediction_eval_job(job.config)
        return
    if job_type == JOB_TYPE_INDEX_RESEARCH_EVAL:
        run_index_research_eval_job(job.config)
        return
    if job_type == JOB_TYPE_AUTONOMOUS_AGENTS_EVAL:
        run_autonomous_agents_eval_job(job.config)
        return
    raise ValueError(f"unsupported dst_eval job_type: {job_type!r}")


async def dispatch_dst_eval_job(job: ScheduledResearchJob) -> None:
    from src.scheduled_research.run_log_buffer import run_logged

    await run_logged(job, dispatch_dst_eval_job_sync)


def register_default_dst_eval_jobs(store: ScheduledResearchJobStore) -> int:
    if not is_dst_eval_scheduler_enabled():
        return 0

    created = 0
    now_ms = int(time.time() * 1000)
    cfg = get_env_config().trade

    jobs = (
        ("dst-eval-recorder-dst", "Local recorder_dst DST-lite suite", cfg.recorder_dst_cron, JOB_TYPE_RECORDER_DST),
        ("dst-eval-prediction", "Local prediction_eval golden-dataset run", cfg.prediction_eval_cron, JOB_TYPE_PREDICTION_EVAL),
        ("dst-eval-index-research", "Local index_research_eval golden-dataset run", cfg.index_research_eval_cron, JOB_TYPE_INDEX_RESEARCH_EVAL),
        ("dst-eval-autonomous-agents", "Local autonomous_agents_eval golden-dataset run", cfg.autonomous_agents_eval_cron, JOB_TYPE_AUTONOMOUS_AGENTS_EVAL),
    )
    for job_id, prompt, cron, job_type in jobs:
        cron = cron.strip()
        validate_schedule(cron)
        if store.get(job_id) is None:
            store.upsert(
                ScheduledResearchJob(
                    id=job_id,
                    prompt=prompt,
                    schedule=cron,
                    next_run_at=now_ms,
                    status=JobStatus.PENDING,
                    created_at=now_ms,
                    config={"job_type": job_type},
                )
            )
            logger.info("registered dst_eval job %s (%s)", job_id, cron)
            created += 1

    return created
