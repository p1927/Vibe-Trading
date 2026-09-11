"""register_default_trade_data_jobs: the unified hub calibration gate may skip only the
jobs it subsumes (fills export, research-history archive) — never the NSE browser jobs.

Regression for .claude/backlog/items/2026-09-11-nse-browser-jobs-never-registered.md
(Trade repo): a whole-function early return under the default-on unified flag meant
``nse-macro-refresh`` / ``nse-repo-consistency`` were never registered in either tier.
"""

from __future__ import annotations

import pytest

from src.config.accessor import reset_env_config
from src.scheduled_research.hub_calibration_jobs import (
    HUB_CALIBRATION_ENABLE_SCHEDULER_ENV,
    HUB_CALIBRATION_UNIFIED_ENV,
)
from src.scheduled_research.store import ScheduledResearchJobStore
from src.scheduled_research.trade_data_jobs import (
    TRADE_DATA_ENABLE_SCHEDULER_ENV,
    register_default_trade_data_jobs,
)

NSE_JOB_IDS = {"nse-macro-refresh", "nse-repo-consistency"}
SUBSUMED_JOB_IDS = {"hub-trade-fills-export", "hub-research-history-archive"}


def _set_env(monkeypatch, *, trade_data: str, calibration: str, unified: str) -> None:
    monkeypatch.setenv(TRADE_DATA_ENABLE_SCHEDULER_ENV, trade_data)
    monkeypatch.setenv(HUB_CALIBRATION_ENABLE_SCHEDULER_ENV, calibration)
    monkeypatch.setenv(HUB_CALIBRATION_UNIFIED_ENV, unified)
    reset_env_config()


def _ids(store: ScheduledResearchJobStore) -> set[str]:
    return set(store.load())


def test_unified_calibration_on_registers_nse_jobs_but_not_subsumed_ones(monkeypatch, tmp_path):
    _set_env(monkeypatch, trade_data="1", calibration="1", unified="1")
    store = ScheduledResearchJobStore(tmp_path / "jobs.json")

    created = register_default_trade_data_jobs(store)

    assert created == 2
    assert _ids(store) == NSE_JOB_IDS


def test_unified_calibration_off_registers_all_four(monkeypatch, tmp_path):
    _set_env(monkeypatch, trade_data="1", calibration="1", unified="0")
    store = ScheduledResearchJobStore(tmp_path / "jobs.json")

    assert register_default_trade_data_jobs(store) == 4
    assert _ids(store) == NSE_JOB_IDS | SUBSUMED_JOB_IDS


def test_trade_data_scheduler_off_registers_nothing(monkeypatch, tmp_path):
    _set_env(monkeypatch, trade_data="0", calibration="1", unified="1")
    store = ScheduledResearchJobStore(tmp_path / "jobs.json")

    assert register_default_trade_data_jobs(store) == 0
    assert _ids(store) == set()


def test_registration_is_idempotent(monkeypatch, tmp_path):
    _set_env(monkeypatch, trade_data="1", calibration="1", unified="1")
    store = ScheduledResearchJobStore(tmp_path / "jobs.json")

    register_default_trade_data_jobs(store)
    assert register_default_trade_data_jobs(store) == 0


def test_gate_errors_propagate(monkeypatch, tmp_path):
    """The gate used to sit in ``except Exception: pass``; an internal error must surface."""
    _set_env(monkeypatch, trade_data="1", calibration="1", unified="1")
    from src.scheduled_research import hub_calibration_jobs

    def _boom(*_a, **_k):
        raise RuntimeError("config broken")

    monkeypatch.setattr(hub_calibration_jobs, "is_hub_unified_calibration_enabled", _boom)
    store = ScheduledResearchJobStore(tmp_path / "jobs.json")

    with pytest.raises(RuntimeError, match="config broken"):
        register_default_trade_data_jobs(store)
