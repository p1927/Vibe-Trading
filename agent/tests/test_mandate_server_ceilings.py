"""Mandate ceilings come from the SERVER (Trade risk config), never from the model.

CEO decision 2026-10-14: ``propose_mandate_profiles`` used to take ``ceilings`` from the model and
``commit_mandate`` re-validated against that same snapshot; mandates defaulted to us_equity/USD.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import src.live.paths as paths
from src.live.mandate.ceilings import (
    CeilingError,
    check_request,
    order_capable_connectors,
    server_ceilings,
    tighten,
)
from src.live.mandate.commit import CommitError, commit_mandate, save_proposal
from src.live.mandate.store import load_mandate
from src.tools.propose_mandate_tool import ProposeMandateProfilesTool
from src.trading.profiles import list_profiles

pytestmark = pytest.mark.unit

ORDER_CAPABLE = sorted(order_capable_connectors())


@pytest.fixture
def live_runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(paths, "get_runtime_root", lambda: tmp_path)
    return tmp_path


def _propose(broker: str, **kwargs) -> dict:
    return json.loads(ProposeMandateProfilesTool().execute(broker=broker, **kwargs))


def test_every_order_capable_profile_has_server_ceilings() -> None:
    assert ORDER_CAPABLE, "no order-capable profile found"
    for profile in list_profiles():
        if not profile.readonly and any(c.startswith("orders.place") for c in profile.capabilities):
            assert profile.connector in ORDER_CAPABLE
    for broker in ORDER_CAPABLE:
        ceilings = server_ceilings(broker)
        assert ceilings["market"] == "IN" and ceilings["currency"] == "INR"
        assert ceilings["asset_classes"] == ["in_equity"]
        assert ceilings["leverage"] == "none"
        for key in ("max_order_notional_usd", "max_total_exposure_usd", "max_daily_loss_inr"):
            assert ceilings[key] > 0


def test_unknown_or_readonly_broker_has_no_ceilings() -> None:
    with pytest.raises(CeilingError):
        server_ceilings("not-a-broker")
    with pytest.raises(CeilingError):
        server_ceilings("")


@pytest.mark.parametrize("broker", ORDER_CAPABLE)
def test_model_ceiling_above_server_is_rejected_loudly(broker: str, live_runtime: Path) -> None:
    server = server_ceilings(broker)
    for key in ("account_funding_usd", "max_order_notional_usd", "max_total_exposure_usd",
                "max_trades_per_day", "max_daily_loss_inr"):
        out = _propose(broker, ceilings={key: server[key] * 10})
        assert out["status"] == "error" and "exceeds the server ceiling" in out["error"], (key, out)
    # alias spellings are the same limit
    assert _propose(broker, ceilings={"max_order_usd": 10**9})["status"] == "error"
    assert _propose(broker, ceilings={"daily_trade_cap": 10**6})["status"] == "error"
    # widening the whitelist / leverage / market is refused too
    assert _propose(broker, ceilings={"instruments": ["equity", "crypto"]})["status"] == "error"
    assert _propose(broker, ceilings={"asset_classes": ["us_equity"]})["status"] == "error"
    assert _propose(broker, ceilings={"leverage": 5})["status"] == "error"
    assert _propose(broker, ceilings={"market": "US"})["status"] == "error"
    assert _propose(broker, ceilings={"currency": "USD"})["status"] == "error"
    # nothing was persisted for any rejected request
    assert not (paths.broker_dir(broker) / "proposals").exists()


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -1, True, "500", None])
def test_model_ceiling_non_finite_negative_or_bool_rejected(bad, live_runtime: Path) -> None:
    out = _propose(ORDER_CAPABLE[0], ceilings={"max_order_notional_usd": bad})
    assert out["status"] == "error"


def test_unknown_ceiling_key_rejected(live_runtime: Path) -> None:
    assert _propose(ORDER_CAPABLE[0], ceilings={"max_everything": 1})["status"] == "error"


@pytest.mark.parametrize("broker", ORDER_CAPABLE)
def test_propose_without_ceilings_uses_server_india_defaults(broker: str, live_runtime: Path) -> None:
    server = server_ceilings(broker)
    out = _propose(broker, intent="large-cap NIFTY stocks")
    assert out["type"] == "mandate.proposal"
    assert out["ceilings"]["market"] == "IN"
    assert out["ceilings"]["currency"] == "INR"
    for profile in out["profiles"]:
        assert profile["max_order_usd"] <= server["max_order_notional_usd"]
        assert profile["max_total_exposure_usd"] <= server["max_total_exposure_usd"]
        assert profile["daily_trade_cap"] <= server["max_trades_per_day"]
        assert profile["asset_classes"] == ["in_equity"]
        assert "AAPL" not in profile["universe"]


def test_requested_lower_values_are_honoured(live_runtime: Path) -> None:
    out = _propose(ORDER_CAPABLE[0], ceilings={"max_order_notional_usd": 100.0, "max_trades_per_day": 1,
                                                "instruments": ["equity"]})
    assert out["type"] == "mandate.proposal"
    for profile in out["profiles"]:
        assert profile["max_order_usd"] <= 100.0
        assert profile["daily_trade_cap"] <= 1
        assert profile["instruments"] == ["equity"]


def _forged_proposal(broker: str, profile: dict, stored_ceilings: dict) -> str:
    proposal_id = "mp_" + "b" * 32
    save_proposal({
        "proposal_id": proposal_id,
        "account": {"broker": broker},
        "ceilings_ref": "forged",
        "ceilings": stored_ceilings,
        "profiles": [{"ordinal": 1, "leverage": "none", "instruments": ["equity"], **profile}],
    })
    return proposal_id


@pytest.mark.parametrize("broker", ORDER_CAPABLE)
@pytest.mark.parametrize("profile", [
    {"max_order_usd": 10**7, "max_total_exposure_usd": 100.0, "daily_trade_cap": 1},
    {"max_order_usd": 100.0, "max_total_exposure_usd": 10**7, "daily_trade_cap": 1},
    {"max_order_usd": 100.0, "max_total_exposure_usd": 100.0, "daily_trade_cap": 10**4},
    {"max_order_usd": 100.0, "max_total_exposure_usd": 100.0, "daily_trade_cap": 1, "leverage": 4},
    {"max_order_usd": 100.0, "max_total_exposure_usd": 100.0, "daily_trade_cap": 1,
     "instruments": ["equity", "crypto"]},
    {"max_order_usd": 100.0, "max_total_exposure_usd": 100.0, "daily_trade_cap": 1,
     "asset_classes": ["us_equity"]},
])
@pytest.mark.parametrize("stored", [
    {"max_order_notional_usd": 10**9, "max_total_exposure_usd": 10**9, "max_trades_per_day": 10**9,
     "leverage": 10, "allowed_instruments": ["equity", "crypto"], "asset_classes": ["us_equity"]},
    {},  # an empty stored snapshot used to switch the ceiling check OFF entirely
])
def test_commit_validates_against_server_not_the_stored_snapshot(
    broker: str, profile: dict, stored: dict, live_runtime: Path
) -> None:
    proposal_id = _forged_proposal(broker, profile, stored)
    with pytest.raises(CommitError):
        commit_mandate(proposal_id=proposal_id, ordinal=1, adjustments=None, consent_ack=True, broker=broker)
    assert load_mandate(broker) is None


def test_commit_refuses_a_non_order_capable_broker(live_runtime: Path) -> None:
    proposal_id = _forged_proposal("not-a-broker", {"max_order_usd": 1.0}, {})
    with pytest.raises(CommitError):
        commit_mandate(proposal_id=proposal_id, ordinal=1, adjustments=None, consent_ack=True,
                       broker="not-a-broker")


@pytest.mark.parametrize("broker", ORDER_CAPABLE)
def test_commit_in_bounds_defaults_to_india_equity(broker: str, live_runtime: Path) -> None:
    out = _propose(broker)
    result = commit_mandate(proposal_id=out["proposal_id"], ordinal=1, adjustments=None,
                            consent_ack=True, broker=broker)
    assert result["mandate_id"]
    mandate = json.loads((paths.broker_dir(broker) / "mandate.json").read_text())
    assert mandate["universe"]["asset_classes"] == ["in_equity"]


def test_broker_reported_ceiling_can_only_lower() -> None:
    server = server_ceilings(ORDER_CAPABLE[0])
    lowered = tighten(server, {"max_order_notional_usd": 50.0})
    assert lowered["max_order_notional_usd"] == 50.0
    raised = tighten(server, {"max_order_notional_usd": server["max_order_notional_usd"] * 100})
    assert raised["max_order_notional_usd"] == server["max_order_notional_usd"]
    assert tighten(server, {"max_order_notional_usd": float("nan")}) == server


def test_commit_honours_a_lower_broker_ceiling(live_runtime: Path) -> None:
    broker = ORDER_CAPABLE[0]
    out = _propose(broker)
    big = max(p["max_order_usd"] for p in out["profiles"])
    with pytest.raises(CommitError):
        commit_mandate(proposal_id=out["proposal_id"], ordinal=3, adjustments=None, consent_ack=True,
                       broker=broker, ceilings_ref={"max_order_notional_usd": big / 10})


def test_check_request_returns_effective_snapshot() -> None:
    server = server_ceilings(ORDER_CAPABLE[0])
    eff = check_request({"max_order_usd": 5.0}, server)
    assert eff["max_order_notional_usd"] == 5.0 and eff["max_total_exposure_usd"] == server["max_total_exposure_usd"]
