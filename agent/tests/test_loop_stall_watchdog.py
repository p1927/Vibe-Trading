"""Tests for the event-loop stall watchdog (src/api/loop_stall_watchdog.py)."""

from __future__ import annotations

import asyncio
import logging
import time

from src.api import loop_stall_watchdog as wd


def _block_the_loop_synchronously(seconds: float) -> None:
    time.sleep(seconds)  # the sync call a real stall would be stuck in


def test_stall_is_logged_with_the_blocking_frame_then_recovery(caplog) -> None:
    caplog.set_level(logging.WARNING, logger=wd.__name__)
    before = wd.loop_stall_stats()["stall_count"]

    async def _scenario() -> None:
        wd.start_loop_stall_watchdog(probe_interval_s=0.05, stall_threshold_s=0.2)
        try:
            await asyncio.sleep(0.15)  # healthy period: no report
            _block_the_loop_synchronously(0.8)
            await asyncio.sleep(0.3)  # let the watchdog see the recovery
        finally:
            wd.stop_loop_stall_watchdog()

    asyncio.run(_scenario())

    messages = [r.getMessage() for r in caplog.records if r.name == wd.__name__]
    stalled = [m for m in messages if m.startswith("event loop stalled")]
    recovered = [m for m in messages if m.startswith("event loop recovered")]
    assert len(stalled) == 1, messages
    assert "_block_the_loop_synchronously" in stalled[0]
    assert len(recovered) == 1, messages
    stats = wd.loop_stall_stats()
    assert stats["stall_count"] == before + 1
    assert stats["max_stall_seconds"] >= 0.5


def test_healthy_loop_logs_nothing(caplog) -> None:
    caplog.set_level(logging.WARNING, logger=wd.__name__)

    async def _scenario() -> None:
        wd.start_loop_stall_watchdog(probe_interval_s=0.02, stall_threshold_s=0.2)
        try:
            for _ in range(20):
                await asyncio.sleep(0.02)
        finally:
            wd.stop_loop_stall_watchdog()

    asyncio.run(_scenario())
    assert not [r for r in caplog.records if r.name == wd.__name__]


def test_start_is_idempotent() -> None:
    async def _scenario() -> None:
        wd.start_loop_stall_watchdog(probe_interval_s=0.05, stall_threshold_s=1.0)
        first = wd._thread
        wd.start_loop_stall_watchdog(probe_interval_s=0.05, stall_threshold_s=1.0)
        assert wd._thread is first
        wd.stop_loop_stall_watchdog()
        assert wd._thread is None

    asyncio.run(_scenario())
