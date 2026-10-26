"""CEO decision 2026-10-14: a Longbridge paper profile never runs on a live token.

The access token is the only thing selecting paper vs live, so the paper profile needs its own
LONGBRIDGE_PAPER_ACCESS_TOKEN and fails closed otherwise. The registry test pins the shape for every
connector: each write-capable paper profile must be classified (a new one fails until someone does).
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.config.accessor import reset_env_config
from src.trading import profiles
from src.trading.connectors.longbridge import credentials as lb_credentials
from src.trading.connectors.longbridge import sdk as lb

pytestmark = pytest.mark.unit

LIVE = {"LONGBRIDGE_APP_KEY": "k", "LONGBRIDGE_APP_SECRET": "s", "LONGBRIDGE_ACCESS_TOKEN": "live-token"}


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    for name in (*LIVE, "LONGBRIDGE_PAPER_ACCESS_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(lb_credentials, "get_runtime_root", lambda: tmp_path)
    reset_env_config()
    yield
    reset_env_config()


def _set(monkeypatch, **env):
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    reset_env_config()


def _boom(*_a, **_k):
    raise AssertionError("SDK must not be reached")


def test_paper_with_only_live_credentials_refuses_to_order(monkeypatch):
    _set(monkeypatch, **LIVE)
    monkeypatch.setattr(lb, "_require_openapi", lambda: MagicMock())
    monkeypatch.setattr(lb, "_call", _boom)
    cfg = lb.build_config({"profile": "paper"}, None)
    assert cfg.access_token == ""  # the live token never reaches the paper config
    out = lb.place_order(cfg, symbol="700.HK", side="buy", quantity=100)
    assert out["status"] == "error" and "LONGBRIDGE_PAPER_ACCESS_TOKEN" in out["error"]
    assert lb.check_status(cfg)["configured"] is False


def test_paper_token_equal_to_live_token_is_a_conflict(monkeypatch):
    _set(monkeypatch, **LIVE, LONGBRIDGE_PAPER_ACCESS_TOKEN="live-token")
    cfg = lb.build_config({"profile": "paper"}, None)
    assert cfg.access_token == ""
    assert cfg._credential_error is not None and cfg._credential_error.code == "credentials_conflict"


def test_paper_with_own_token_resolves_and_live_keeps_its_token(monkeypatch):
    _set(monkeypatch, **LIVE, LONGBRIDGE_PAPER_ACCESS_TOKEN="paper-token")
    assert lb.build_config({"profile": "paper"}, None).access_token == "paper-token"
    assert lb.build_config({"profile": "live-readonly"}, None).access_token == "live-token"


def test_paper_token_alone_never_opens_a_live_profile(monkeypatch):
    _set(monkeypatch, LONGBRIDGE_APP_KEY="k", LONGBRIDGE_APP_SECRET="s", LONGBRIDGE_PAPER_ACCESS_TOKEN="paper-token")
    assert lb.build_config({"profile": "live-readonly"}, None)._credential_error is not None


def test_config_cannot_be_relabelled_between_paper_and_live(monkeypatch):
    _set(monkeypatch, **LIVE, LONGBRIDGE_PAPER_ACCESS_TOKEN="paper-token")
    with pytest.raises(lb.LongbridgeConfigError):
        lb.build_config({"profile": "paper"}, None).with_overrides(profile="live")


#: Every connector with a write-capable paper profile, and why paper cannot reach a live account.
#: Audited 2026-10-14: only Longbridge shared one credential between its paper and live accounts.
PAPER_ISOLATION = {
    "alpaca": "paper host + paper-only keys",
    "binance": "testnet host + testnet keys",
    "dhan": "orders simulated locally, no broker call",
    "etoro": "demo environment keys",
    "futu": "OpenD simulate trade env",
    "longbridge": "separate LONGBRIDGE_PAPER_ACCESS_TOKEN (this file)",
    "mt5": "paper terminal login",
    "okx": "demo header + demo keys",
    "openalgo": "analyze (sandbox) mode",
    "shoonya": "orders simulated locally, no broker call",
    "tiger": "paper account prefix",
}


def test_every_writable_paper_profile_is_classified():
    writers = {p.connector for p in profiles.list_profiles() if p.environment == "paper" and not p.readonly}
    assert writers <= set(PAPER_ISOLATION), f"classify new paper-writing connector(s): {writers - set(PAPER_ISOLATION)}"
