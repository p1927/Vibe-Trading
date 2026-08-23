"""Tests for VibeTrading's global-markets proxy routes (`/trade/markets/...`),
which front `StockSimulatorClient`'s multi-country read methods
(`get_market_index_history`/`get_policy_factors`/`get_flow_of_funds`/
`get_market_factor_coverage`/`get_live_market_spot`) the same way
`test_trade_routes_replay.py` covers the replay-control routes: no network,
`requests.request` stubbed.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import api_server


def _client() -> TestClient:
    return TestClient(api_server.app, client=("127.0.0.1", 50000))


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict[str, Any]) -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = str(payload)

    def json(self) -> dict[str, Any]:
        return self._payload


@pytest.fixture(autouse=True)
def _token(monkeypatch):
    monkeypatch.setenv("SIMULATOR_CONTROL_TOKEN", "test-shared-secret")
    monkeypatch.setenv("STOCK_SIMULATOR_URL", "http://127.0.0.1:8902")
    yield


def test_markets_registry_lists_all_supported_markets() -> None:
    res = _client().get("/trade/markets/registry")
    assert res.status_code == 200
    body = res.json()
    codes = {m["code"] for m in body["markets"]}
    assert codes == {"IN", "US", "CN", "JP", "RU", "ME", "LATAM"}
    us = next(m for m in body["markets"] if m["code"] == "US")
    assert us["indices"] == ["SPX", "NASDAQ", "DOW"]
    assert us["currency"] == "USD"


def test_markets_registry_does_not_require_simulator_token(monkeypatch) -> None:
    """The registry is static local data — no reason to fail-closed on a
    missing control token the way the proxy reads below do."""
    monkeypatch.delenv("SIMULATOR_CONTROL_TOKEN", raising=False)
    res = _client().get("/trade/markets/registry")
    assert res.status_code == 200


def test_market_index_history_returns_503_without_token(monkeypatch) -> None:
    monkeypatch.delenv("SIMULATOR_CONTROL_TOKEN", raising=False)
    res = _client().get("/trade/markets/US/index/SPX")
    assert res.status_code == 503
    assert "SIMULATOR_CONTROL_TOKEN" in res.json()["detail"]


def test_market_index_history_forwards_country_index_and_period() -> None:
    captured: dict[str, Any] = {}

    def fake_request(method, url, json=None, params=None, headers=None, timeout=None):
        captured["method"] = method
        captured["url"] = url
        captured["params"] = params
        captured["headers"] = headers
        return _FakeResponse(200, {"status": "ok", "data": {"rows": [{"date": "2026-08-20", "close": 5500.1}]}})

    with patch("requests.request", side_effect=fake_request):
        res = _client().get("/trade/markets/US/index/SPX", params={"period": "6mo"})

    assert res.status_code == 200
    assert captured["method"] == "GET"
    assert captured["url"].endswith("/history/US/index/SPX")
    assert captured["params"] == {"period": "6mo"}
    assert captured["headers"] == {"X-Simulator-Control-Token": "test-shared-secret"}
    assert res.json()["data"]["rows"][0]["close"] == 5500.1


def test_market_index_history_propagates_unknown_index_as_400() -> None:
    def fake_request(method, url, json=None, params=None, headers=None, timeout=None):
        return _FakeResponse(400, {"detail": "No index 'BOGUS' registered for market 'US'"})

    with patch("requests.request", side_effect=fake_request):
        res = _client().get("/trade/markets/US/index/BOGUS")

    assert res.status_code == 400


def test_market_live_spot_forwards_country_and_index() -> None:
    captured: dict[str, Any] = {}

    def fake_request(method, url, json=None, params=None, headers=None, timeout=None):
        captured["url"] = url
        captured["params"] = params
        return _FakeResponse(200, {"status": "ok", "data": {"series": "JP:NIKKEI225", "value": 38000.5}})

    with patch("requests.request", side_effect=fake_request):
        res = _client().get("/trade/markets/JP/live_spot/NIKKEI225")

    assert res.status_code == 200
    assert captured["url"].endswith("/history/JP/live_spot/NIKKEI225")
    assert res.json()["data"]["series"] == "JP:NIKKEI225"


def test_market_policy_factors_propagates_not_sourced_as_404() -> None:
    def fake_request(method, url, json=None, params=None, headers=None, timeout=None):
        return _FakeResponse(404, {"detail": "no source configured for RU:bond_10y"})

    with patch("requests.request", side_effect=fake_request):
        res = _client().get("/trade/markets/RU/factors/bond_10y")

    assert res.status_code == 404


def test_market_sector_indices_forwards_country() -> None:
    captured: dict[str, Any] = {}

    def fake_request(method, url, json=None, params=None, headers=None, timeout=None):
        captured["url"] = url
        return _FakeResponse(200, {"status": "ok", "data": [{"name": "SPX", "label": "S&P 500 Index", "kind": "headline"}]})

    with patch("requests.request", side_effect=fake_request):
        res = _client().get("/trade/markets/US/sector_indices")

    assert res.status_code == 200
    assert captured["url"].endswith("/history/US/sector_indices")
    assert res.json()["data"][0]["name"] == "SPX"


def test_market_top_constituents_forwards_country_and_top_n() -> None:
    captured: dict[str, Any] = {}

    def fake_request(method, url, json=None, params=None, headers=None, timeout=None):
        captured["url"] = url
        captured["params"] = params
        return _FakeResponse(200, {"status": "ok", "data": [{"symbol": "NSE:RELIANCE", "name": "RELIANCE"}]})

    with patch("requests.request", side_effect=fake_request):
        res = _client().get("/trade/markets/IN/top_constituents", params={"top_n": 5})

    assert res.status_code == 200
    assert captured["url"].endswith("/history/IN/top_constituents")
    assert captured["params"] == {"top_n": 5}
    assert res.json()["data"][0]["symbol"] == "NSE:RELIANCE"


def test_market_top_constituents_propagates_not_sourced_as_404() -> None:
    def fake_request(method, url, json=None, params=None, headers=None, timeout=None):
        return _FakeResponse(404, {"detail": "no constituent ranking source for market 'US'"})

    with patch("requests.request", side_effect=fake_request):
        res = _client().get("/trade/markets/US/top_constituents")

    assert res.status_code == 404


def test_market_flow_of_funds_forwards_country_and_series() -> None:
    captured: dict[str, Any] = {}

    def fake_request(method, url, json=None, params=None, headers=None, timeout=None):
        captured["url"] = url
        return _FakeResponse(200, {"status": "ok", "data": {"rows": []}})

    with patch("requests.request", side_effect=fake_request):
        res = _client().get("/trade/markets/CN/flow/stock_connect_net")

    assert res.status_code == 200
    assert captured["url"].endswith("/history/CN/flow/stock_connect_net")


def test_market_factor_coverage_reads_from_the_service() -> None:
    def fake_request(method, url, json=None, params=None, headers=None, timeout=None):
        return _FakeResponse(200, {"status": "ok", "data": {"sourced": [], "not_sourced": []}})

    with patch("requests.request", side_effect=fake_request):
        res = _client().get("/trade/markets/factor_coverage")

    assert res.status_code == 200
    assert res.json()["data"] == {"sourced": [], "not_sourced": []}


def test_market_replay_calendar_forwards_country() -> None:
    captured: dict[str, Any] = {}

    def fake_request(method, url, json=None, params=None, headers=None, timeout=None):
        captured["url"] = url
        return _FakeResponse(200, {"status": "ok", "days": [{"date": "2024-05-01", "has_spx": True}], "indices": ["SPX"]})

    with patch("requests.request", side_effect=fake_request):
        res = _client().get("/trade/markets/US/replay/calendar")

    assert res.status_code == 200
    assert captured["url"].endswith("/history/US/replay/calendar")
    assert res.json()["days"][0]["has_spx"] is True


def test_market_backfill_forwards_country_index_and_period() -> None:
    captured: dict[str, Any] = {}

    def fake_request(method, url, json=None, params=None, headers=None, timeout=None):
        captured["method"] = method
        captured["url"] = url
        captured["json"] = json
        return _FakeResponse(200, {"status": "ok", "results": [{"country": "US", "index": "SPX", "written": 3}]})

    with patch("requests.request", side_effect=fake_request):
        res = _client().post("/trade/markets/backfill", json={"country": "US", "index": "SPX"})

    assert res.status_code == 200
    assert captured["method"] == "POST"
    assert captured["url"].endswith("/tick_recording/backfill")
    assert captured["json"] == {"country": "US", "index": "SPX", "period": "max"}
    assert res.json()["results"][0]["written"] == 3


# ============================================================
# global_macro proxy — currencies (usd_inr/usd_cny/...) and global factors
# (gold/oil/vix/us_10y), fronting `/history/global_macro` and
# `/history/live_macro_spot` rather than the per-country `/markets/{country}/...`
# dispatch above (these series aren't owned by any one market).
# ============================================================

def test_market_global_macro_forwards_series_and_filters() -> None:
    captured: dict[str, Any] = {}

    def fake_request(method, url, json=None, params=None, headers=None, timeout=None):
        captured["url"] = url
        captured["params"] = params
        return _FakeResponse(200, {"status": "ok", "data": [{"day": "2026-08-20", "series": "usd_inr", "field": "rate", "value": 87.1, "source": "yfinance_eod_refresh"}]})

    with patch("requests.request", side_effect=fake_request):
        res = _client().get("/trade/markets/global_macro/usd_inr", params={"field": "rate"})

    assert res.status_code == 200
    assert captured["url"].endswith("/history/global_macro")
    assert captured["params"]["series"] == "usd_inr"
    assert captured["params"]["field"] == "rate"
    assert res.json()["data"][0]["value"] == 87.1


def test_market_global_macro_live_spot_forwards_series() -> None:
    captured: dict[str, Any] = {}

    def fake_request(method, url, json=None, params=None, headers=None, timeout=None):
        captured["url"] = url
        captured["params"] = params
        return _FakeResponse(200, {"status": "ok", "data": {"series": "gold", "value": 3400.5, "source": "yfinance_live_spot", "fetched_at": "2026-08-23T00:00:00+00:00", "stale": False}})

    with patch("requests.request", side_effect=fake_request):
        res = _client().get("/trade/markets/global_macro/gold/live_spot")

    assert res.status_code == 200
    assert captured["url"].endswith("/history/live_macro_spot")
    assert captured["params"]["series"] == "gold"
    assert res.json()["data"]["value"] == 3400.5


def test_market_global_macro_returns_503_without_token(monkeypatch) -> None:
    monkeypatch.delenv("SIMULATOR_CONTROL_TOKEN", raising=False)
    res = _client().get("/trade/markets/global_macro/usd_inr")
    assert res.status_code == 503


def test_market_global_macro_refresh_forwards_series_and_lookback() -> None:
    captured: dict[str, Any] = {}

    def fake_request(method, url, json=None, params=None, headers=None, timeout=None):
        captured["method"] = method
        captured["url"] = url
        captured["params"] = params
        return _FakeResponse(200, {"status": "ok", "data": {"status": "ok", "series": "usd_inr", "rows": 91, "new_rows": 91}})

    with patch("requests.request", side_effect=fake_request):
        res = _client().post("/trade/markets/global_macro/usd_inr/refresh", params={"lookback_days": 30})

    assert res.status_code == 200
    assert captured["method"] == "POST"
    assert captured["url"].endswith("/history/global_macro/usd_inr/refresh")
    assert captured["params"] == {"lookback_days": 30}
    assert res.json()["data"]["rows"] == 91


def test_market_global_macro_refresh_propagates_unsupported_series_as_400() -> None:
    def fake_request(method, url, json=None, params=None, headers=None, timeout=None):
        return _FakeResponse(400, {"detail": "EOD refresh not supported for series 'bogus'"})

    with patch("requests.request", side_effect=fake_request):
        res = _client().post("/trade/markets/global_macro/bogus/refresh")

    assert res.status_code == 400


def test_market_global_macro_refreshable_series_lists_series() -> None:
    def fake_request(method, url, json=None, params=None, headers=None, timeout=None):
        return _FakeResponse(200, {"status": "ok", "series": ["gold", "oil_brent_daily", "sp500", "usd_inr"]})

    with patch("requests.request", side_effect=fake_request):
        res = _client().get("/trade/markets/global_macro/refreshable_series")

    assert res.status_code == 200
    assert "usd_inr" in res.json()["series"]


def test_market_global_macro_refreshable_series_not_swallowed_as_series_path_param() -> None:
    """Regression guard: `refreshable_series` must route to its own handler, not to
    `GET /markets/global_macro/{series}` with series="refreshable_series" — this only works
    because the static route is registered before the `{series}` route in trade_routes.py."""
    captured: dict[str, Any] = {}

    def fake_request(method, url, json=None, params=None, headers=None, timeout=None):
        captured["url"] = url
        return _FakeResponse(200, {"status": "ok", "series": []})

    with patch("requests.request", side_effect=fake_request):
        res = _client().get("/trade/markets/global_macro/refreshable_series")

    assert res.status_code == 200
    assert captured["url"].endswith("/history/global_macro/refreshable_series")
