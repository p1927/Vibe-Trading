"""Trade R5 class-sweep fixes in the fork: the OpenAlgo host default is the running tier's, the connector config
(it holds the API key) is written atomically at 0600 from creation, and the archive jobs leave the day to the
market day (never a UTC date computed here)."""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from src.config import accessor
from src.trading.connectors.openalgo import sdk


@pytest.fixture(autouse=True)
def _fresh_env_config():
    accessor.reset_env_config()
    yield
    accessor.reset_env_config()


def test_openalgo_host_default_follows_the_tier_not_a_dev_literal(monkeypatch) -> None:
    monkeypatch.delenv("OPENALGO_HOST", raising=False)
    monkeypatch.setenv("STACK_PROFILE", "release")
    assert sdk.OpenAlgoConfig().host.endswith(":5011")  # release OpenAlgo (stack/ports.yaml), never dev's 5001
    assert sdk.OpenAlgoConfig.from_mapping({}).host.endswith(":5011")
    monkeypatch.setenv("STACK_PROFILE", "dev")
    accessor.reset_env_config()
    assert sdk.OpenAlgoConfig().host.endswith(":5001")


def test_save_config_is_atomic_and_0600(monkeypatch, tmp_path: Path) -> None:
    target = tmp_path / "openalgo.json"
    monkeypatch.setattr(sdk, "config_path", lambda: target)
    sdk.save_config(sdk.OpenAlgoConfig(host="http://127.0.0.1:1", api_key="SECRETKEY"))
    assert stat.S_IMODE(os.stat(target).st_mode) == 0o600
    assert "SECRETKEY" in target.read_text()
    assert not [p for p in tmp_path.iterdir() if p.name.endswith(".tmp")]
    assert sdk.load_config().api_key == "SECRETKEY"


def test_archive_jobs_pass_no_utc_day(monkeypatch) -> None:
    from src.scheduled_research import index_jobs, trade_data_jobs

    seen: dict[str, dict] = {}
    import trade_integrations.context.hub as hub

    monkeypatch.setattr(hub, "archive_company_research_snapshots", lambda **kw: seen.setdefault("company", kw))
    monkeypatch.setattr(hub, "archive_options_stock_snapshots", lambda **kw: seen.setdefault("options", kw))
    index_jobs.run_company_research_archive_job({})
    trade_data_jobs.run_research_history_archive_job({})
    assert seen == {"company": {"as_of_date": None}, "options": {"as_of_date": None}}  # the archive keys by the market day
    seen.clear()
    index_jobs.run_company_research_archive_job({"as_of_date": "2026-01-02"})
    assert seen["company"] == {"as_of_date": "2026-01-02"}
