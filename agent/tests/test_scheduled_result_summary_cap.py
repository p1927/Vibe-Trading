"""`last_result_summary` is capped at the one writer (executor._store_result_summary), so a handler step
that returns a huge result (hub-evening-maintenance: 995 KB of scenario drafts) cannot bloat the
scheduled-runs store and every /scheduled-runs list payload
(2026-10-13-scenario-autogen-summary-bloats-scheduled-runs). Existing records are never rewritten."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from src.scheduled_research.executor import (
    LAST_RESULT_CONFIG_KEY,
    ScheduledResearchExecutor,
    cap_result_summary,
)
from src.scheduled_research.models import JobStatus, ScheduledResearchJob
from src.scheduled_research.store import ScheduledResearchJobStore

LIMIT = 4096


def _size(v) -> int:
    return len(json.dumps(v))


def test_small_summary_is_returned_untouched() -> None:
    summary = {"status": "ok", "steps": {"a": {"status": "ok", "n": 3}}}
    assert cap_result_summary(summary, LIMIT) is summary


def test_incident_shape_keeps_counts_and_sibling_steps_drops_the_bulk() -> None:
    drafts = [{"draft_id": f"d{i}", "body": "x" * 5000} for i in range(40)]
    summary = {
        "status": "partial",
        "steps": {
            "news_reconcile": {"status": "ok", "reconciled": 12},
            "scenario_autogen": {"status": "ok", "by_ticker": {"NIFTY": {"generated": drafts}}},
        },
    }
    capped = cap_result_summary(summary, LIMIT)
    assert _size(capped) <= LIMIT + 400  # bounded (markers add a few bytes)
    assert capped["status"] == "partial" and capped["truncated"] is True
    assert capped["original_bytes"] > 100_000
    assert capped["steps"]["news_reconcile"] == {"status": "ok", "reconciled": 12}  # siblings survive
    assert capped["steps"]["scenario_autogen"]["status"] == "ok"  # scalar next to the bulk survives


def test_variants_huge_list_huge_string_unserialisable_and_deep_nesting() -> None:
    capped = cap_result_summary({"status": "ok", "rows": list(range(100_000))}, LIMIT)
    assert capped["status"] == "ok" and capped["rows"]["truncated"] is True

    capped = cap_result_summary({"status": "error", "error": "boom " * 50_000}, LIMIT)
    assert capped["error"].startswith("boom boom") and len(capped["error"]) < 600  # message stays readable

    class Weird:
        def __str__(self) -> str:  # json default=str handles it; size is still measured
            return "w" * 10

    assert cap_result_summary({"status": "ok", "w": Weird()}, LIMIT)["status"] == "ok"

    deep: dict = {"status": "ok"}
    node = deep
    for i in range(30):
        node["next"] = {"i": i, "pad": "p" * 3000}
        node = node["next"]
    capped = cap_result_summary(deep, LIMIT)
    assert capped["status"] == "ok" and _size(capped) < 4 * LIMIT


def test_module_default_limit_applies(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.scheduled_research import executor

    monkeypatch.setattr(executor, "RESULT_SUMMARY_MAX_BYTES", 100)
    assert cap_result_summary({"status": "ok", "big": "x" * 50, "rows": list(range(500))})["truncated"] is True


def test_writer_caps_for_success_and_for_a_failed_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Both completion paths (ok and handler-raised) go through the same writer."""
    monkeypatch.setenv("STACK_PROFILE", "release")  # hub_evening_maintenance is a release-only collection job
    store = ScheduledResearchJobStore(path=tmp_path / "jobs.json")
    big = {"status": "partial", "blob": ["x" * 1000] * 500}

    async def dispatch(job: ScheduledResearchJob) -> None:
        job.config[LAST_RESULT_CONFIG_KEY] = dict(big)
        if job.id == "fail":
            raise RuntimeError("handler failed after attaching its summary")

    for job_id in ("ok", "fail"):
        store.upsert(ScheduledResearchJob(
            id=job_id, prompt="p", schedule="3600000", next_run_at=10, status=JobStatus.PENDING,
            created_at=0, config={"job_type": "hub_evening_maintenance"},
        ))

    async def go() -> None:
        executor = ScheduledResearchExecutor(store, dispatch, dispatch_concurrency=2)
        await executor.tick(100, wait=True)

    asyncio.run(go())
    for job_id in ("ok", "fail"):
        stored = store.get(job_id)
        assert stored.last_result_summary and stored.last_result_summary.get("truncated") is True, job_id
        assert _size(stored.last_result_summary) < 70_000
        assert LAST_RESULT_CONFIG_KEY not in stored.config
    assert store.get("fail").consecutive_failures == 1  # the failure is recorded; the cap does not hide it


def test_already_stored_oversized_record_is_not_rewritten(tmp_path: Path) -> None:
    store = ScheduledResearchJobStore(path=tmp_path / "jobs.json")
    old = {"status": "ok", "blob": "y" * 200_000}
    store.upsert(ScheduledResearchJob(
        id="legacy", prompt="p", schedule="3600000", next_run_at=10**12, status=JobStatus.COMPLETED,
        created_at=0, last_result_summary=old,
    ))
    assert store.get("legacy").last_result_summary == old  # migration-free: only new writes are capped
