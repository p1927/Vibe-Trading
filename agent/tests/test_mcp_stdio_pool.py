"""Fork: warm stdio MCP sessions (tools/mcp_stdio_pool.py) and the ``{"item": [...]}`` unwrap.

Trade backlog 2026-09-23-mcp-subprocess-per-call: every MCP tool call used to spawn a fresh
OpenAlgo MCP subprocess. Trade backlog 2026-09-23-mcp-tool-arg-contracts: MiniMax sends array
arguments as ``{"item": [...]}``, which tool validation rejected.
"""

from __future__ import annotations

import asyncio
import threading
from typing import Any

import pytest

from src.agent.tool_arg_coercion import unwrap_item_arrays
from src.agent.tools import BaseTool, ToolRegistry
from src.config.schema import MCPServerConfig
from src.tools import mcp_stdio_pool
from src.tools.mcp import MCPServerAdapter, _make_cache_key


class _FakeClient:
    created = 0

    def __init__(self) -> None:
        type(self).created += 1
        self.id = type(self).created
        self.connected = False
        self.closed = False

    async def __aenter__(self) -> "_FakeClient":
        self.connected = True
        return self

    def is_connected(self) -> bool:
        return self.connected

    async def close(self) -> None:
        self.connected = False
        self.closed = True


@pytest.fixture
def pool():
    _FakeClient.created = 0
    return mcp_stdio_pool.StdioClientPool(_FakeClient, max_size=2)


def _use(pool, body=None):
    async def op():
        async with pool.lease() as client:
            if body is not None:
                await body(client)
            return client

    return pool.run_sync(op)


def test_calls_reuse_one_warm_client(pool) -> None:
    first, second = _use(pool), _use(pool)
    assert first is second and _FakeClient.created == 1 and not first.closed


def test_a_failed_call_closes_its_session_and_the_next_call_starts_fresh(pool) -> None:
    async def boom(client):
        raise TimeoutError("tool timed out")

    with pytest.raises(TimeoutError):
        _use(pool, boom)
    fresh = _use(pool)
    assert _FakeClient.created == 2 and fresh.id == 2


def test_a_session_that_died_while_idle_is_replaced(pool) -> None:
    dead = _use(pool)
    dead.connected = False  # the server subprocess exited between calls
    assert _use(pool) is not dead and _FakeClient.created == 2


def test_concurrency_is_bounded_and_waiters_get_a_freed_client(pool) -> None:
    release = threading.Event()
    in_use: list[int] = []
    peak: list[int] = []

    async def hold(client):
        in_use.append(client.id)
        peak.append(len(in_use))
        await asyncio.get_running_loop().run_in_executor(None, release.wait, 5)
        in_use.remove(client.id)

    threads = [threading.Thread(target=_use, args=(pool, hold)) for _ in range(4)]
    for t in threads:
        t.start()
    release.set()
    for t in threads:
        t.join(10)
    assert max(peak) <= 2 and _FakeClient.created == 2


def test_only_default_factory_stdio_adapters_are_pooled() -> None:
    stdio = MCPServerConfig.model_validate({"command": "uvx", "args": ["demo-pool-test"]})
    adapter = MCPServerAdapter("demo", stdio)
    pool = mcp_stdio_pool.pool_for_adapter(adapter, _make_cache_key)
    assert pool is not None and adapter._client_factory == pool.lease
    assert mcp_stdio_pool.pool_for_adapter(MCPServerAdapter("demo", stdio), _make_cache_key) is pool

    injected = MCPServerAdapter("demo", stdio, client_factory=_FakeClient)
    assert mcp_stdio_pool.pool_for_adapter(injected, _make_cache_key) is None


_ARRAY_SCHEMA = {
    "type": "object",
    "properties": {
        "actions_taken": {"anyOf": [{"type": "array", "items": {"type": "string"}}, {"type": "null"}]},
        "allowed_instruments": {"type": "array", "items": {"type": "string"}},
        "payload": {"type": "object"},
        "note": {"type": "string"},
    },
}


def test_item_envelope_is_unwrapped_only_for_array_params() -> None:
    params = {
        "actions_taken": {"item": ["a", "b"]},
        "allowed_instruments": {"item": "options"},
        "payload": {"item": [1]},
        "note": "x",
    }
    out = unwrap_item_arrays(params, _ARRAY_SCHEMA)
    assert out["actions_taken"] == ["a", "b"]
    assert out["allowed_instruments"] == ["options"]
    assert out["payload"] == {"item": [1]}  # object param: left alone
    assert params["actions_taken"] == {"item": ["a", "b"]}  # input not mutated
    assert unwrap_item_arrays({"actions_taken": ["a"]}, _ARRAY_SCHEMA) == {"actions_taken": ["a"]}


def test_registry_execute_hands_tools_the_unwrapped_list() -> None:
    seen: dict[str, Any] = {}

    class _Tool(BaseTool):
        name = "record"
        parameters = _ARRAY_SCHEMA

        def execute(self, **kwargs: Any) -> str:
            seen.update(kwargs)
            return "{}"

    registry = ToolRegistry()
    registry.register(_Tool())
    registry.execute("record", {"actions_taken": {"item": ["entered NIFTY"]}})
    assert seen["actions_taken"] == ["entered NIFTY"]
