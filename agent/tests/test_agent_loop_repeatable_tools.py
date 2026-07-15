"""Regression tests for parameter-dependent query tools in the agent loop."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from src.agent.context import ContextBuilder
from src.agent.loop import AgentLoop
from src.agent.tools import ToolRegistry
from src.agent.trace import TraceWriter
from src.tools.market_data_tool import MarketDataTool


def test_market_data_executes_again_with_different_arguments(
    monkeypatch, tmp_path: Path
) -> None:
    """A successful query must not suppress the next iteration's symbol."""
    calls: list[list[str]] = []
    tool = MarketDataTool()

    def _execute(**kwargs: object) -> str:
        calls.append(list(kwargs["codes"]))  # type: ignore[arg-type]
        return json.dumps({"status": "ok", "codes": kwargs["codes"]})

    monkeypatch.setattr(tool, "execute", _execute)
    registry = ToolRegistry()
    registry.register(tool)
    agent = AgentLoop(registry=registry, llm=SimpleNamespace(), max_iterations=2)

    run_dir = tmp_path / "run"
    run_dir.mkdir()
    agent.memory.run_dir = str(run_dir)
    trace = TraceWriter(run_dir)
    messages: list[dict[str, object]] = []
    react_trace: list[dict[str, object]] = []

    for iteration, (call_id, code) in enumerate(
        (("call_aapl", "AAPL.US"), ("call_msft", "MSFT.US")), start=1
    ):
        agent._process_tool_calls(
            [
                SimpleNamespace(
                    id=call_id,
                    name="get_market_data",
                    arguments={
                        "codes": [code],
                        "start_date": "2025-01-01",
                        "end_date": "2025-01-31",
                    },
                )
            ],
            ContextBuilder,
            messages,
            trace,
            react_trace,
            iteration,
        )
    trace.close()

    assert calls == [["AAPL.US"], ["MSFT.US"]]
    assert len(messages) == 2
    assert not any(
        event["type"] == "tool_skipped" for event in TraceWriter.read(run_dir)
    )
