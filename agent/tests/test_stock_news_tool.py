"""Tests for the stock-news tool.

No request leaves the process: the Eastmoney HTTP boundary
(:func:`backtest.loaders.eastmoney_client.throttled_get_json`) and the Yahoo
:func:`backtest.loaders.yahoo_client.search_news` helper are mocked so the real
client + tool parsing run fully offline. The Hub read/write functions
(:mod:`trade_integrations.dataflows.news_hub_bridge`) are also mocked — this
tool ingests fetched articles into the Hub and serves the response back from
the Hub rather than the raw vendor payload, per the Hub boundary contract.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import patch

import pytest

from backtest.loaders import eastmoney_client, yahoo_client
from src.tools.stock_news_tool import (
    StockNewsTool,
    _bare_query,
    _clamp_limit,
    _snippet,
    _suffix_of,
)
from trade_integrations.dataflows import news_hub_bridge


def _hub_headline(
    *, title: str, url: str, source: str, published_at: str, summary: str
) -> dict[str, Any]:
    """A minimal hub headline dict, as returned by ``query_with_staging``."""
    return {
        "title": title,
        "url": url,
        "source": source,
        "published_at": published_at,
        "content_summary": summary,
        "summary": summary,
        "sources": [{"vendor": source, "publisher": source, "url": url}],
    }


def _em_news_payload() -> dict[str, Any]:
    """An Eastmoney search payload carrying two CMS articles."""
    return {
        "result": {
            "cmsArticleWebOld": [
                {
                    "title": "贵州茅台一季度净利大增",
                    "url": "https://finance.eastmoney.com/a/1.html",
                    "mediaName": "东方财富",
                    "date": "2024-04-30 08:00:00",
                    "content": "公司披露一季报，营收同比增长 " * 30,
                },
                {
                    "title": "白酒板块全线走强",
                    "url": "https://finance.eastmoney.com/a/2.html",
                    "mediaName": "证券时报",
                    "date": "2024-04-29 18:30:00",
                    "content": "市场情绪回暖",
                },
            ]
        }
    }


def _yahoo_news() -> list[dict[str, Any]]:
    """A Yahoo search result list with two news articles."""
    return [
        {
            "title": "Apple unveils new products",
            "publisher": "Reuters",
            "link": "https://example.com/apple-products",
            "providerPublishTime": 1704067200,
            "summary": "Apple announced a new product lineup. " * 30,
            "relatedTickers": ["AAPL"],
        },
        {
            "title": "Apple shares rise",
            "publisher": "Bloomberg",
            "link": "https://example.com/apple-shares",
            "providerPublishTime": 1704153600,
            "relatedTickers": ["AAPL"],
        },
    ]


class TestHelpers:
    def test_suffix_of(self) -> None:
        assert _suffix_of("600519.SH") == "SH"
        assert _suffix_of("AAPL.US") == "US"
        assert _suffix_of("NOSUFFIX") == ""

    def test_bare_query(self) -> None:
        assert _bare_query("600519.SH") == "600519"
        assert _bare_query(" AAPL.US ") == "AAPL"

    def test_clamp_limit(self) -> None:
        assert _clamp_limit(None) == 20
        assert _clamp_limit("garbage") == 20
        assert _clamp_limit(0) == 1
        assert _clamp_limit(999) == 50
        assert _clamp_limit(5) == 5

    def test_snippet_trims(self) -> None:
        assert _snippet(None) == ""
        long = "x" * 400
        out = _snippet(long)
        assert len(out) <= 281
        assert out.endswith("…")

    def test_search_news_filters_to_dict_items(self, monkeypatch) -> None:
        def fake_get_json(url: str, **kwargs: Any) -> dict[str, Any]:
            assert url == yahoo_client._SEARCH_BASE
            assert kwargs["host_key"] == yahoo_client.HOST_KEY
            assert kwargs["params"] == {"q": "apple", "newsCount": 2}
            return {"news": [_yahoo_news()[0], "garbage", _yahoo_news()[1]]}

        monkeypatch.setattr(yahoo_client, "throttled_get_json", fake_get_json)

        articles = yahoo_client.search_news("apple", 2)

        assert articles == _yahoo_news()


class TestToolContract:
    def test_name_and_schema(self) -> None:
        tool = StockNewsTool()
        assert tool.name == "get_stock_news"
        assert tool.is_readonly is True
        assert tool.parameters["required"] == []
        assert tool.parameters["properties"]["scope"]["enum"] == ["stock", "global"]
        # Description must advertise the shared article contract for every market.
        desc = tool.description.lower()
        assert "yahoo finance news articles" in desc
        assert "matches" not in desc


class TestExecuteSuccess:
    def test_a_share_stock_news(self) -> None:
        tool = StockNewsTool()
        hub_articles = [
            _hub_headline(
                title="贵州茅台一季度净利大增",
                url="https://finance.eastmoney.com/a/1.html",
                source="东方财富",
                published_at="2024-04-30 08:00:00",
                summary="公司披露一季报",
            )
        ]
        with (
            patch.object(
                eastmoney_client, "throttled_get_json", return_value=_em_news_payload()
            ) as http,
            patch.object(news_hub_bridge, "ingest_rows_to_hub") as ingest,
            patch.object(
                news_hub_bridge, "query_with_staging", return_value=hub_articles
            ) as query,
        ):
            out = json.loads(tool.execute(code="600519.SH", scope="stock", limit=10))

        http.assert_called_once()
        _, kwargs = http.call_args
        assert kwargs["host_key"] == "eastmoney"

        # Fetched vendor rows are ingested into the Hub, tagged with the
        # A-share market — never handed straight back to the caller.
        ingest.assert_called_once()
        _, ingest_kwargs = ingest.call_args
        assert ingest_kwargs["ticker"] == "600519"
        assert ingest_kwargs["market"] == "CN"
        assert len(ingest.call_args.args[0]) == 2

        query.assert_called_once_with(ticker="600519", market="CN", limit=10)

        assert out["ok"] is True
        assert out["market"] == "a_share"
        assert out["source"] == "eastmoney"
        assert out["data"]["code"] == "600519.SH"
        assert len(out["data"]["articles"]) == 1
        first = out["data"]["articles"][0]
        assert first["title"] == "贵州茅台一季度净利大增"
        assert first["source"] == "东方财富"

    def test_global_scope_needs_no_code(self) -> None:
        tool = StockNewsTool()
        with (
            patch.object(
                eastmoney_client, "throttled_get_json", return_value=_em_news_payload()
            ),
            patch.object(news_hub_bridge, "ingest_rows_to_hub") as ingest,
            patch.object(
                news_hub_bridge, "query_with_staging", return_value=[]
            ) as query,
        ):
            out = json.loads(tool.execute(scope="global"))

        _, ingest_kwargs = ingest.call_args
        assert ingest_kwargs["ticker"] == "CN_MARKET"
        assert ingest_kwargs["market"] == "CN"
        query.assert_called_once_with(ticker="CN_MARKET", market="CN", limit=20)

        assert out["ok"] is True
        assert out["market"] == "global"
        assert out["source"] == "eastmoney"
        assert out["data"]["scope"] == "global"

    def test_us_stock_via_yahoo_returns_articles(self) -> None:
        tool = StockNewsTool()
        hub_articles = [
            _hub_headline(
                title="Apple unveils new products",
                url="https://example.com/apple-products",
                source="Reuters",
                published_at="2024-01-01 00:00:00",
                summary="Apple announced a new product lineup.",
            )
        ]
        with (
            patch.object(
                yahoo_client, "search_news", return_value=_yahoo_news()
            ) as srch,
            patch.object(news_hub_bridge, "ingest_rows_to_hub") as ingest,
            patch.object(
                news_hub_bridge, "query_with_staging", return_value=hub_articles
            ) as query,
        ):
            out = json.loads(tool.execute(code="AAPL.US", limit=1))

        srch.assert_called_once_with("AAPL", 1)
        _, ingest_kwargs = ingest.call_args
        assert ingest_kwargs["ticker"] == "AAPL"
        assert ingest_kwargs["market"] == "US"
        query.assert_called_once_with(ticker="AAPL", market="US", limit=1)

        assert out["ok"] is True
        assert out["market"] == "us"
        assert out["source"] == "yahoo"
        assert len(out["data"]["articles"]) == 1
        first = out["data"]["articles"][0]
        assert first["title"] == "Apple unveils new products"
        assert first["url"] == "https://example.com/apple-products"
        assert first["source"] == "Reuters"
        assert first["published"] == "2024-01-01 00:00:00"

    def test_hk_stock_via_yahoo_returns_articles(self) -> None:
        tool = StockNewsTool()
        hub_articles = [
            _hub_headline(
                title="Apple unveils new products",
                url="https://example.com/apple-products",
                source="Reuters",
                published_at="2024-01-01 00:00:00",
                summary="Apple announced a new product lineup.",
            )
        ]
        with (
            patch.object(
                yahoo_client, "search_news", return_value=_yahoo_news()[:1]
            ) as srch,
            patch.object(news_hub_bridge, "ingest_rows_to_hub") as ingest,
            patch.object(
                news_hub_bridge, "query_with_staging", return_value=hub_articles
            ) as query,
        ):
            out = json.loads(tool.execute(code="00700.HK"))

        srch.assert_called_once_with("00700", 20)
        _, ingest_kwargs = ingest.call_args
        assert ingest_kwargs["ticker"] == "00700"
        assert ingest_kwargs["market"] == "HK"
        query.assert_called_once_with(ticker="00700", market="HK", limit=20)

        assert out["ok"] is True
        assert out["market"] == "hk"
        assert out["source"] == "yahoo"
        assert out["data"]["articles"][0]["title"] == "Apple unveils new products"

    def test_yahoo_empty_news_returns_empty_articles(self) -> None:
        with (
            patch.object(yahoo_client, "search_news", return_value=[]),
            patch.object(news_hub_bridge, "ingest_rows_to_hub") as ingest,
            patch.object(news_hub_bridge, "query_with_staging", return_value=[]),
        ):
            out = json.loads(StockNewsTool().execute(code="AAPL.US"))

        # No fetched articles -> nothing to ingest.
        ingest.assert_not_called()
        assert out["ok"] is True
        assert out["data"]["articles"] == []

    def test_yahoo_limit_is_clamped_before_request(self) -> None:
        with (
            patch.object(yahoo_client, "search_news", return_value=[]) as srch,
            patch.object(news_hub_bridge, "ingest_rows_to_hub"),
            patch.object(
                news_hub_bridge, "query_with_staging", return_value=[]
            ) as query,
        ):
            out = json.loads(StockNewsTool().execute(code="AAPL.US", limit=999))

        assert out["ok"] is True
        srch.assert_called_once_with("AAPL", 50)
        query.assert_called_once_with(ticker="AAPL", market="US", limit=50)


class TestExecuteError:
    def test_missing_code_when_stock_scope(self) -> None:
        out = json.loads(StockNewsTool().execute(scope="stock"))
        assert out["ok"] is False
        assert "code" in out["error"]

    def test_invalid_scope(self) -> None:
        out = json.loads(StockNewsTool().execute(scope="weird"))
        assert out["ok"] is False
        assert "invalid scope" in out["error"]

    def test_unsupported_market(self) -> None:
        out = json.loads(StockNewsTool().execute(code="BTC-USDT"))
        assert out["ok"] is False
        assert "unsupported market" in out["error"]

    def test_eastmoney_http_failure_envelope(self) -> None:
        tool = StockNewsTool()
        with patch.object(
            eastmoney_client,
            "throttled_get_json",
            side_effect=RuntimeError("eastmoney banned"),
        ):
            out = json.loads(tool.execute(code="600519.SH"))

        assert out["ok"] is False
        assert "eastmoney banned" in out["error"]

    def test_yahoo_failure_envelope(self) -> None:
        tool = StockNewsTool()
        with patch.object(
            yahoo_client, "search_news", side_effect=RuntimeError("yahoo 429")
        ):
            out = json.loads(tool.execute(code="AAPL.US"))

        assert out["ok"] is False
        assert "yahoo 429" in out["error"]
        assert "yahoo news fetch failed" in out["error"]
