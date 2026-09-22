"""Fork sidecar: keep stdio MCP server subprocesses warm across tool calls (Trade fork).

Upstream ``MCPServerAdapter`` opens a fresh ``StdioTransport(keep_alive=False)`` client for every
``list_tools`` and ``call_tool``, so every call cold-starts the server. For Trade's OpenAlgo MCP
server that means sourcing ``.env``, starting the openalgo venv, importing ``mcpserver.py``,
``custom_tools`` and ``trade_integrations``, and registering ~100 tools, which measured 2.5 s per
trivial call and 7-11 s per autonomous-agent call (Trade backlog 2026-09-23-mcp-subprocess-per-call).

This module keeps connected clients in a process-wide pool per server config. The clients live
on one background event loop, because a fastmcp session is bound to the loop that opened it.

* **Warm reuse.** A call borrows an idle connected client. When none is idle it starts a new
  one, up to ``VIBE_MCP_STDIO_POOL_SIZE`` (default 4); beyond that it waits for one to free up.
  More than one session is needed because FastMCP runs a sync tool on its own event loop, so one
  slow tool (a TradingAgents debate) would block every other call on the same session.
* **Reconnect on death.** An idle client whose session has gone (the server exited) is dropped
  before it is handed out. A call that raises (timeout, closed connection, protocol error)
  closes its session instead of returning it to the pool: the subprocess may still be running
  a timed-out tool, and a new call must not queue behind it. The next call starts a fresh one.
  A failed call is never retried, matching upstream's no-retry rule for side-effecting tools.
  A tool that *reports* an error (``isError``) is a normal result and keeps its session.
* **Config changes.** The pool key is the adapter's config cache key, so a changed command,
  args or env gets its own pool. A rotated OpenAlgo API key inside ``agent.json`` needs no
  restart: the server rebinds it itself (Trade D33).
* **Exit.** The loop thread is a daemon. When this process exits, each child sees EOF on stdin
  and exits by itself.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import threading
from collections.abc import AsyncIterator, Callable, Coroutine
from typing import Any, TypeVar

logger = logging.getLogger(__name__)

ResultT = TypeVar("ResultT")

_DEFAULT_POOL_SIZE = 4

_loop: asyncio.AbstractEventLoop | None = None
_loop_lock = threading.Lock()
_pools: dict[tuple[str, ...], "StdioClientPool"] = {}
_pools_lock = threading.Lock()


def _background_loop() -> asyncio.AbstractEventLoop:
    global _loop
    with _loop_lock:
        if _loop is None:
            loop = asyncio.new_event_loop()
            threading.Thread(target=loop.run_forever, name="mcp-stdio-pool", daemon=True).start()
            _loop = loop
        return _loop


def _pool_size() -> int:
    raw = os.environ.get("VIBE_MCP_STDIO_POOL_SIZE", "")
    return max(1, int(raw)) if raw.strip() else _DEFAULT_POOL_SIZE


class StdioClientPool:
    """Warm fastmcp clients for one stdio server config, all on the shared background loop."""

    def __init__(self, client_factory: Callable[[], Any], max_size: int) -> None:
        self._factory = client_factory
        self._max_size = max_size
        self._idle: list[Any] = []
        self._size = 0
        self._cond: asyncio.Condition | None = None  # created on the background loop

    def run_sync(self, operation: Callable[[], Coroutine[Any, Any, ResultT]]) -> ResultT:
        """Run ``operation`` on the pool's loop and block for its result.

        Drop-in for ``tools.mcp._run_sync``: ``operation`` borrows clients through ``lease``.
        """
        return asyncio.run_coroutine_threadsafe(operation(), _background_loop()).result()

    @contextlib.asynccontextmanager
    async def lease(self) -> AsyncIterator[Any]:
        """Borrow a connected client; return it on success, close it on any exception."""
        client = await self._acquire()
        try:
            yield client
        except BaseException:
            await self._discard(client)
            raise
        await self._release(client)

    def _condition(self) -> asyncio.Condition:
        if self._cond is None:
            self._cond = asyncio.Condition()
        return self._cond

    async def _acquire(self) -> Any:
        cond = self._condition()
        async with cond:
            while True:
                while self._idle:
                    client = self._idle.pop()
                    if _alive(client):
                        return client
                    logger.warning("MCP stdio session died while idle; starting a new one")
                    self._size -= 1
                    await _close(client)  # already dead, so its teardown is quick
                if self._size < self._max_size:
                    self._size += 1
                    break
                await cond.wait()
        try:
            client = self._factory()
            await client.__aenter__()
            return client
        except BaseException:
            async with cond:
                self._size -= 1
                cond.notify()
            raise

    async def _release(self, client: Any) -> None:
        cond = self._condition()
        async with cond:
            self._idle.append(client)
            cond.notify()

    async def _discard(self, client: Any) -> None:
        await _close(client)
        cond = self._condition()
        async with cond:
            self._size -= 1
            cond.notify()


def _alive(client: Any) -> bool:
    """Whether a pooled client's server is still there.

    ``Client.is_connected()`` stays true after the subprocess dies. The transport's own
    stream check is what fastmcp itself uses before reconnecting (``StdioTransport.connect``),
    so it is used here too. It is private, hence the ``getattr``.
    """
    if not client.is_connected():
        return False
    session_dead = getattr(getattr(client, "transport", None), "_is_session_dead", None)
    return not (callable(session_dead) and session_dead())


async def _close(client: Any) -> None:
    try:
        await client.close()
    except Exception as exc:  # the session is being thrown away; its teardown error is not actionable
        logger.warning("Closing a discarded MCP stdio session failed: %s", exc)


def pool_for(key: tuple[str, ...], client_factory: Callable[[], Any]) -> StdioClientPool:
    """The process-wide pool for ``key``, created on first use with ``client_factory``."""
    with _pools_lock:
        pool = _pools.get(key)
        if pool is None:
            pool = _pools[key] = StdioClientPool(client_factory, _pool_size())
        return pool


def pool_for_adapter(adapter: Any, make_key: Callable[[str, Any], tuple[str, ...]]) -> StdioClientPool | None:
    """The pool an ``MCPServerAdapter`` should run on, or None to keep upstream's per-call client.

    Only a stdio adapter built with its own default client factory is pooled; an injected
    factory (tests, callers with their own client) is left alone. Once pooled, the adapter's
    ``_client_factory`` becomes the pool's ``lease``, so upstream's
    ``async with self._client_factory() as client`` borrows a warm client.
    """
    if not getattr(adapter, "_default_client_factory", False):
        return None
    if adapter.server_config.resolved_transport() != "stdio":
        return None
    pool = pool_for(make_key(adapter.server_name, adapter.server_config), adapter._build_client)
    adapter._client_factory = pool.lease
    return pool
