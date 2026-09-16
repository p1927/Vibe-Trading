"""D80: a per-family `*_ENABLE_SCHEDULER` env switch must register the family
paused, never leave it unregistered.

Before this fix, ~10 job families (all but the news jobs, already fixed by D22)
decided whether their default jobs were registered at all from a `.env` switch
read at boot (`register_default_*_jobs` early-returning, or the boot-time caller
in `scheduled_startup.py` skipping the call outright). A family switched off was
therefore invisible in the Scheduled UI and unresumable — the same shape D22
already ruled out for news jobs. See docs/DECISIONS.md D80 and
.claude/backlog/items/2026-09-16-per-family-scheduler-env-switches.md.

These tests cover three structurally different call shapes converted by D80:
- `factor_health_jobs`: a plain "if store.get(id) is None: upsert" family (the
  simplest, most common shape — also representative of capture_jobs,
  hub_calibration_jobs, financial_knowledge_jobs, dst_eval_jobs, trade_data_jobs).
- `options_jobs`: a "defaults list -> loop" family whose dispatch functions also
  used to re-check the same switch at run time (a second on/off mechanism D80
  says to drop).
- `index_jobs`: the family with two independent switches feeding one
  registration function (INDEX_RESEARCH_ENABLE_SCHEDULER gates the whole family;
  INDEX_MONITOR_ENABLE_SCHEDULER gates one job within it), and whose reconcile
  loop must keep leaving `paused` alone on an already-registered job.

Each "off" test is a red/green regression: on the pre-D80 code (early return /
external `if enabled:` gate), the job is simply never registered and this
assertion fails with `job is None`; on the fixed code, it is registered and
`paused` is True.
"""

from __future__ import annotations

import pytest

from src.config.accessor import reset_env_config
from src.scheduled_research import factor_health_jobs, index_jobs, options_jobs
from src.scheduled_research.factor_health_jobs import FACTOR_HEALTH_ENABLE_SCHEDULER_ENV
from src.scheduled_research.index_jobs import (
    INDEX_MONITOR_ENABLE_SCHEDULER_ENV,
    INDEX_RESEARCH_ENABLE_SCHEDULER_ENV,
)
from src.scheduled_research.options_jobs import OPTIONS_MONITOR_ENABLE_SCHEDULER_ENV
from src.scheduled_research.store import ScheduledResearchJobStore


@pytest.mark.unit
def test_factor_health_disabled_registers_paused_not_absent(monkeypatch, tmp_path):
    monkeypatch.setenv(FACTOR_HEALTH_ENABLE_SCHEDULER_ENV, "0")
    store = ScheduledResearchJobStore(tmp_path / "jobs.json")

    created = factor_health_jobs.register_default_factor_health_jobs(store)

    assert created >= 1
    job = store.get("factor-health")
    assert job is not None, "family must still register when its switch is off (D80)"
    assert job.paused is True
    assert job.auto_paused_reason == "FACTOR_HEALTH_ENABLE_SCHEDULER disabled (D80)"


@pytest.mark.unit
def test_factor_health_enabled_registers_unpaused(monkeypatch, tmp_path):
    monkeypatch.setenv(FACTOR_HEALTH_ENABLE_SCHEDULER_ENV, "1")
    store = ScheduledResearchJobStore(tmp_path / "jobs.json")

    factor_health_jobs.register_default_factor_health_jobs(store)

    job = store.get("factor-health")
    assert job is not None
    assert job.paused is False
    assert job.auto_paused_reason is None


@pytest.mark.unit
def test_factor_health_existing_pause_state_untouched_on_reregister(monkeypatch, tmp_path):
    """An operator's manual pause/resume must survive a later boot even if the
    switch's own value hasn't changed — mirrors index_jobs' reconcile guarantee.
    """
    monkeypatch.setenv(FACTOR_HEALTH_ENABLE_SCHEDULER_ENV, "0")
    store = ScheduledResearchJobStore(tmp_path / "jobs.json")
    factor_health_jobs.register_default_factor_health_jobs(store)

    resumed = store.get("factor-health")
    resumed.paused = False
    resumed.auto_paused_reason = None
    store.upsert(resumed)

    # Re-register (e.g. a restart): existing jobs are only created once
    # (`if store.get(id) is None`), so a resumed job must stay resumed.
    created_again = factor_health_jobs.register_default_factor_health_jobs(store)

    assert created_again == 0
    assert store.get("factor-health").paused is False


@pytest.mark.unit
def test_options_monitor_disabled_registers_paused_not_absent(monkeypatch, tmp_path):
    monkeypatch.setenv(OPTIONS_MONITOR_ENABLE_SCHEDULER_ENV, "0")
    reset_env_config()
    store = ScheduledResearchJobStore(tmp_path / "jobs.json")

    created = options_jobs.register_default_options_jobs(store)

    assert created == 2
    for job_id in ("options-plan-refresh", "options-position-monitor"):
        job = store.get(job_id)
        assert job is not None, "family must still register when its switch is off (D80)"
        assert job.paused is True
        assert job.auto_paused_reason == "OPTIONS_MONITOR_ENABLE_SCHEDULER disabled (D80)"
    reset_env_config()


@pytest.mark.unit
def test_options_monitor_active_no_longer_reads_scheduler_switch(monkeypatch):
    """D80: is_options_monitor_active() must be governed only by the master
    monitor switch now — the scheduler switch's on/off is the job's own pause,
    not a second dispatch-time gate for the same thing.
    """
    monkeypatch.setenv(OPTIONS_MONITOR_ENABLE_SCHEDULER_ENV, "0")
    reset_env_config()
    from trade_integrations.monitor import config as monitor_config

    monkeypatch.setattr(monitor_config, "is_monitor_enabled", lambda: True)

    assert options_jobs.is_options_monitor_active() is True
    reset_env_config()


@pytest.mark.unit
def test_index_research_disabled_registers_everything_paused(monkeypatch, tmp_path):
    monkeypatch.setenv(INDEX_RESEARCH_ENABLE_SCHEDULER_ENV, "0")
    monkeypatch.setenv(INDEX_MONITOR_ENABLE_SCHEDULER_ENV, "1")
    reset_env_config()
    store = ScheduledResearchJobStore(tmp_path / "jobs.json")

    index_jobs.register_default_index_jobs(store)

    # A large family: spot-check a representative job plus the tight-cadence clone.
    for job_id in ("nifty-index-research", "nifty-hub-news-ingest-light", "us-hub-news-ingest-tight"):
        job = store.get(job_id)
        assert job is not None, f"{job_id} must still register when INDEX_RESEARCH_ENABLE_SCHEDULER is off (D80)"
        assert job.paused is True
    reset_env_config()


@pytest.mark.unit
def test_index_monitor_disabled_registers_plan_refresh_paused_not_absent(monkeypatch, tmp_path):
    monkeypatch.setenv(INDEX_RESEARCH_ENABLE_SCHEDULER_ENV, "1")
    monkeypatch.setenv(INDEX_MONITOR_ENABLE_SCHEDULER_ENV, "0")
    reset_env_config()
    store = ScheduledResearchJobStore(tmp_path / "jobs.json")

    index_jobs.register_default_index_jobs(store)

    plan_refresh = store.get("nifty-index-plan-refresh")
    assert plan_refresh is not None, "must still register when INDEX_MONITOR_ENABLE_SCHEDULER is off (D80)"
    assert plan_refresh.paused is True
    assert plan_refresh.auto_paused_reason == "INDEX_MONITOR_ENABLE_SCHEDULER disabled (D80)"

    # The rest of the family is unaffected by the monitor-only switch.
    assert store.get("nifty-index-research").paused is False
    reset_env_config()


@pytest.mark.unit
def test_index_jobs_fully_enabled_registers_unpaused(monkeypatch, tmp_path):
    monkeypatch.setenv(INDEX_RESEARCH_ENABLE_SCHEDULER_ENV, "1")
    monkeypatch.setenv(INDEX_MONITOR_ENABLE_SCHEDULER_ENV, "1")
    reset_env_config()
    store = ScheduledResearchJobStore(tmp_path / "jobs.json")

    index_jobs.register_default_index_jobs(store)

    for job_id in ("nifty-index-research", "nifty-index-plan-refresh"):
        job = store.get(job_id)
        assert job is not None
        assert job.paused is False
        assert job.auto_paused_reason is None
    reset_env_config()
