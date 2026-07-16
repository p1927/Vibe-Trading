"""Schedule coroutines on the FastAPI main event loop from sync contexts."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Coroutine

logger = logging.getLogger(__name__)

_main_loop: asyncio.AbstractEventLoop | None = None


def register_main_loop(loop: asyncio.AbstractEventLoop) -> None:
    """Called once during API startup."""
    global _main_loop
    _main_loop = loop


def get_main_loop() -> asyncio.AbstractEventLoop | None:
    return _main_loop


def schedule_coroutine(coro: Coroutine[Any, Any, Any], *, label: str = "background") -> asyncio.Future | asyncio.Task | None:
    """Run *coro* on the main loop (thread-safe from worker threads)."""
    try:
        running = asyncio.get_running_loop()
    except RuntimeError:
        running = None

    loop = _main_loop
    if loop is None or not loop.is_running():
        if running is not None and running.is_running():
            loop = running
        else:
            logger.error("cannot schedule %s coroutine: main event loop unavailable", label)
            return None

    if running is loop:
        return asyncio.create_task(coro, name=label)

    return asyncio.run_coroutine_threadsafe(coro, loop)
