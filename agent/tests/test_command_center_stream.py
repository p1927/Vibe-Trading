"""Tests for the Command Center SSE stream (`GET /trade/command-center/stream`) —
per-leg (positions/prediction/news) server-push replacing the dashboard's old
client-side poll timers + manual refresh buttons. See
.claude/backlog/items/2026-08-28-command-center-real-time-push.md.

Drives `_command_center_event_stream` directly (an async generator) rather than
through `TestClient`/`StreamingResponse`, which is simpler and avoids streaming-
transport edge cases unrelated to this generator's own diff/poll logic.
"""

from __future__ import annotations

import asyncio

import pytest

from src.api import trade_routes as routes


class _FakeRequest:
    """`is_disconnected()` returns False for `alive_ticks` calls, then True."""

    def __init__(self, alive_ticks: int) -> None:
        self._remaining = alive_ticks

    async def is_disconnected(self) -> bool:
        if self._remaining <= 0:
            return True
        self._remaining -= 1
        return False


def _drive(agent_id: str, ticker: str, alive_ticks: int) -> list[tuple[str, str]]:
    """Run the generator for `alive_ticks` live loop passes, return [(event, raw_data), ...]."""

    async def _run() -> list[tuple[str, str]]:
        frames: list[tuple[str, str]] = []
        request = _FakeRequest(alive_ticks)
        async for frame in routes._command_center_event_stream(agent_id, ticker, request):
            if frame.startswith(": keepalive"):
                frames.append(("keepalive", ""))
                continue
            event_line, data_line = frame.split("\n", 1)
            event = event_line.removeprefix("event: ")
            data = data_line.removeprefix("data: ").rstrip("\n")
            frames.append((event, data))
        return frames

    return asyncio.run(_run())


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    real_sleep = asyncio.sleep
    monkeypatch.setattr(routes.asyncio, "sleep", lambda *_a, **_kw: real_sleep(0))


def test_first_tick_emits_all_three_legs_when_agent_id_given(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "nautilus_openalgo_bridge.live_pop.compute_live_pop_for_agent",
        lambda agent_id: {"agent_id": agent_id, "groups": [], "skipped": []},
    )
    monkeypatch.setattr("src.trade.hub_bridge.load_hub_plan_artifact", lambda ticker, kind: {"spot": 24000.0})
    monkeypatch.setattr("trade_integrations.context.hub.load_index_research_json", lambda ticker: None)
    monkeypatch.setattr(
        "trade_integrations.dataflows.news_hub_bridge.resolve_news_impact",
        lambda **kwargs: {"items": []},
    )

    frames = _drive("aa_one", "NIFTY", alive_ticks=1)
    events = [e for e, _ in frames]

    assert "positions" in events
    assert "prediction" in events
    assert "news" in events


def test_no_agent_id_skips_positions_leg_but_still_emits_prediction_and_news(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("src.trade.hub_bridge.load_hub_plan_artifact", lambda ticker, kind: {"spot": 24000.0})
    monkeypatch.setattr("trade_integrations.context.hub.load_index_research_json", lambda ticker: None)
    monkeypatch.setattr(
        "trade_integrations.dataflows.news_hub_bridge.resolve_news_impact",
        lambda **kwargs: {"items": []},
    )

    frames = _drive("", "NIFTY", alive_ticks=1)
    events = [e for e, _ in frames]

    assert "positions" not in events
    assert "prediction" in events
    assert "news" in events


def test_unchanged_snapshot_across_polls_is_not_re_emitted(monkeypatch: pytest.MonkeyPatch) -> None:
    """Real-time push means "notify on change", not "blast every tick" — an identical
    snapshot on a later poll must not produce a second frame. Forces re-polls within one
    test run by monkeypatching monotonic() to jump past every leg's interval each tick."""
    calls = {"positions": 0}

    def _positions(agent_id: str) -> dict:
        calls["positions"] += 1
        return {"agent_id": agent_id, "groups": [], "skipped": []}

    monkeypatch.setattr("nautilus_openalgo_bridge.live_pop.compute_live_pop_for_agent", _positions)
    monkeypatch.setattr("src.trade.hub_bridge.load_hub_plan_artifact", lambda ticker, kind: {"spot": 24000.0})
    monkeypatch.setattr("trade_integrations.context.hub.load_index_research_json", lambda ticker: None)
    monkeypatch.setattr(
        "trade_integrations.dataflows.news_hub_bridge.resolve_news_impact",
        lambda **kwargs: {"items": []},
    )

    fake_now = [0.0]

    def _fake_monotonic() -> float:
        fake_now[0] += 100.0  # always past every leg's poll interval
        return fake_now[0]

    monkeypatch.setattr("time.monotonic", _fake_monotonic)

    frames = _drive("aa_one", "NIFTY", alive_ticks=3)
    positions_events = [d for e, d in frames if e == "positions"]

    assert calls["positions"] == 3  # polled every tick...
    assert len(positions_events) == 1  # ...but only emitted once, since the snapshot never changed
