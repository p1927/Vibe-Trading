"""Regression coverage for the hard tool-call-required gate on operator-retry turns.

Two softer countermeasures for the same fabrication (an instruction telling the
model to re-verify, and a content-based grounding check) both proved
insufficient in live testing -- the model still answered a "retry" turn with a
fabricated tool-results narrative and zero real tool calls. See
.claude/backlog/items/2026-08-29-autonomous-agent-retry-fix-not-live-effective.md.
This is the hard, content-blind replacement: an operator-retry turn in an
autonomous-agent session must make a real tool call before its first answer is
accepted, regardless of what that first answer says.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from src.agent.loop import AgentLoop
from src.agent.tools import BaseTool, ToolRegistry


class _EchoTool(BaseTool):
    name = "get_research_status"
    description = "test tool"
    parameters = {"type": "object", "properties": {}}

    def __init__(self) -> None:
        self.calls = 0

    def execute(self, **kwargs: Any) -> str:
        self.calls += 1
        return '{"status": "ok"}'


class _Response:
    def __init__(self, *, content: str = "", tool_calls: list | None = None) -> None:
        self.content = content
        self.tool_calls = tool_calls or []
        self.reasoning_content = None
        self.has_tool_calls = bool(self.tool_calls)


class _ScriptedLLM:
    model_name = "forced-retry-test"

    def __init__(self, responses: list[_Response]) -> None:
        self.responses = list(responses)
        self.messages_history: list[list[dict[str, Any]]] = []

    def stream_chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[Any] | None = None,
        on_text_chunk: Callable[[str], None] | None = None,
        on_reasoning_chunk: Callable[[str], None] | None = None,
        timeout: int | None = None,
        idle_timeout_s: float | None = None,
        should_cancel: Callable[[], bool] | None = None,
    ) -> _Response:
        self.messages_history.append(list(messages))
        return self.responses.pop(0)

    def chat(self, messages: list[dict[str, Any]], **kwargs: Any) -> _Response:
        return _Response()


def _agent(tmp_path: Path, llm: _ScriptedLLM, tool: _EchoTool) -> AgentLoop:
    registry = ToolRegistry()
    registry.register(tool)
    agent = AgentLoop(registry=registry, llm=llm, max_iterations=4)
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    agent.memory.run_dir = str(run_dir)
    return agent


class TestForcedToolCallRetry:
    def test_fabricated_first_answer_forces_a_real_tool_call(self, tmp_path: Path) -> None:
        tool = _EchoTool()
        llm = _ScriptedLLM(
            [
                _Response(content="Backend NameError persists, cannot proceed."),
                _Response(
                    tool_calls=[__import__("types").SimpleNamespace(
                        id="c1", name="get_research_status", arguments={}
                    )]
                ),
                _Response(content="Research status confirmed ok."),
            ]
        )
        agent = _agent(tmp_path, llm, tool)

        result = agent.run(
            "Please retry now and report what tools you called.",
            session_config={"session_kind": "autonomous_agent"},
        )

        assert tool.calls == 1
        assert result["status"] == "success"
        assert "Research status confirmed ok." in result["content"]

    def test_only_forces_once_per_run(self, tmp_path: Path) -> None:
        """A model that keeps refusing to call a tool must not loop forever -- it only gets
        retried once. Live-tested 2026-08-30 (see the backlog item's Attempts log): a model can
        satisfy the forced retry by narrating a fabricated "tool called -> result" table in prose
        instead of making a real tool call, which `has_tool_calls` alone can't catch. The second
        consecutive no-tool-call answer must NOT be passed through verbatim -- it's replaced with
        an honest failure notice instead of accepted as fact."""
        tool = _EchoTool()
        llm = _ScriptedLLM(
            [
                _Response(content="Backend broken."),
                _Response(content="Still broken, no tools available."),
            ]
        )
        agent = _agent(tmp_path, llm, tool)

        result = agent.run(
            "Please retry now.",
            session_config={"session_kind": "autonomous_agent"},
        )

        assert tool.calls == 0
        assert result["status"] == "success"
        assert "Still broken" not in result["content"]
        assert "could not complete real tool verification" in result["content"]

    def test_exhaustion_writes_trace_event(self, tmp_path: Path) -> None:
        tool = _EchoTool()
        llm = _ScriptedLLM(
            [
                _Response(content="Backend broken."),
                _Response(content="Still broken, fabricated tool table here."),
            ]
        )
        agent = _agent(tmp_path, llm, tool)

        agent.run(
            "Please retry now.",
            session_config={"session_kind": "autonomous_agent"},
        )

        import json

        trace_path = Path(agent.memory.run_dir) / "trace.jsonl"
        events = [
            json.loads(line).get("type")
            for line in trace_path.read_text().splitlines()
            if line.strip()
        ]
        assert "forced_tool_call_retry" in events
        assert "forced_tool_call_retry_exhausted" in events

    def test_non_autonomous_session_is_not_gated(self, tmp_path: Path) -> None:
        tool = _EchoTool()
        llm = _ScriptedLLM([_Response(content="Here is my answer with no tool call.")])
        agent = _agent(tmp_path, llm, tool)

        result = agent.run("Please retry now.", session_config={})

        assert tool.calls == 0
        assert result["status"] == "success"
        assert "Here is my answer" in result["content"]

    def test_non_retry_message_is_not_gated(self, tmp_path: Path) -> None:
        tool = _EchoTool()
        llm = _ScriptedLLM([_Response(content="A normal answer, not a retry.")])
        agent = _agent(tmp_path, llm, tool)

        result = agent.run(
            "What is my current mandate?",
            session_config={"session_kind": "autonomous_agent"},
        )

        assert tool.calls == 0
        assert result["status"] == "success"
