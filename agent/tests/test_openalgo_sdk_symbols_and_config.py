"""OpenAlgo connector: one symbol authority, and the config the caller passed is the one used.

* ``_resolve_india`` kept a hand copy of Trade's alias table as a silent ``except Exception``
  fallback; it lacked the registry's NSE renames (``TATAMOTORS -> TMPV``).
* ``get_historical_bars`` handed US symbols to ``_us_history``, which re-loaded the saved config
  instead of using the ``config`` argument (wrong host / key / profile).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.trading.connectors.openalgo import sdk as oa


@pytest.fixture(autouse=True)
def _offline_market_detection(monkeypatch: pytest.MonkeyPatch) -> None:
    """``detect_market`` probes OpenAlgo's search for a plain ticker (30 s of retries when it is
    down). The suffix / index guards and the config plumbing are what is under test here."""
    from trade_integrations.dataflows.company_research import market

    monkeypatch.setattr(market, "detect_market", lambda raw: market.Market.US if raw == "AAPL" else market.Market.IN)


def test_resolver_is_the_registry() -> None:
    assert oa._resolve_india("NIFTY") == ("NIFTY", "NSE_INDEX")
    assert oa._resolve_india("^NSEI") == ("NIFTY", "NSE_INDEX")
    assert oa._resolve_india("RELIANCE.NS") == ("RELIANCE", "NSE")


def test_resolver_applies_the_registry_nse_renames() -> None:
    """The deleted fallback table did not know renames (TATAMOTORS -> TMPV)."""
    assert oa._resolve_india("TATAMOTORS") == ("TMPV", "NSE")


def test_resolver_failure_is_not_masked(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(_symbol):
        raise RuntimeError("registry down")

    monkeypatch.setattr("trade_integrations.openalgo.symbols.resolve_openalgo_symbol", broken)
    with pytest.raises(RuntimeError):
        oa._resolve_india("NIFTY")


@pytest.mark.parametrize(
    "symbol, expected",
    [("AAPL", True), ("RELIANCE", False), ("RELIANCE.NS", False), ("NIFTY", False), ("^NSEI", False), ("BANKNIFTY", False)],
)
def test_us_symbol_detection(symbol: str, expected: bool) -> None:
    assert oa._is_us_symbol(symbol) is expected


def test_unmappable_symbol_is_an_error_envelope_not_a_raise() -> None:
    cfg = oa.OpenAlgoConfig(api_key="k", profile="paper")
    assert oa.get_quote("^NOSUCHINDEX", config=cfg)["status"] == "error"
    assert oa.get_historical_bars("^NOSUCHINDEX", config=cfg)["status"] == "error"
    assert oa.place_order(cfg, symbol="^NOSUCHINDEX", side="buy", quantity=1)["status"] == "error"


def test_us_history_uses_the_config_it_was_given(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[oa.OpenAlgoConfig] = []

    class _Client:
        def __init__(self, cfg):
            seen.append(cfg)

        def post(self, path, payload):
            return {"data": [{"timestamp": "2026-01-01", "open": 1, "high": 2, "low": 0.5, "close": 1.5, "volume": 10}]}

    monkeypatch.setattr(oa, "_client", lambda cfg: _Client(cfg))
    monkeypatch.setattr(oa, "load_config", lambda: pytest.fail("must not re-load the saved config"))
    cfg = oa.OpenAlgoConfig(host="http://example.invalid:1", api_key="k", profile="paper")

    out = oa.get_historical_bars("AAPL", config=cfg, period="1d", limit=5)

    assert out["status"] == "ok" and out["market"] == "US" and len(out["bars"]) == 1
    assert seen == [cfg]


@pytest.mark.parametrize("data", [None, {}, "oops", {"message": "x"}])
def test_unrecognised_book_reply_is_an_error_not_an_empty_book(monkeypatch: pytest.MonkeyPatch, data) -> None:
    """A positionbook/orderbook reply with no records list used to read as a flat, order-free account."""
    from src.trading.types import ConnectorVendorError

    cfg = oa.OpenAlgoConfig(api_key="k", host="http://127.0.0.1:5001")
    client = oa._RestClient(cfg)
    monkeypatch.setattr(client, "post", lambda path, payload: {"status": "success", "data": data})
    with pytest.raises(ConnectorVendorError):
        client.positions()
    with pytest.raises(ConnectorVendorError):
        client.orders()


def test_recognised_book_replies_still_parse(monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = oa.OpenAlgoConfig(api_key="k", host="http://127.0.0.1:5001")
    client = oa._RestClient(cfg)
    monkeypatch.setattr(client, "post", lambda path, payload: {"status": "success", "data": [{"symbol": "X"}]})
    assert client.positions() == [{"symbol": "X"}]
    monkeypatch.setattr(client, "post", lambda path, payload: {"status": "success", "data": {"orders": []}})
    assert client.orders() == []
