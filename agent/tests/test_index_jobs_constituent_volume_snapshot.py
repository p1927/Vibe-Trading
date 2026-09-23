"""constituent_volume_snapshot scheduled job (D102 registry-bypass audit,
.claude/backlog/items/2026-09-11-index-job-handlers-ignore-had-errors.md): the job's dispatch
function now checks `IN/volume_interest_score` is catalogued in the factor registry
(`factors/constituent_specs.py`) before running the existing accumulator capture, instead of
calling `constituent_volume_snapshot_store` with no registry involvement at all."""
from __future__ import annotations

import pytest

from src.scheduled_research import index_jobs


@pytest.mark.unit
def test_run_constituent_volume_snapshot_job_delegates_to_capture_and_append(monkeypatch):
    calls = {"capture": 0}

    def _fake_capture():
        calls["capture"] += 1
        return {"status": "ok", "rows_added": 50, "day": "2026-09-17", "symbols_captured": 50}

    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.constituent_volume_snapshot_store."
        "capture_and_append_constituent_volume_snapshot",
        _fake_capture,
    )
    monkeypatch.setattr(
        "trade_integrations.factors.registry.constituent_factor_keys_for_market",
        lambda market: ("volume_interest_score",),
    )
    monkeypatch.setattr("nautilus_openalgo_bridge.market_hours.is_real_nse_market_open", lambda: True)

    result = index_jobs.run_constituent_volume_snapshot_job(None)

    assert calls["capture"] == 1
    assert result["status"] == "ok"
    assert result["symbols_captured"] == 50


@pytest.mark.unit
def test_run_constituent_volume_snapshot_job_checks_registry_before_capture(monkeypatch):
    """A future rename/removal of the `IN/volume_interest_score` registry entry must fail this
    job loudly, not silently keep writing to an accumulator no longer catalogued anywhere."""
    calls = {"capture": 0}

    def _fake_capture():
        calls["capture"] += 1
        return {"status": "ok", "rows_added": 0}

    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.constituent_volume_snapshot_store."
        "capture_and_append_constituent_volume_snapshot",
        _fake_capture,
    )
    monkeypatch.setattr(
        "trade_integrations.factors.registry.constituent_factor_keys_for_market",
        lambda market: (),  # key no longer catalogued
    )

    with pytest.raises(ValueError, match="volume_interest_score"):
        index_jobs.run_constituent_volume_snapshot_job(None)

    assert calls["capture"] == 0, "must not capture when the registry entry is missing"


@pytest.mark.unit
def test_run_constituent_volume_snapshot_job_reaches_the_real_registry_entry():
    """End-to-end against the real (unmocked) factor registry: `IN/volume_interest_score` must
    actually resolve for a real NIFTY50 symbol, not just in a monkeypatched test double."""
    from trade_integrations.factors.registry import require_factor

    spec = require_factor("IN", "volume_interest_score", "RELIANCE")
    assert spec.key == "volume_interest_score"
    assert spec.instrument == "RELIANCE"
    assert spec.sources[0].provider_id == "internal:constituent_volume"


@pytest.mark.unit
def test_constituent_volume_snapshot_job_type_registered():
    assert index_jobs.JOB_TYPE_CONSTITUENT_VOLUME_SNAPSHOT in index_jobs.INDEX_JOB_TYPES
