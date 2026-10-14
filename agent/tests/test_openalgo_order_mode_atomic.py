"""OpenAlgo order send re-checks the analyzer mode at the one send point (CEO decision 2026-10-14).

The mode used to be read in ``place_order`` and the order posted later: analyze could flip in between
(TOCTOU). ``_RestClient.post_order`` now reads it right before sending, fails closed when the mode is
unreadable, and never reads a reply that says it was not analyze-mode as ok.
"""

from __future__ import annotations

import sys
import types

import pytest

from src.trading.connectors.openalgo import sdk as oa

pytestmark = pytest.mark.unit


class _Shared:
    def __init__(self) -> None:
        self.outcome: dict = {"status": "success", "orderid": "OA1"}
        self.calls: list[tuple[str, dict]] = []

    def post(self, path: str, payload: dict, *, timeout: float = 30) -> dict:
        self.calls.append((path, dict(payload)))
        return self.outcome


@pytest.fixture
def shared(monkeypatch: pytest.MonkeyPatch) -> _Shared:
    client = _Shared()
    for name in ("trade_integrations", "trade_integrations.openalgo"):
        if name not in sys.modules:
            monkeypatch.setitem(sys.modules, name, types.ModuleType(name))
    module = types.ModuleType("trade_integrations.openalgo.rest_client")
    module.get_rest_client = lambda host=None, api_key=None: client  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "trade_integrations.openalgo.rest_client", module)
    return client


def _cfg() -> oa.OpenAlgoConfig:
    return oa.OpenAlgoConfig(api_key="test-key", host="http://127.0.0.1:5001", profile="paper")


def test_mode_flipped_off_between_check_and_send_never_sends(shared: _Shared, monkeypatch) -> None:
    reads = iter([True, False])  # an earlier check passes; the send-time read sees analyze OFF
    monkeypatch.setattr(oa._RestClient, "analyzer_status", lambda self: next(reads))
    assert oa._client(_cfg()).analyzer_status() is True
    result = oa.place_order(_cfg(), symbol="RELIANCE", side="buy", quantity=1)
    assert result["status"] == "error" and "Analyze mode is OFF" in result["error"]
    assert shared.calls == []


def test_unknown_mode_fails_closed(shared: _Shared, monkeypatch) -> None:
    def unreadable(self):
        raise RuntimeError("analyzer endpoint unreachable")

    monkeypatch.setattr(oa._RestClient, "analyzer_status", unreadable)
    assert oa.place_order(_cfg(), symbol="RELIANCE", side="buy", quantity=1)["status"] == "error"
    assert oa.cancel_order(_cfg(), "OA1", symbol="RELIANCE")["status"] == "error"
    assert shared.calls == []


def test_every_send_rereads_the_mode(shared: _Shared, monkeypatch) -> None:
    reads = []
    monkeypatch.setattr(oa._RestClient, "analyzer_status", lambda self: reads.append(1) or True)
    oa.place_order(_cfg(), symbol="RELIANCE", side="buy", quantity=1)
    oa.place_order(_cfg(), symbol="TCS", side="sell", quantity=2)
    assert len(reads) == 2 and len(shared.calls) == 2


def test_reply_saying_live_mode_is_never_ok(shared: _Shared, monkeypatch) -> None:
    monkeypatch.setattr(oa._RestClient, "analyzer_status", lambda self: True)
    shared.outcome = {"status": "success", "orderid": "OA9", "mode": "live"}
    result = oa.place_order(_cfg(), symbol="RELIANCE", side="buy", quantity=1)
    assert result["status"] == "error" and result["code"] == "order_outcome_unknown"


@pytest.mark.parametrize("price", ["nan", "inf", -1, 0, "abc"])
def test_limit_price_must_be_finite_positive(shared: _Shared, monkeypatch, price) -> None:
    monkeypatch.setattr(oa._RestClient, "analyzer_status", lambda self: True)
    result = oa.place_order(_cfg(), symbol="RELIANCE", side="buy", quantity=1, order_type="limit", limit_price=price)
    assert result["status"] == "error" and shared.calls == []
