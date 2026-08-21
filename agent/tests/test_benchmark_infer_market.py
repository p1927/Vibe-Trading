"""Tests for backtest/benchmark.py's ``_infer_market`` helper."""

from __future__ import annotations

from backtest.benchmark import _infer_market


class TestInferMarket:
    def test_us_equity(self):
        assert _infer_market(["AAPL.US"], "yahoo") == "us_equity"

    def test_hk_equity(self):
        assert _infer_market(["0700.HK"], "yahoo") == "hk_equity"

    def test_ca_equity(self):
        assert _infer_market(["TD.TO"], "yahoo") == "ca_equity"

    def test_india_equity_ns_suffix(self):
        assert _infer_market(["RELIANCE.NS"], "yahoo") == "india_equity"

    def test_india_equity_bo_suffix(self):
        assert _infer_market(["TCS.BO"], "yahoo") == "india_equity"

    def test_kr_equity(self):
        assert _infer_market(["005930.KS"], "yahoo") == "kr_equity"

    def test_empty_codes_defaults_to_us_equity(self):
        assert _infer_market([], "yahoo") == "us_equity"
