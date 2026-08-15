"""Unit tests for the Hub-backed event/sentiment provider (no network).

The Hub read function (:func:`news_hub_bridge.query_with_staging`) is mocked
so these tests exercise the provider's mapping/point-in-time logic fully
offline, mirroring the mocking style in ``test_stock_news_tool.py``.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pandas as pd
import pytest

from backtest.loaders.rsshub_events import (
    DEFAULT_FEEDS,
    EVENT_COLUMNS,
    EventProviderError,
    FeedSpec,
    RSSHubEventProvider,
    UnknownFeedError,
    default_lexicon_scorer,
    feed_specs_from_config,
    format_code_for_route,
)
from trade_integrations.dataflows import news_hub_bridge


def _hub_item(
    *,
    title: str,
    summary: str = "",
    published_at: str,
    event_kind: str = "",
    source: str = "vendor",
) -> dict[str, Any]:
    """A minimal hub headline dict, as returned by ``query_with_staging``."""
    return {
        "title": title,
        "content_summary": summary,
        "summary": summary,
        "url": "https://example.com/a",
        "source": source,
        "sources": [{"vendor": source, "publisher": source, "url": "https://example.com/a"}],
        "published_at": published_at,
        "publish_day": published_at.split(" ")[0].split("T")[0],
        "tags": {},
        "event_kind": event_kind,
        "verification_status": "unverified",
    }


def _provider(feeds: list[FeedSpec] | None = None) -> RSSHubEventProvider:
    return RSSHubEventProvider(
        feeds=feeds if feeds is not None else [FeedSpec("news", "/stock/news/{code}", "sentiment")],
    )


def test_is_available_true_when_trade_integrations_present() -> None:
    assert _provider().is_available() is True


def test_is_available_false_when_trade_integrations_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    import backtest.loaders.rsshub_events as mod

    monkeypatch.setattr(mod, "_INTEGRATIONS", mod._TRADE_ROOT / "does-not-exist")
    with patch.dict("sys.modules", {"trade_integrations": None}):
        assert mod.RSSHubEventProvider(feeds=[]).is_available() is False


def test_query_events_schema_and_scoring() -> None:
    items = [
        _hub_item(title="Company beats earnings", summary="Q4 revenue surges", published_at="2024-01-15 09:00:00"),
        _hub_item(title="Regulator opens probe", summary="probe into accounting, shares plunge", published_at="2024-01-16 18:30:00"),
    ]
    with patch.object(news_hub_bridge, "query_with_staging", return_value=items):
        frame = _provider().query_events(["AAA"], as_of="2024-01-31")

    assert list(frame.columns) == list(EVENT_COLUMNS)
    assert set(frame["ts_code"]) == {"AAA"}
    assert (frame["event_type"] == "sentiment").all()

    by_summary = {row.summary: row.score for row in frame.itertuples()}
    bullish = next(s for t, s in by_summary.items() if "revenue" in t)
    bearish = next(s for t, s in by_summary.items() if "probe" in t)
    assert bullish > 0
    assert bearish < 0


def test_after_close_publication_rolls_to_next_day() -> None:
    items = [
        _hub_item(title="Company beats earnings", summary="Q4 revenue surges", published_at="2024-01-15 09:00:00"),
        _hub_item(title="Regulator opens probe", summary="probe into accounting, shares plunge", published_at="2024-01-16 18:30:00"),
    ]
    with patch.object(news_hub_bridge, "query_with_staging", return_value=items):
        frame = _provider().query_events(["AAA"], as_of="2024-01-31")

    dates = dict(zip(frame["summary"], frame["knowable_date"]))
    intraday = next(v for k, v in dates.items() if "revenue" in k)  # 09:00 -> same day
    after_close = next(v for k, v in dates.items() if "probe" in k)  # 18:30 -> next day
    assert intraday == pd.Timestamp("2024-01-15")
    assert after_close == pd.Timestamp("2024-01-17")


def test_point_in_time_filter_excludes_future_items() -> None:
    items = [
        _hub_item(title="Company beats earnings", summary="Q4 revenue surges", published_at="2024-01-15 09:00:00"),
        _hub_item(title="Regulator opens probe", summary="probe into accounting, shares plunge", published_at="2024-01-16 18:30:00"),
    ]
    with patch.object(news_hub_bridge, "query_with_staging", return_value=items):
        frame = _provider().query_events(["AAA"], as_of="2024-01-15")

    assert (frame["knowable_date"] <= pd.Timestamp("2024-01-15")).all()
    assert len(frame) == 1  # only the 09:00 item is knowable by the 15th


def test_duplicate_items_are_deduplicated() -> None:
    item = _hub_item(title="Company beats earnings", summary="Q4 revenue surges", published_at="2024-01-15 09:00:00")
    with patch.object(news_hub_bridge, "query_with_staging", return_value=[item, dict(item)]):
        frame = _provider().query_events(["AAA"], as_of="2024-01-31")

    assert len(frame) == 1
    assert frame.duplicated(subset=["ts_code", "knowable_date", "event_type", "summary"]).sum() == 0


def test_unknown_feed_raises() -> None:
    with pytest.raises(UnknownFeedError):
        _provider().query_events(["AAA"], as_of="2024-01-31", feeds=["does_not_exist"])


def test_custom_scorer_override() -> None:
    items = [_hub_item(title="x", summary="y", published_at="2024-01-15 09:00:00")]
    with patch.object(news_hub_bridge, "query_with_staging", return_value=items):
        frame = _provider().query_events(["AAA"], as_of="2024-01-31", scorer=lambda t, s: 0.5)
    assert (frame["score"] == 0.5).all()


def test_default_lexicon_scorer_bounds() -> None:
    assert default_lexicon_scorer("", "") == 0.0
    assert default_lexicon_scorer("neutral filler text", "") == 0.0
    assert -1.0 <= default_lexicon_scorer("loss plunge fraud", "") <= 0.0
    assert 0.0 <= default_lexicon_scorer("beat surge record", "") <= 1.0


# ── No-default-catalogue + per-symbol code formatting + loud failures ─────────


def test_no_builtin_feed_catalogue() -> None:
    # Feeds rot / evolve, so they are always declared explicitly.
    assert DEFAULT_FEEDS == ()


@pytest.mark.parametrize(
    "code,style,expected",
    [
        ("600519.SH", "raw", "600519.SH"),
        ("600519.SH", "bare", "600519"),
        ("600519.SH", "exchange_prefix", "SH600519"),
        ("000001.SZ", "exchange_prefix", "SZ000001"),
        ("AAPL", "exchange_prefix", "AAPL"),  # no exchange suffix -> symbol only
    ],
)
def test_format_code_for_route(code: str, style: str, expected: str) -> None:
    assert format_code_for_route(code, style) == expected


def test_format_code_for_route_rejects_unknown_style() -> None:
    with pytest.raises(ValueError):
        format_code_for_route("600519.SH", "nope")


def test_feedspec_rejects_unknown_code_style() -> None:
    with pytest.raises(ValueError):
        FeedSpec("x", "/x/{code}", "earnings", code_style="bogus")


def test_feed_specs_from_config_parses_and_validates() -> None:
    specs = feed_specs_from_config(
        [
            {"name": "a", "route_template": "/x/{code}", "event_type": "earnings"},
            {"name": "b", "route_template": "/y", "event_type": "macro", "code_style": "bare"},
        ]
    )
    assert [s.name for s in specs] == ["a", "b"]
    assert specs[1].code_style == "bare"
    assert specs[0].code_style == "raw"  # default


@pytest.mark.parametrize(
    "bad",
    [
        [{"name": "", "route_template": "/x", "event_type": "earnings"}],  # blank name
        [{"route_template": "/x", "event_type": "earnings"}],  # missing name
        [{"name": "a", "route_template": "/x"}],  # missing event_type
        [
            {"name": "a", "route_template": "/x", "event_type": "e"},
            {"name": "a", "route_template": "/y", "event_type": "e"},  # duplicate name
        ],
        ["not-a-mapping"],
    ],
)
def test_feed_specs_from_config_rejects_bad(bad: list) -> None:
    with pytest.raises(ValueError):
        feed_specs_from_config(bad)


def test_all_hub_queries_fail_raises() -> None:
    # A configured-but-fully-unreachable Hub must fail loudly, never score
    # every bar 0.0 silently.
    with patch.object(news_hub_bridge, "query_with_staging", side_effect=ConnectionError("unreachable")):
        with pytest.raises(EventProviderError):
            _provider().query_events(["AAA"], as_of="2024-01-31")


def test_reachable_but_empty_query_does_not_raise() -> None:
    # A Hub query that succeeds but returns no items is legitimate, not an error.
    with patch.object(news_hub_bridge, "query_with_staging", return_value=[]):
        frame = _provider().query_events(["AAA"], as_of="2024-01-31")
    assert frame.empty


def test_event_kind_used_when_valid_taxonomy_value() -> None:
    items = [
        _hub_item(
            title="Policy shift announced",
            summary="regulator changes rules",
            published_at="2024-01-15 09:00:00",
            event_kind="policy",
        )
    ]
    with patch.object(news_hub_bridge, "query_with_staging", return_value=items):
        frame = _provider().query_events(["AAA"], as_of="2024-01-31")
    assert (frame["event_type"] == "policy").all()


def test_event_kind_falls_back_to_feed_event_type_when_missing() -> None:
    items = [
        _hub_item(title="Something happened", summary="", published_at="2024-01-15 09:00:00", event_kind="")
    ]
    with patch.object(news_hub_bridge, "query_with_staging", return_value=items):
        frame = _provider(
            feeds=[FeedSpec("earn", "/x/{code}", "earnings")]
        ).query_events(["AAA"], as_of="2024-01-31")
    assert (frame["event_type"] == "earnings").all()
