import json
import os
from types import SimpleNamespace

import pandas as pd
import pytest

from backtest.loaders.vendor_payloads import validate_payload

CASES = [
    ("https://datacenter-web.eastmoney.com/api/data/v1/get", {"result": {"data": []}}),
    ("https://searchapi.eastmoney.com/api/suggest/get", {"QuotationCodeTable": {"Data": []}}),
    ("https://reportapi.eastmoney.com/report/list", {"data": []}),
    ("https://search-api-web.eastmoney.com/search/jsonp", {"result": {"cmsArticleWebOld": []}}),
    ("https://push2his.eastmoney.com/api/qt/stock/kline/get", {"data": {"klines": []}}),
    ("https://push2.eastmoney.com/api/qt/clist/get", {"data": {"diff": []}}),
    ("https://www.iwencai.com/customized/chart/get-robot-data", {"data": {"answer": []}}),
    ("https://gamma-api.polymarket.com/public-search", {"events": []}),
    ("https://query1.finance.yahoo.com/v1/finance/search", {"quotes": []}),
]


@pytest.mark.parametrize("url,empty", CASES)
def test_confirmed_empty_is_accepted(url, empty):
    assert validate_payload(empty, url=url, host_key="fixture") is empty


@pytest.mark.parametrize("url,empty", CASES)
@pytest.mark.parametrize("body", [{"success": False}, {"error": "authentication failed"}, {}])
def test_vendor_failure_or_missing_layout_never_becomes_empty(url, empty, body):
    with pytest.raises(ValueError):
        validate_payload(body, url=url, host_key="fixture")


def test_india_auth_failure_remains_named_error(monkeypatch):
    from src.tools import india_options_chain_tool as module
    from trade_integrations.dataflows.stock_history_bridge import _openalgo_client as client

    monkeypatch.setattr(module, "_resolve_india_symbol", lambda _: ("NIFTY", "NSE"))

    def fail(*args, **kwargs):
        raise client.OpenAlgoError("authentication expired")

    monkeypatch.setattr(client.OpenAlgoClient, "get_option_chain", fail)
    result = json.loads(module.IndiaOptionsChainTool().execute(ticker="NIFTY", source="openalgo"))
    assert result["ok"] is False
    assert "authentication expired" in result["error"]
    assert "no openalgo option chain available" not in result["error"]


@pytest.mark.parametrize("code", ['600519") OR 1=1', "600519.BAD", "abc", "600519.SH.EXTRA"])
def test_filter_input_rejected(code):
    from src.tools.dragon_tiger_tool import _bare_code
    from src.tools.financial_statements_tool import _eastmoney_filter

    with pytest.raises(ValueError):
        _bare_code(code)
    with pytest.raises(ValueError):
        _eastmoney_filter("a", code, "1.600519")


def test_old_cache_is_refetched_without_unpickling_and_degraded_not_cached(monkeypatch, tmp_path):
    from src.tools import alpha_bench_tool as module
    import src.config.paths as paths

    monkeypatch.setattr(paths, "get_runtime_root", lambda: tmp_path)
    cache = tmp_path / "cache" / "fixture_2020-01-01_2021-12-31.pkl"
    cache.parent.mkdir()
    cache.write_bytes(b"stale blob")
    os.utime(cache, (1, 1))
    calls = []
    panel = {"close": pd.DataFrame({"a": [1.0]}), "_meta": {"degraded": True}}
    monkeypatch.setitem(
        module.UNIVERSE_REGISTRY, "fixture", SimpleNamespace(panel_loader=lambda *a: calls.append(a) or panel)
    )
    monkeypatch.setattr(module, "_read_pickle_cache", lambda _: pytest.fail("expired cache was unpickled"))
    monkeypatch.setattr(module, "_write_pickle_cache", lambda *a: pytest.fail("degraded panel was cached"))
    assert module._load_universe_panel("fixture", "2020-2021") is panel
    assert len(calls) == 1


@pytest.mark.parametrize("top", ["junk", float("inf"), float("nan"), None, 0, True, False, 1.5])
def test_model_top_returns_error_envelope(top):
    from src.tools.alpha_bench_tool import run_alpha_bench

    result = run_alpha_bench(universe="sp500", period="2020-2021", top=top)
    assert result["status"] == "error" and "top" in result["error"]


def test_invalid_model_filter_returns_error_without_vendor_or_fallback(monkeypatch):
    from src.tools import dragon_tiger_tool as dragon
    from src.tools import financial_statements_tool as statements

    monkeypatch.setattr(
        dragon.eastmoney_client, "get_json", lambda *a, **k: pytest.fail("invalid filter reached vendor")
    )
    monkeypatch.setattr(
        dragon.tushare_fallbacks, "fetch_dragon_tiger", lambda *a, **k: pytest.fail("invalid filter reached fallback")
    )
    result = json.loads(dragon.DragonTigerTool().execute(date="2026-01-01", code='600519".SH'))
    assert not result["ok"]
    monkeypatch.setattr(statements, "resolve_secid", lambda _: "1.600519")
    monkeypatch.setattr(statements, "get_json", lambda *a, **k: pytest.fail("invalid filter reached vendor"))
    result = json.loads(statements.FinancialStatementsTool().execute(code='600519".SH'))
    assert not result["ok"]
