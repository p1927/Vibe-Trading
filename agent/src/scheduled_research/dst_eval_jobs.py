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
    """Shell out to pytest: the ``@given(...)`` Hypothesis tests have no direct callable.

    Uses the root repo's own ``.venv`` (where ``yfinance``/``tradingagents``/pytest all live),
    not ``sys.executable`` — the vibetrading agent server that dispatches this job runs under
    ``vibetrading/.venv``, a separate, narrower environment that doesn't have pytest at all.
    """
    root = trade_repo_root()
    if root is None:
        return {"status": "error", "error": "trade repo root not found", "had_errors": True}
    venv_python = root / ".venv" / "bin" / "python3"
    python_bin = str(venv_python) if venv_python.is_file() else sys.executable
    try:
        proc = subprocess.run(
            [
                python_bin, "-m", "pytest", "tests/test_recorder_dst_lite.py",
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
    from trade_integrations.autonomous_agents.decision_quality_golden_eval import (
        run_decision_quality_golden_eval,
    )
    from trade_integrations.autonomous_agents.outcome_ledger_golden_eval import (
        run_outcome_ledger_golden_eval,
    )

    results: dict[str, Any] = {}
    had_errors = False
    for name, fn in (
        ("intent_extractor", run_intent_extractor_golden_eval),
        ("outcome_ledger", run_outcome_ledger_golden_eval),
        # Per-decision judgement quality — regret against the alternatives the agent
        # itself ranked, plus confidence calibration. Sits alongside outcome_ledger
        # rather than replacing it: that one answers "did it make money", this one
        # answers "did it decide well", and the two can move in opposite directions.
        ("decision_quality", run_decision_quality_golden_eval),
    ):
        try:
            results[name] = fn()
        except Exception as exc:
            logger.exception("autonomous_agents_eval sub-eval %s failed", name)
            results[name] = {"status": "error", "error": str(exc)}
            had_errors = True
    return {"status": "error" if had_errors else "ok", "had_errors": had_errors, "results": results}


def dispatch_dst_eval_job_sync(job: ScheduledResearchJob) -> None:
    """Run one dst-eval job under its own job-scoped cancel flag.

    Without the binding, the dispatch-timeout cancel aimed at this job
    (`staleness._request_pipeline_cancel_on_dispatch_timeout`) was invisible to the golden-eval
    loops' `check_pipeline_cancel()` checkpoints. The timed-out run then kept its LLM slot and
    pool thread for as long as the loop took. Same scope `index_jobs.dispatch_index_job_sync` uses.
    See .claude/backlog/items/2026-09-11-eval-jobs-exceed-dispatch-timeout.md.
    """
    ensure_trade_stack_path()
    from trade_integrations.dataflows.index_research.pipeline_cancel import pipeline_job_scope

    with pipeline_job_scope(job.id):
        _dispatch_dst_eval_job_body(job)


def _dispatch_dst_eval_job_body(job: ScheduledResearchJob) -> None:
    """Route one dst-eval job, attach its summary, and fail the run when the summary says it failed.

    Every runner here catches its own errors and *returns* ``{"status": "error", "had_errors":
    True}``, and this function used to discard that return value. So the executor took its success
    branch and recorded the run ``completed`` with ``failure_kind: None``. Observed on release: all
    three index_research_eval sub-evals raised on 2026-09-08 and the job still read ``completed``.
    The runners stay report-only, so one failing sub-eval never stops the others. Only the job's
    own record changes.
    See .claude/backlog/items/2026-09-11-job-errors-recorded-as-success.md.
    """
    from src.scheduled_research.index_jobs import LAST_RESULT_CONFIG_KEY
    from src.scheduled_research.run_outcome import raise_if_run_had_errors

    job_type = str(job.config.get("job_type") or "")
    runners = {
        JOB_TYPE_RECORDER_DST: run_recorder_dst_job,
        JOB_TYPE_PREDICTION_EVAL: run_prediction_eval_job,
        JOB_TYPE_INDEX_RESEARCH_EVAL: run_index_research_eval_job,
        JOB_TYPE_AUTONOMOUS_AGENTS_EVAL: run_autonomous_agents_eval_job,
    }
    runner = runners.get(job_type)
    if runner is None:
        raise ValueError(f"unsupported dst_eval job_type: {job_type!r}")
    summary = runner(job.config)
    compact = _compact_dst_eval_summary(summary)
    if compact:
        job.config[LAST_RESULT_CONFIG_KEY] = compact
    logger.info("dst_eval %s completed for job %s: %s", job_type, job.id, compact)
    raise_if_run_had_errors(job, summary, f"dst_eval {job_type}")


def _compact_dst_eval_summary(summary: Any) -> dict[str, Any]:
    """Status, error, and each sub-eval's status/error/scored/skipped counts. Small enough to persist."""
    if not isinstance(summary, dict):
        return {}
    compact: dict[str, Any] = {
        k: summary[k]
        for k in ("status", "had_errors", "error", "returncode", "scored_count", "skipped_case_count")
        if k in summary
    }
    results = summary.get("results")
    if isinstance(results, dict):
        compact["results"] = {
            name: {
                k: r[k]
                for k in ("status", "error", "scored_count", "skipped_case_count", "mlflow_run_id")
                if k in r
            }
            for name, r in results.items()
            if isinstance(r, dict)
        }
    return compact


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
