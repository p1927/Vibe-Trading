"""Full LiveRunner.run_once() state machine: the order-authorization invariant.

Domain B of the testing-strategy backlog
(`.claude/backlog/items/2026-08-21-autonomous-watcher-dst-lite-tests.md`):
"a Hypothesis state machine over triggers/scheduler/runner checking 'no
unauthorized order ever'". This is the piece `test_scheduler_triggers_dst_lite.py`
deliberately deferred — that file covers the *scheduling* half (no
double-fire); this covers the *authorization* half, end to end through a
real agent dispatch, by wiring together every pattern this session's Domain B
work built separately:

- `LiveRunner.run_once()` (`src/live/runtime/runner.py`) — the real fail-closed
  tick order: halt → mandate/expiry → reconcile → agent invocation → audit.
- A fake `AgentCaller` that runs a REAL `AgentLoop` per tick, scripted (via
  `ScriptedChatLLM` + `FakeSearchSymbolTool`, both from `scripted_llm_helpers.py`)
  to always attempt one small, in-cap order through a real `LiveOrderGuardTool`
  — the same pattern `test_agent_loop_order_guard_scenarios.py` uses directly,
  now driven from inside the runner instead of standalone.
- Real mandate.json / HALT sentinel files on disk (`src.live.mandate.store`,
  `src.live.halt`) — so BOTH the runner's own pre-checks (steps 1-2 of
  `run_once`) and the order guard's independent internal checks read the same
  on-disk state, matching production wiring exactly rather than mocking one
  layer and leaving the other real.

State machine: rules mutate the on-disk mandate/halt state; `run_tick` drives
one `run_once()` and checks the invariant named in the backlog — "the mock
broker adapter's order call count is zero unless a live, unexpired, unhalted
mandate explicitly authorized it" — after every tick.

Reconcile is deliberately held always-safe (`safe_to_trade=True` injected) —
that's R4's own concern, out of scope for this file's mandate/halt-focused
invariant.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from hypothesis import HealthCheck, settings
from hypothesis.stateful import RuleBasedStateMachine, invariant, rule
from hypothesis import strategies as st

import src.live.paths as paths
from src.agent.loop import AgentLoop
from src.agent.tools import ToolRegistry
from src.live.halt import clear_halt, halt_flag_set, trip_halt
from src.live.mandate.model import MANDATE_SCHEMA_VERSION
from src.live.mandate.store import load_mandate
from src.live.order_guard import LiveOrderGuardTool
from src.live.runtime.runner import LiveRunner, _mandate_is_expired
from src.tools.mcp import MCPRemoteToolSpec
from tests.scripted_llm_helpers import (
    FakeSearchSymbolTool,
    ScriptedChatLLM,
    llm_text,
    llm_tool_call,
)

pytestmark = pytest.mark.runtime_dst

_BROKER = "robinhood"
_ORDER_TOOL_REMOTE_NAME = "place_equity_order"
# Comfortably inside the mandate's own max_order_notional_usd below — the
# ONLY reasons this order should ever be blocked are the structural gates
# under test (halt / no mandate / expired mandate), not a cap breach.
_IN_CAP_NOTIONAL_USD = 100.0


class _MockAdapter:
    """Fake broker adapter — returns a realistic EMPTY positions envelope for
    read calls so `check_mandate`'s exposure/leverage math can actually
    resolve (a garbage/unparseable positions payload fails closed at that
    check regardless of mandate/halt state, which would silently make this
    file's "authorized order actually gets placed" positive case impossible
    to reach — caught via a direct manual check while building this test)."""

    def __init__(self) -> None:
        self.server_name = _BROKER
        self.calls: list[str] = []

    def call_tool(self, remote_name: str, arguments: dict, *, local_name: str | None = None) -> dict:
        self.calls.append(remote_name)
        if remote_name == _ORDER_TOOL_REMOTE_NAME:
            return {"status": "ok", "order_id": "rh_x", "state": "accepted"}
        if "position" in remote_name.lower():
            return {"positions": []}
        return {"status": "ok"}

    @property
    def order_call_count(self) -> int:
        return sum(1 for name in self.calls if name == _ORDER_TOOL_REMOTE_NAME)


def _order_spec() -> MCPRemoteToolSpec:
    return MCPRemoteToolSpec(
        server_name=_BROKER,
        remote_name=_ORDER_TOOL_REMOTE_NAME,
        local_name="mcp_robinhood_place_equity_order",
        description="Place an order.",
        parameters={"type": "object", "properties": {}, "additionalProperties": True},
    )


def _mandate_payload(*, expires_at: datetime) -> dict[str, Any]:
    created = datetime.now(timezone.utc)
    return {
        "schema_version": MANDATE_SCHEMA_VERSION,
        "hard_caps": {
            "account_funding_usd": 5000.0,
            "max_order_notional_usd": 750.0,
            "max_total_exposure_usd": 5000.0,
            "max_leverage": 1.0,
            "allowed_instruments": ["equity", "etf"],
            "max_trades_per_day": 50,
        },
        "universe": {
            "asset_classes": ["us_equity", "us_etf"],
            "min_market_cap_usd": None,
            "min_avg_daily_volume_usd": None,
            "exclude_symbols": [],
        },
        "consent": {
            "created_at": created.isoformat(),
            "consent_token_sha256": "deadbeef",
            "broker": _BROKER,
            "account_ref": "acct_ref",
            "expires_at": expires_at.isoformat(),
        },
    }


class LiveRunnerOrderAuthorizationStateMachine(RuleBasedStateMachine):
    def __init__(self) -> None:
        super().__init__()
        self._monkey = pytest.MonkeyPatch()
        import tempfile

        self._tmp = Path(tempfile.mkdtemp(prefix="live_runner_dst_"))
        self._monkey.setattr(paths, "get_runtime_root", lambda: self._tmp)

        self.adapter = _MockAdapter()
        self._tick_counter = 0

        self.runner = LiveRunner(
            _BROKER,
            agent_caller=self._agent_caller,
            reconcile_fn=lambda *_a, **_kw: SimpleNamespace(safe_to_trade=True),
            read_positions=lambda: {"status": "ok"},
            read_balance=lambda: {"status": "ok"},
            read_open_orders=lambda: {"status": "ok"},
            session_id="dst-live-runner",
        )

    def teardown(self) -> None:
        self._monkey.undo()

    async def _agent_caller(self, session_id: str, prompt: str) -> dict[str, Any]:
        """One tick's agent turn: resolve AAPL, then request one in-cap order."""
        guard = LiveOrderGuardTool(self.adapter, _order_spec(), broker=_BROKER, session_id=session_id)
        registry = ToolRegistry()
        registry.register(guard)
        registry.register(FakeSearchSymbolTool())

        llm = ScriptedChatLLM(
            [
                llm_tool_call("search_symbol", {"query": "AAPL"}),
                llm_tool_call(
                    guard.name,
                    {
                        "symbol": "AAPL",
                        "side": "buy",
                        "instrument_type": "equity",
                        "notional_usd": _IN_CAP_NOTIONAL_USD,
                    },
                ),
                llm_text("Tick complete."),
            ]
        )
        agent = AgentLoop(registry=registry, llm=llm, max_iterations=5)
        self._tick_counter += 1
        run_dir = self._tmp / "run" / f"tick-{self._tick_counter}"
        run_dir.mkdir(parents=True, exist_ok=True)
        agent.memory.run_dir = str(run_dir)
        agent.run(user_message=prompt)
        return {"status": "ok"}

    def _mandate_path(self) -> Path:
        broker_dir = self._tmp / "live" / _BROKER
        broker_dir.mkdir(parents=True, exist_ok=True)
        return broker_dir / "mandate.json"

    # -- rules: mutate on-disk mandate/halt state --------------------------

    @rule()
    def write_valid_mandate(self) -> None:
        expires_at = datetime.now(timezone.utc) + timedelta(days=30)
        self._mandate_path().write_text(json.dumps(_mandate_payload(expires_at=expires_at)))

    @rule()
    def write_expired_mandate(self) -> None:
        expires_at = datetime.now(timezone.utc) - timedelta(days=1)
        self._mandate_path().write_text(json.dumps(_mandate_payload(expires_at=expires_at)))

    @rule()
    def delete_mandate(self) -> None:
        self._mandate_path().unlink(missing_ok=True)

    @rule()
    def trip_halt(self) -> None:
        trip_halt(by="file", reason="dst state machine")

    @rule()
    def clear_halt_rule(self) -> None:
        # Clears BOTH the global sentinel this rule trips AND the per-broker
        # one LiveRunner itself trips proactively on mandate expiry
        # (`_expired_result` — confirmed via manual trace: an expired-mandate
        # tick halts `robinhood` specifically, not just globally). Without
        # clearing both, one expired-mandate tick permanently halts the rest
        # of a run regardless of later `write_valid_mandate` calls, which
        # would stop this state machine from ever exploring the "recovers
        # after re-auth" transition.
        clear_halt()
        clear_halt(broker=_BROKER)

    # -- rule: drive one tick, check the invariant --------------------------

    def _is_live_authorized(self) -> bool:
        """Recompute authorization straight from disk, the same way
        `LiveRunner.run_once()` itself does — never hand-tracked, so it can't
        drift out of sync with side effects the runner causes on its own
        (like the proactive per-broker halt on mandate expiry)."""
        if halt_flag_set(_BROKER):
            return False
        mandate = load_mandate(_BROKER)
        if mandate is None:
            return False
        return not _mandate_is_expired(mandate, datetime.now(timezone.utc))

    @rule()
    def run_tick(self) -> None:
        before = self.adapter.order_call_count
        authorized_before = self._is_live_authorized()
        asyncio.run(self.runner.run_once())
        after = self.adapter.order_call_count

        assert after - before <= 1, (
            f"more than one order call happened in a single tick: "
            f"{before} -> {after}"
        )
        if after > before:
            assert authorized_before, (
                f"an order was placed (order_call_count {before} -> {after}) "
                f"without a live, unexpired, unhalted mandate authorizing it "
                f"at tick time."
            )

    @invariant()
    def adapter_never_saw_more_orders_than_ticks_could_authorize(self) -> None:
        # Cheap sanity invariant checked after every rule (not just run_tick):
        # the mock adapter's order-call count can only ever have grown during
        # a run_tick call, and run_tick already asserts the authorization
        # condition on growth — this just guards against a future rule
        # accidentally calling the adapter directly outside run_tick.
        assert self.adapter.order_call_count <= self._tick_counter


TestLiveRunnerOrderAuthorization = LiveRunnerOrderAuthorizationStateMachine.TestCase
TestLiveRunnerOrderAuthorization.settings = settings(
    max_examples=20,
    stateful_step_count=15,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large],
)
TestLiveRunnerOrderAuthorization.pytestmark = pytest.mark.runtime_dst
