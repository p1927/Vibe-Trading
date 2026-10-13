"""The recording-wake poll loop must outlive a failing tick.

It is the only thing that wakes a ``wait_for_open`` recording and re-arms Auto Record; an
exception from the auto-rearm half (e.g. ``get_job`` on a corrupt record) used to end the task
silently, after which no recording ever woke again until the API restarted.
"""

from __future__ import annotations

import asyncio

from src.trade import recording_wait_scheduler as rws


def test_poll_loop_keeps_ticking_after_an_exception(monkeypatch):
    ticks: list[int] = []

    async def tick(_store):
        ticks.append(1)
        if len(ticks) == 1:
            raise RuntimeError("corrupt store")

    async def rearm():
        if len(ticks) == 2:
            raise ValueError("bad auto_record config")

    async def no_sleep(_s):
        if len(ticks) >= 4:
            raise asyncio.CancelledError

    monkeypatch.setattr(rws, "_recording_wake_poll_tick", tick)
    monkeypatch.setattr(rws, "_maybe_rearm_auto_record", rearm)
    monkeypatch.setattr(rws.asyncio, "sleep", no_sleep)

    try:
        asyncio.run(rws._recording_wake_poll_loop(object()))
    except asyncio.CancelledError:
        pass

    assert len(ticks) >= 4
