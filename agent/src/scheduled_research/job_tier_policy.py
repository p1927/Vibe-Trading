"""Which scheduled-research job types are "data collection" (release-exclusive) vs.
operational/session-scoped (stays per-tier) vs. QA/eval (fine anywhere).

Part of the release-as-sole-data-collector design (see
.claude/backlog/items/2026-09-02-vibe-trading-home-scope-audit.md): `trade release` is meant to
be the sole active collector for the same category of work stock_simulator's live-vendor capture
loops already exclusively do (see
trade_integrations/stock_simulator/service/app.py's `_live_capture_enabled()`), but the
scheduled-research executor had no equivalent gate at all — both `trade dev`'s and `trade
release`'s vibetrading-agent processes could independently dispatch the same hub-news-ingest /
index-calibration / etc. jobs, duplicating real LLM/external-API cost for zero benefit, exactly
the class of waste the stock-simulator fix already closed for capture loops.

This is a defense-in-depth structural gate, not the only protection: as of the same pass that
added this, dev's and release's scheduled-job stores were also given separate `VIBE_TRADING_HOME`
roots (no longer one shared file), and the ~26 collection-type jobs in dev's store were paused
directly. This gate exists so a job that gets unpaused in dev later (by mistake, or because
someone didn't know the classification) still can't actually dispatch there.

Classification is deliberately conservative: only genuine external-vendor/LLM data-collection and
archival work is gated. QA/eval job types (`recorder_dst`, `prediction_eval`,
`index_research_eval`, `autonomous_agents_eval`, `news_quality_eval`,
`news_dedup_quality_eval`) are NOT gated — they evaluate already-collected data or exercise dev's
own code changes, which is exactly the kind of thing a developer needs to run in dev, not
something release should monopolize. Genuinely operational/session-scoped types (the
`autonomous_agent_*` family, `recording_wake`, `options_position_monitor`,
`trade_fills_export`) are also not gated — they were never collection work in the first place,
same boundary as OpenAlgo.
"""

from __future__ import annotations

from src.scheduled_research.factor_health_jobs import (
    JOB_TYPE_FACTOR_HEALTH,
    JOB_TYPE_FACTOR_HEALTH_LIVE,
)
from src.scheduled_research.capture_jobs import (
    JOB_TYPE_HUB_CAPTURE_FACTOR_SNAPSHOT,
    JOB_TYPE_HUB_CAPTURE_INTRADAY,
)
from src.scheduled_research.financial_knowledge_jobs import (
    JOB_TYPE_FINANCIAL_KNOWLEDGE_CURATOR,
)
from src.scheduled_research.hub_calibration_jobs import (
    JOB_TYPE_HUB_EVENING_MAINTENANCE,
    JOB_TYPE_HUB_MORNING_CALIBRATION,
)
from src.scheduled_research.index_jobs import (
    JOB_TYPE_COMPANY_RESEARCH_ARCHIVE,
    JOB_TYPE_CONSTITUENT_VOLUME_SNAPSHOT,
    JOB_TYPE_FORECAST_PLATFORM_RETRAIN,
    JOB_TYPE_FUTURES_POSITIONING,
    JOB_TYPE_GLOBAL_MACRO_EOD_REFRESH,
    JOB_TYPE_HUB_NEWS_ENTITY,
    JOB_TYPE_HUB_NEWS_INGEST,
    JOB_TYPE_INDEX_CALIBRATION,
    JOB_TYPE_INDEX_FACTOR_SNAPSHOT,
    JOB_TYPE_INDEX_PLAN_REFRESH,
    JOB_TYPE_INDEX_PREDICTION_POST_CLOSE,
    JOB_TYPE_INDEX_RESEARCH,
    JOB_TYPE_MAX_PAIN_BHAVCOPY,
    JOB_TYPE_OI_SNAPSHOT,
    JOB_TYPE_PUMP_DUMP_PROXY,
    JOB_TYPE_QUANTILE_FORECAST_LEDGER_PUSH,
    JOB_TYPE_REINFERENCE_TICK,
    JOB_TYPE_STOCK_HISTORY_COVERAGE_SWEEP,
)
from src.scheduled_research.options_jobs import (
    JOB_TYPE_OPTIONS_PLAN_REFRESH,
    JOB_TYPE_OPTIONS_POSITION_MONITOR,
)
from src.scheduled_research.recording_wake_jobs import JOB_TYPE_RECORDING_WAKE
from src.scheduled_research.autonomous_agent_jobs import AUTONOMOUS_JOB_TYPES
from src.scheduled_research.trade_data_jobs import (
    JOB_TYPE_NSE_MACRO_REFRESH,
    JOB_TYPE_NSE_REPO_CONSISTENCY,
    JOB_TYPE_RESEARCH_HISTORY_ARCHIVE,
)

# External-vendor/LLM data-collection and archival work — same category stock_simulator's live
# capture loops already exclusively run under `trade release`. Never dispatched outside
# STACK_PROFILE=release.
COLLECTION_JOB_TYPES: frozenset[str] = frozenset(
    {
        JOB_TYPE_HUB_CAPTURE_INTRADAY,
        JOB_TYPE_HUB_CAPTURE_FACTOR_SNAPSHOT,
        JOB_TYPE_HUB_MORNING_CALIBRATION,
        JOB_TYPE_HUB_EVENING_MAINTENANCE,
        JOB_TYPE_INDEX_FACTOR_SNAPSHOT,
        JOB_TYPE_INDEX_RESEARCH,
        JOB_TYPE_INDEX_PLAN_REFRESH,
        JOB_TYPE_INDEX_CALIBRATION,
        JOB_TYPE_COMPANY_RESEARCH_ARCHIVE,
        JOB_TYPE_INDEX_PREDICTION_POST_CLOSE,
        JOB_TYPE_HUB_NEWS_ENTITY,
        JOB_TYPE_HUB_NEWS_INGEST,
        JOB_TYPE_STOCK_HISTORY_COVERAGE_SWEEP,
        JOB_TYPE_GLOBAL_MACRO_EOD_REFRESH,
        JOB_TYPE_OI_SNAPSHOT,
        JOB_TYPE_REINFERENCE_TICK,
        JOB_TYPE_PUMP_DUMP_PROXY,
        JOB_TYPE_FUTURES_POSITIONING,
        JOB_TYPE_MAX_PAIN_BHAVCOPY,
        JOB_TYPE_CONSTITUENT_VOLUME_SNAPSHOT,
        JOB_TYPE_FORECAST_PLATFORM_RETRAIN,
        JOB_TYPE_QUANTILE_FORECAST_LEDGER_PUSH,
        JOB_TYPE_OPTIONS_PLAN_REFRESH,
        JOB_TYPE_NSE_MACRO_REFRESH,
        JOB_TYPE_NSE_REPO_CONSISTENCY,
        JOB_TYPE_RESEARCH_HISTORY_ARCHIVE,
        JOB_TYPE_FINANCIAL_KNOWLEDGE_CURATOR,
        # Literal, not a constant: no pipeline claims this job_type, so it falls
        # through `try_dispatch_pipeline_job` to the legacy agent-prompt path and
        # there is no handler module to import a constant from. The gate is
        # applied in `executor.tick` against `config.job_type`, so it still binds
        # on that path.
        #
        # Gated because it is external-vendor collection: a daily cron spawns a
        # detached worker that fetches external forecasts per ticker/horizon.
        # Confirmed running on DEV 2026-09-07 (`nifty-external-predictions-refresh`,
        # status completed, last_run_at 18:35Z), which is exactly the independent
        # dev collection `2026-09-02-release-sole-data-collector` removes.
        # See .claude/backlog/items/2026-09-08-finish-e2e-and-data-defects-plan.md
        "external_predictions_refresh",
    }
)


def collection_job_dispatch_enabled(stack_profile: str) -> bool:
    """Whether collection-type jobs should be allowed to dispatch under this profile.

    Plain string comparison, not a module-level constant, so it stays trivially testable without
    needing to reload this module under different env vars — same pattern as
    stock_simulator/service/app.py's `_live_capture_enabled()`."""
    return stack_profile == "release"


def is_collection_job(job_type: str) -> bool:
    return job_type in COLLECTION_JOB_TYPES


#: Job types that are **read-only and idempotent**, and therefore safe to recover to PENDING
#: WITHOUT being auto-paused when the stack stops mid-run.
#:
#: Auto-pause on shutdown recovery exists to stop a job whose side effects may be half-applied
#: from silently re-running (`lifecycle._advance_recovered_job`). That is right for a job that
#: writes. It is actively harmful for a monitoring check, which has no side effects to half-apply
#: and whose entire value is that it keeps running:
#:
#: Measured 2026-09-07 on release, `factor-health` was the **only paused job of 61**, with
#: `auto_paused_reason: "auto-paused: recovered on stack shutdown"`. A daily check that scans the
#: whole hub is disproportionately likely to be mid-run when a `trade release update` restarts the
#: tier — so the monitor switched itself off precisely when the stack was being changed, which is
#: when monitoring matters most. Nothing then noticed that the global index-history writer had been
#: dead for nine days, or that 27 factors were stale on disk.
#:
#: Deliberately narrow: only the two factor-health types. They read the hub and the registry and
#: raise on a regression; the live variant additionally makes read-only vendor calls. Every eval
#: type is left OUT even though most are probably also safe — widening this set is a claim about
#: each job's side effects that should be made per job, with evidence, not in bulk.
#: See `.claude/backlog/items/2026-09-07-a-paused-health-job-is-a-silent-monitor.md`.
SAFE_TO_AUTO_RESUME_JOB_TYPES = frozenset(
    {
        JOB_TYPE_FACTOR_HEALTH,
        JOB_TYPE_FACTOR_HEALTH_LIVE,
    }
)


def is_safe_to_auto_resume(job_type: str) -> bool:
    """Whether shutdown/boot recovery may leave this job UNPAUSED.

    A `False` answer is the safe default: an unknown job type keeps the existing auto-pause
    behaviour, so this can only ever narrow the set of jobs that go silent, never widen the set
    that re-runs unattended.
    """
    return job_type in SAFE_TO_AUTO_RESUME_JOB_TYPES


# The genuinely operational/session-scoped types called out in this module's own docstring —
# never collection work, dispatched fast (0.1-19s observed live) and on a short cadence an
# autonomous agent actually depends on (autonomous_agent_watch's 7-minute default). Given their
# own executor loop and tick barrier (`ScheduledResearchExecutor`'s `_run_operational`/
# `_operational_tick`) so a long collection-job dispatch on the main loop can never hold them
# hostage — see .claude/backlog/items/2026-09-07-scheduler-tick-head-of-line-stall.md, where a
# single 31.4-minute hub_evening_maintenance run starved every autonomous agent's watch/news/
# strategy-review cadence for the same duration because `tick()` is a barrier over ALL due jobs.
OPERATIONAL_TIER_JOB_TYPES: frozenset[str] = frozenset(AUTONOMOUS_JOB_TYPES) | frozenset(
    {
        JOB_TYPE_RECORDING_WAKE,
        JOB_TYPE_OPTIONS_POSITION_MONITOR,
    }
)


def is_operational_tier_job(job_type: str) -> bool:
    return job_type in OPERATIONAL_TIER_JOB_TYPES
