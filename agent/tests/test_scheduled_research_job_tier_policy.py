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
