"""Tests for job_tier_policy.py and executor.py's `_collection_dispatch_blocked` gate — release
is the sole active dispatcher for data-collection job types, dev must not independently dispatch
them. See .claude/backlog/items/2026-09-02-vibe-trading-home-scope-audit.md.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from src.scheduled_research.executor import ScheduledResearchExecutor, _collection_dispatch_blocked
from src.scheduled_research.job_tier_policy import (
    COLLECTION_JOB_TYPES,
    collection_job_dispatch_enabled,
    is_collection_job,
)
from src.scheduled_research.models import JobStatus, ScheduledResearchJob
from src.scheduled_research.store import ScheduledResearchJobStore


def _store(tmp_path: Path) -> ScheduledResearchJobStore:
    return ScheduledResearchJobStore(path=tmp_path / "jobs.json")


def _job(job_id: str, *, job_type: str, next_run_at: int = 0) -> ScheduledResearchJob:
    job = ScheduledResearchJob(
        id=job_id, prompt=f"prompt for {job_id}", schedule="1000",
        next_run_at=next_run_at, status=JobStatus.PENDING, created_at=0,
    )
    job.config = {"job_type": job_type}
    return job


def test_collection_job_dispatch_enabled_only_for_release() -> None:
    assert collection_job_dispatch_enabled("release") is True
    assert collection_job_dispatch_enabled("dev") is False
    assert collection_job_dispatch_enabled("") is False
    assert collection_job_dispatch_enabled("something-unexpected") is False


def test_is_collection_job_classifies_known_types() -> None:
    assert is_collection_job("hub_news_ingest") is True
    assert is_collection_job("index_calibration") is True
    # Operational/session-scoped — never gated.
    assert is_collection_job("trade_fills_export") is False
    assert is_collection_job("autonomous_agent_watch") is False
    assert is_collection_job("recording_wake") is False
    assert is_collection_job("options_position_monitor") is False
    # QA/eval — never gated (dev needs these to test its own changes).
    assert is_collection_job("recorder_dst") is False
    assert is_collection_job("prediction_eval") is False
    assert is_collection_job("index_research_eval") is False
    assert is_collection_job("autonomous_agents_eval") is False
    assert is_collection_job("news_quality_eval") is False
    assert is_collection_job("news_dedup_quality_eval") is False
    # Unknown type — not gated (conservative: only gate what's explicitly classified).
    assert is_collection_job("some_future_job_type_nobody_classified_yet") is False


def test_external_predictions_refresh_is_gated() -> None:
    """Daily cron spawning a detached worker that fetches external forecasts.

    Confirmed running on DEV 2026-09-07 (`nifty-external-predictions-refresh`,
    completed, last_run_at 18:35Z) — independent dev collection, which is what
    2026-09-02-release-sole-data-collector removes. No pipeline claims this
    job_type, so it dispatches via the legacy agent-prompt path; the gate binds
    there too because executor.tick checks config.job_type before dispatch.
    """
    assert is_collection_job("external_predictions_refresh") is True


def test_a_gated_job_is_blocked_even_with_no_registered_pipeline(monkeypatch) -> None:
    """The gate must not depend on a typed handler existing."""
    from src.scheduled_research.job_dispatch_registry import try_dispatch_pipeline_job

    monkeypatch.delenv("STACK_PROFILE", raising=False)
    job = _job("ext", job_type="external_predictions_refresh")
    # No pipeline claims it — it would fall through to the agent-prompt path...
    assert asyncio.run(try_dispatch_pipeline_job(job)) is False
    # ...and the tier gate still blocks it.
    assert _collection_dispatch_blocked(job) is True


def test_factor_health_stays_ungated_on_purpose() -> None:
    """Reviewed 2026-09-08 and deliberately NOT gated.

    `factor_health` (daily) reads only what is already on disk and collects
    nothing, and it is meaningful on both tiers precisely because each reads its
    own TRADE_STACK_HUB_DIR — release's real hub, dev's mirror. Gating it would
    blind dev to its own hub's drift. See factor_health_jobs.py's module docstring.
    """
    assert is_collection_job("factor_health") is False


def test_factor_health_live_stays_ungated_on_purpose() -> None:
    """Reviewed 2026-09-08 and deliberately kept ungated, despite vendor calls.

    The weekly variant does hit external vendors, so gating it was considered.
    Kept ungated because it *collects* nothing — it calls each RECORDED factor's
    live source only to assert the source still answers, and stores no vendor
    data. It is a health check, and dev needs to be able to run health checks
    against live sources to test its own changes to them, the same rationale the
    `*_eval` types are ungated under. The cost is one extra weekly read-only
    vendor pass, which was judged smaller than blinding dev.

    If this is ever revisited, the decision to change is "does dev need to
    exercise the live-source path at all", not "does it touch a vendor".
    """
    assert is_collection_job("factor_health_live") is False


def test_collection_job_types_is_nonempty_and_only_strings() -> None:
    assert len(COLLECTION_JOB_TYPES) >= 20
    assert all(isinstance(t, str) and t for t in COLLECTION_JOB_TYPES)


@pytest.mark.parametrize("stack_profile", ["dev", "", None])
def test_collection_dispatch_blocked_outside_release(monkeypatch, stack_profile) -> None:
    if stack_profile is None:
        monkeypatch.delenv("STACK_PROFILE", raising=False)
    else:
        monkeypatch.setenv("STACK_PROFILE", stack_profile)
    job = _job("j1", job_type="hub_news_ingest")

    assert _collection_dispatch_blocked(job) is True


def test_collection_dispatch_allowed_under_release(monkeypatch) -> None:
    monkeypatch.setenv("STACK_PROFILE", "release")
    job = _job("j1", job_type="hub_news_ingest")

    assert _collection_dispatch_blocked(job) is False


def test_non_collection_job_never_blocked(monkeypatch) -> None:
    monkeypatch.delenv("STACK_PROFILE", raising=False)
    job = _job("j1", job_type="trade_fills_export")

    assert _collection_dispatch_blocked(job) is False


def test_tick_skips_due_collection_job_outside_release(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("STACK_PROFILE", raising=False)
    store = _store(tmp_path)
    store.upsert(_job("collect-me", job_type="hub_news_ingest", next_run_at=10))
    calls: list[str] = []

    async def dispatch(job: ScheduledResearchJob) -> None:
        calls.append(job.id)

    async def scenario() -> None:
        executor = ScheduledResearchExecutor(store, dispatch)
        await executor.tick(100)

    asyncio.run(scenario())

    assert calls == []
    # Skipped, not mutated — next_run_at/status stay exactly as they were, same as a paused
    # job, so the job doesn't silently accumulate "overdue" churn while gated.
    saved = store.get("collect-me")
    assert saved is not None
    assert saved.next_run_at == 10
    assert saved.status == JobStatus.PENDING


def test_tick_dispatches_due_collection_job_under_release(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("STACK_PROFILE", "release")
    store = _store(tmp_path)
    store.upsert(_job("collect-me", job_type="hub_news_ingest", next_run_at=10))
    calls: list[str] = []

    async def dispatch(job: ScheduledResearchJob) -> None:
        calls.append(job.id)

    async def scenario() -> None:
        executor = ScheduledResearchExecutor(store, dispatch)
        await executor.tick(100)

    asyncio.run(scenario())

    assert calls == ["collect-me"]


def test_tick_dispatches_operational_job_regardless_of_profile(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("STACK_PROFILE", raising=False)
    store = _store(tmp_path)
    store.upsert(_job("export-fills", job_type="trade_fills_export", next_run_at=10))
    calls: list[str] = []

    async def dispatch(job: ScheduledResearchJob) -> None:
        calls.append(job.id)

    async def scenario() -> None:
        executor = ScheduledResearchExecutor(store, dispatch)
        await executor.tick(100)

    asyncio.run(scenario())

    assert calls == ["export-fills"]
