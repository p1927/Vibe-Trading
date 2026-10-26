"""trade_plan.widget SSE de-dup: a repeat is dropped, a regenerated plan is not."""

from __future__ import annotations

import pytest

from src.api import sse_frames


@pytest.fixture(autouse=True)
def _clean_recent():
    sse_frames._recent_trade_plan_widgets.clear()
    yield
    sse_frames._recent_trade_plan_widgets.clear()


def _widget(**over):
    base = {
        "widget_id": "w1",
        "underlying": "nifty",
        "recommended": {"strategy": "Iron Condor"},
        "legs": [{"strike": 24000, "side": "sell"}],
        "spot": 24100.0,
        "generated_at": "2026-10-26T10:00:00Z",
    }
    base.update(over)
    return base


def test_an_identical_repeat_with_a_new_id_and_timestamp_is_dropped():
    assert sse_frames._should_skip_duplicate_trade_widget("s", _widget()) is False
    again = _widget(widget_id="w2", generated_at="2026-10-26T10:00:05Z")
    assert sse_frames._should_skip_duplicate_trade_widget("s", again) is True


@pytest.mark.parametrize(
    "change",
    [
        {"legs": [{"strike": 24200, "side": "sell"}]},
        {"spot": 24310.5},
        {"underlying": "banknifty"},
        {"recommended": {"strategy": "Bull Put Spread"}},
    ],
)
def test_a_regenerated_plan_is_not_dropped(change):
    assert sse_frames._should_skip_duplicate_trade_widget("s", _widget()) is False
    assert sse_frames._should_skip_duplicate_trade_widget("s", _widget(widget_id="w2", **change)) is False


def test_another_session_is_independent_and_no_session_never_skips():
    assert sse_frames._should_skip_duplicate_trade_widget("s", _widget()) is False
    assert sse_frames._should_skip_duplicate_trade_widget("other", _widget()) is False
    assert sse_frames._should_skip_duplicate_trade_widget("", _widget()) is False
