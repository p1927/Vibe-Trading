"""Tests for the eToro Public API connector."""

from __future__ import annotations

import json
from typing import Any

import pytest

from src.live import registry
from src.live.classification import ToolClass, classify_tool
from src.trading import profiles, service
from src.trading.connectors.etoro import sdk as etoro_sdk
from src.trading.connectors.etoro.client import (
    EtoroClient,
    EtoroConfig,
    execution_root,
    execution_v1_root,
    info_root,
    copy_root,
    positions_root,
    set_client_factory,
)
from src.trading.connectors.etoro.classification import ETORO_TOOL_CLASS

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _reset_etoro_client_factory() -> None:
    yield
    set_client_factory(None)


class _FakeResponse:
    def __init__(self, status_code: int, payload: Any = None, headers: dict[str, str] | None = None):
        self.status_code = status_code
        self._payload = payload
        self.headers = headers or {}
        self.content = b"{}" if payload is not None else b""

    def json(self) -> Any:
        return self._payload


def _transport_factory(responses: dict[tuple[str, str], _FakeResponse]):
    def _transport(method: str, url: str, **kwargs: Any) -> _FakeResponse:
        key = (method.upper(), url.split("public-api.etoro.com", 1)[-1])
        if key not in responses:
            raise AssertionError(f"unexpected request {key}")
        return responses[key]

    return _transport


def test_etoro_profiles_registered() -> None:
    ids = {profile.id for profile in profiles.list_profiles()}
    assert {
        "etoro-paper-sdk",
        "etoro-paper-trade",
        "etoro-live-sdk-readonly",
        "etoro-live-trade",
    } <= ids

    paper = profiles.profile_by_id("etoro-paper-trade")
    live = profiles.profile_by_id("etoro-live-trade")
    assert paper.connector == "etoro"
    assert live.connector == "etoro"
    assert paper.environment == "paper"
    assert live.environment == "live"
    assert "orders.place" in paper.capabilities
    assert "orders.place.requires_mandate" in live.capabilities


def test_demo_and_real_paths_separated() -> None:
    paper = EtoroConfig(profile="paper", api_key="k", user_key="u")
    live = EtoroConfig(profile="live", api_key="k", user_key="u")
    assert execution_root(paper) == "/api/v2/trading/execution/demo"
    assert execution_root(live) == "/api/v2/trading/execution"
    assert execution_v1_root(paper).endswith("/demo")
    assert "/demo" not in execution_v1_root(live)
    assert info_root(paper).endswith("/demo")
    assert copy_root(paper).endswith("/demo")
    assert positions_root(paper).endswith("/demo")


def test_every_request_has_unique_request_id() -> None:
    seen: list[str] = []
    cfg = EtoroConfig(profile="paper", api_key="k", user_key="u")

    def _transport(method: str, url: str, **kwargs: Any) -> _FakeResponse:
        seen.append(kwargs["headers"]["x-request-id"])
        return _FakeResponse(200, {})

    client = EtoroClient(cfg, transport=_transport)
    client.request("GET", "/api/v1/market-data/search", params={"search": "AAPL"}, allow_retry=True)
    client.request("GET", "/api/v1/market-data/search", params={"search": "MSFT"}, allow_retry=True)
    assert len(seen) == 2
    assert seen[0] != seen[1]


def test_credentials_redacted_in_public_config() -> None:
    cfg = EtoroConfig(profile="paper", api_key="secret-key", user_key="secret-user")
    from src.trading.connectors.etoro.client import public_config

    payload = public_config(cfg)
    assert "secret-key" not in json.dumps(payload)
    assert payload["api_key_configured"] is True


def test_place_order_uses_demo_execution_path(monkeypatch) -> None:
    cfg = EtoroConfig(profile="paper", api_key="k", user_key="u", readonly=False)
    captured: dict[str, Any] = {}

    def _transport(method: str, url: str, **kwargs: Any) -> _FakeResponse:
        captured["method"] = method
        captured["url"] = url
        captured["json"] = kwargs.get("json")
        return _FakeResponse(200, {"orderID": 42})

    monkeypatch.setattr("src.trading.connectors.etoro.trading.resolve_instrument_id", lambda symbol, c: 1001)
    client = EtoroClient(cfg, transport=_transport)
    set_client_factory(lambda c: client)

    result = etoro_sdk.place_order(
        cfg,
        symbol="AAPL",
        side="buy",
        notional=100.0,
        request_id="fixed-request-id",
    )
    assert result["status"] == "ok"
    assert "/execution/demo/orders" in captured["url"]
    assert captured["json"]["instrumentId"] == 1001
    assert captured["method"] == "POST"


def test_classification_registered() -> None:
    curated = registry._BROKER_CURATED_MAPS["etoro"]
    assert classify_tool("get_positions", None, curated) is ToolClass.READ
    assert classify_tool("place_order", None, curated) is ToolClass.WRITE
    assert classify_tool("unknown_etoro_op", None, curated) is ToolClass.UNKNOWN
    assert ETORO_TOOL_CLASS["copy_close"] is ToolClass.WRITE
    assert ETORO_TOOL_CLASS["get_instrument_metadata"] is ToolClass.READ


def test_resolve_btc_uses_internal_symbol_full(monkeypatch) -> None:
    cfg = EtoroConfig(profile="paper", api_key="k", user_key="u")
    captured_params: list[dict[str, Any]] = []

    def _transport(method: str, url: str, **kwargs: Any) -> _FakeResponse:
        if "/market-data/search" in url:
            captured_params.append(dict(kwargs.get("params") or {}))
            if kwargs.get("params", {}).get("internalSymbolFull") == "BTC":
                return _FakeResponse(
                    200,
                    {
                        "items": [
                            {"instrumentId": 100134, "internalSymbolFull": "BTCA"},
                            {"instrumentId": 100000, "internalSymbolFull": "BTC", "internalInstrumentDisplayName": "Bitcoin"},
                        ],
                    },
                )
        raise AssertionError(f"unexpected request {method} {url}")

    set_client_factory(lambda c: EtoroClient(cfg, transport=_transport))
    from src.trading.connectors.etoro.instruments import resolve_instrument_id

    assert resolve_instrument_id("BTC", cfg) == 100000
    assert resolve_instrument_id("Bitcoin", cfg) == 100000
    assert any(p.get("internalSymbolFull") == "BTC" for p in captured_params)


def test_fuzzy_search_filters_sentinel_ids(monkeypatch) -> None:
    cfg = EtoroConfig(profile="paper", api_key="k", user_key="u")

    def _transport(method: str, url: str, **kwargs: Any) -> _FakeResponse:
        if kwargs.get("params", {}).get("search"):
            return _FakeResponse(
                200,
                {"items": [{"instrumentId": -100000}, {"instrumentId": 100000, "internalSymbolFull": "BTC"}]},
            )
        return _FakeResponse(200, {"items": []})

    set_client_factory(lambda c: EtoroClient(cfg, transport=_transport))
    from src.trading.connectors.etoro.instruments import search_instruments

    result = search_instruments("bitcoin", cfg, limit=5, mode="discover")
    assert result["status"] == "ok"
    assert result["instruments"][0]["instrument_id"] == 100000


def test_get_open_orders_reads_portfolio_orders(monkeypatch) -> None:
    cfg = EtoroConfig(profile="paper", api_key="k", user_key="u")

    def _transport(method: str, url: str, **kwargs: Any) -> _FakeResponse:
        assert method == "GET"
        assert info_root(cfg) + "/portfolio" in url
        return _FakeResponse(
            200,
            {
                "clientPortfolio": {
                    "ordersForOpen": [{"orderID": 99, "instrumentID": 100000}],
                    "ordersForClose": [],
                }
            },
        )

    set_client_factory(lambda c: EtoroClient(cfg, transport=_transport))
    from src.trading.connectors.etoro import sdk as etoro_sdk

    result = etoro_sdk.get_open_orders(cfg)
    assert result["status"] == "ok"
    assert result["orders"][0]["order_id"] == 99
    assert result["orders"][0]["order_kind"] == "ordersForOpen"


def test_service_dispatches_positions(monkeypatch) -> None:
    cfg = EtoroConfig(profile="paper", api_key="k", user_key="u")

    def _transport(method: str, url: str, **kwargs: Any) -> _FakeResponse:
        assert method == "GET"
        assert info_root(cfg) + "/portfolio" in url
        return _FakeResponse(200, {"positions": [{"positionID": 1, "instrumentDisplayName": "AAPL"}]})

    set_client_factory(lambda c: EtoroClient(cfg, transport=_transport))
    result = service.get_positions("etoro-paper-sdk", api_key="k", user_key="u")
    assert result["status"] == "ok"
    assert result["connector"] == "etoro"
    assert result["positions"][0]["symbol"] == "AAPL"
