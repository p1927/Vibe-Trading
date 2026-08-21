"""Reusable scripted-LLM stub for full-AgentLoop integration tests.

The ad hoc pattern of "write a one-off class implementing chat()/stream_chat()
that returns canned LLMResponse objects" is duplicated across many test files
(test_agent_loop_content_filter.py, test_agent_loop_stream_retry.py,
test_chat_response_metadata.py, etc.). This module centralizes it: script a
sequence of LLMResponse turns (text and/or tool calls) and drive a real
AgentLoop through them via ``AgentLoop(registry=..., llm=ScriptedChatLLM(...))``,
then assert on what the loop actually did — which tools were called, in what
order, with what arguments — rather than re-deriving a stub per test file.

Existing single-purpose stubs (e.g. content-filter or stream-retry behavior)
are unaffected; this is for new tests that want a plain scripted sequence.
"""

from __future__ import annotations

from typing import Any, Callable

from src.providers.chat import LLMResponse, ToolCallRequest


def llm_text(content: str, **kwargs: Any) -> LLMResponse:
    """Build a final-answer (no tool call) scripted turn."""
    return LLMResponse(content=content, **kwargs)


def llm_tool_call(
    name: str,
    arguments: dict[str, Any] | None = None,
    *,
    call_id: str | None = None,
    **kwargs: Any,
) -> LLMResponse:
    """Build a scripted turn where the LLM requests one tool call."""
    return LLMResponse(
        content="",
        tool_calls=[ToolCallRequest(id=call_id or f"tc_{name}", name=name, arguments=arguments or {})],
        **kwargs,
    )


class ScriptedChatLLM:
    """Fake ChatLLM returning a pre-scripted sequence of LLMResponse turns.

    Pass one LLMResponse per expected loop iteration, e.g.
    ``ScriptedChatLLM([llm_tool_call("get_positions"), llm_text("done")])``.
    If the loop requests more turns than were scripted, the last response
    repeats — so a trailing ``llm_text(...)`` naturally ends the ReAct loop
    without needing to over-script exact iteration counts.
    """

    def __init__(self, responses: list[LLMResponse]) -> None:
        if not responses:
            raise ValueError("ScriptedChatLLM needs at least one response")
        self._responses = responses
        self.calls: list[list[dict[str, Any]]] = []

    def _next(self, messages: list[dict[str, Any]]) -> LLMResponse:
        idx = min(len(self.calls), len(self._responses) - 1)
        self.calls.append(list(messages))
        return self._responses[idx]

    def stream_chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[Any] | None = None,
        on_text_chunk: Callable[[str], None] | None = None,
        on_reasoning_chunk: Callable[[str], None] | None = None,
        timeout: int | None = None,
        idle_timeout_s: float | None = None,
        should_cancel: Callable[[], bool] | None = None,
    ) -> LLMResponse:
        response = self._next(messages)
        if on_text_chunk and response.content:
            on_text_chunk(response.content)
        return response

    def chat(self, messages: list[dict[str, Any]], **_: Any) -> LLMResponse:
        return self._next(messages)

    @property
    def call_count(self) -> int:
        return len(self.calls)
