"""The OpenAlgo connector sends orders through Trade's one order seam (Trade docs/DECISIONS.md D37).

``place_order`` and ``cancel_order`` post through ``trade_integrations.openalgo.rest_client``,
which claims every order by content and never retries one; reads keep the connector's own
``requests`` client. Trade is stood in by a fake module here, so this checks the routing and the
error codes only. The real dedupe is exercised on the Trade side
(``tests/test_vibe_connector_order_dedupe.py``).
"""

from __future__ import annotations

import sys
import types

import pytest

from src.trading.connectors.openalgo import sdk as oa


class DuplicateOrderBlocked(RuntimeError):
    """Stand-in with the same class name as Trade's ``order_dedupe.DuplicateOrderBlocked``."""


class AmbiguousOrderOutcome(RuntimeError):
    """Stand-in with the same class name as Trade's ``order_dedupe.AmbiguousOrderOutcome``."""


class _SharedClient:
    def __init__(self) -> None:
        self.outcome: object = {"status": "success", "orderid": "OA1"}
        self.calls: list[tuple[str, dict, float]] = []

    def post(self, path: str, payload: dict, *, timeout: float = 30) -> dict:
        self.calls.append((path, dict(payload), timeout))
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return self.outcome  # type: ignore[return-value]


@pytest.fixture
def shared(monkeypatch: pytest.MonkeyPatch) -> _SharedClient:
    client = _SharedClient()
    for name in ("trade_integrations", "trade_integrations.openalgo"):
        if name not in sys.modules:
            monkeypatch.setitem(sys.modules, name, types.ModuleType(name))
    module = types.ModuleType("trade_integrations.openalgo.rest_client")
    module.get_rest_client = lambda host=None, api_key=None: client  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "trade_integrations.openalgo.rest_client", module)
    monkeypatch.setattr(oa._RestClient, "analyzer_status", lambda self: True)

    def _no_raw_order_post(*_args, **_kwargs):
        raise AssertionError("an order went out through the connector's own requests client")

    monkeypatch.setattr(oa.requests, "post", _no_raw_order_post)
    return client


def _cfg() -> oa.OpenAlgoConfig:
    return oa.OpenAlgoConfig(api_key="test-key", host="http://127.0.0.1:5001", profile="paper")


def test_place_order_goes_through_the_shared_order_seam(shared: _SharedClient) -> None:
    result = oa.place_order(_cfg(), symbol="RELIANCE", side="buy", quantity=1)

    assert result["status"] == "ok"
    assert result["order_id"] == "OA1"
    [(path, payload, timeout)] = shared.calls
    assert path == "placeorder"
    assert payload["apikey"] == "test-key"
    assert payload["strategy"] == "vibe_connector"
    assert payload["action"] == "BUY"
    assert timeout == _cfg().timeout


def test_cancel_order_goes_through_the_shared_order_seam(shared: _SharedClient) -> None:
    result = oa.cancel_order(_cfg(), "OA1", symbol="RELIANCE")

    assert result["status"] == "ok"
    [(path, payload, _timeout)] = shared.calls
    assert path == "cancelorder"
    assert payload["orderid"] == "OA1"
    assert payload["apikey"] == "test-key"


@pytest.mark.parametrize(
    ("exc", "code"),
    [
        (DuplicateOrderBlocked("Blocked a duplicate placeorder (D37)"), "duplicate_order_blocked"),
        (AmbiguousOrderOutcome("OpenAlgo placeorder outcome unknown"), "order_outcome_unknown"),
    ],
)
def test_a_d37_refusal_keeps_its_message_and_gets_a_code(shared: _SharedClient, exc: Exception, code: str) -> None:
    shared.outcome = exc
    result = oa.place_order(_cfg(), symbol="RELIANCE", side="buy", quantity=1)

    assert result == {"status": "error", "error": str(exc), "code": code}


def test_any_other_order_failure_has_no_code(shared: _SharedClient) -> None:
    shared.outcome = RuntimeError("Invalid OpenAlgo API key")
    result = oa.cancel_order(_cfg(), "OA1", symbol="RELIANCE")

    assert result == {"status": "error", "error": "Invalid OpenAlgo API key"}
