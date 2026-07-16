"""MiniMax think-block separation tests."""

from __future__ import annotations

import os
from unittest.mock import patch

from src.providers.capabilities import get_provider_capabilities
from src.providers.chat import ChatLLM
from src.providers.llm import build_llm
from src.providers.minimax_content import ThinkBlockStreamFilter, split_minimax_think_blocks


def test_split_minimax_think_blocks() -> None:
    raw = (
        "<think>\nLet me analyze NIFTY options.\n</think>\n"
        "Here is the trade plan."
    )
    cleaned, thinking = split_minimax_think_blocks(raw)
    assert cleaned == "Here is the trade plan."
    assert "NIFTY" in (thinking or "")


def test_think_block_stream_filter() -> None:
    filt = ThinkBlockStreamFilter()
    visible, reasoning = filt.feed("<think>step 1")
    assert visible == ""
    assert reasoning == ""
    visible, reasoning = filt.feed("</think>Answer")
    assert reasoning == "step 1"
    assert visible == "Answer"


def test_minimax_capabilities_capture_reasoning() -> None:
    caps = get_provider_capabilities("minimax", "MiniMax-M3")
    assert caps.capture_reasoning is True
    assert caps.send_reasoning_content is True
    assert caps.minimax_reasoning_split is True


def test_minimax_build_llm_requests_reasoning_split() -> None:
    import src.providers.llm as llm_mod

    llm_mod._dotenv_loaded = True
    captured: dict = {}

    class _FakeChatOpenAI:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

    env = {
        "LANGCHAIN_PROVIDER": "minimax",
        "MINIMAX_API_KEY": "minimax-key",
        "MINIMAX_BASE_URL": "https://api.minimax.io/v1",
        "LANGCHAIN_MODEL_NAME": "MiniMax-M3",
    }
    with patch.dict(os.environ, env, clear=True):
        with patch.object(llm_mod, "ChatOpenAIWithReasoning", _FakeChatOpenAI):
            build_llm()

    assert captured["extra_body"] == {"reasoning_split": True}


class _FakeChunk:
    def __init__(self, *, content: str = "", reasoning: str = "", finish_reason: str = "stop") -> None:
        self.content = content
        self.tool_calls: list = []
        self.additional_kwargs = {"reasoning_content": reasoning} if reasoning else {}
        self.response_metadata = {"finish_reason": finish_reason}
        self.usage_metadata = None

    def __add__(self, other: "_FakeChunk") -> "_FakeChunk":
        return _FakeChunk(
            content=f"{self.content}{other.content}",
            reasoning=(
                f"{self.additional_kwargs.get('reasoning_content', '')}"
                f"{other.additional_kwargs.get('reasoning_content', '')}"
            ),
            finish_reason=other.response_metadata.get("finish_reason", "stop"),
        )


class _FakeStreamingLLM:
    def __init__(self, chunks: list[_FakeChunk]) -> None:
        self.chunks = chunks

    def bind_tools(self, tools: list) -> "_FakeStreamingLLM":
        return self

    def stream(self, messages: list, config: dict | None = None):
        yield from self.chunks


def test_parse_response_strips_embedded_think_blocks() -> None:
    chunk = _FakeChunk(
        content=(
            "<think>internal reasoning</think>"
            "Visible answer"
        )
    )
    with patch.dict(os.environ, {"LANGCHAIN_PROVIDER": "minimax"}, clear=False):
        response = ChatLLM._parse_response(chunk)
    assert response.content == "Visible answer"
    assert response.reasoning_content == "internal reasoning"


def test_stream_chat_routes_embedded_think_blocks_to_reasoning_callback() -> None:
    fake = _FakeStreamingLLM([
        _FakeChunk(content="<think>thinking</think>"),
        _FakeChunk(content="Final answer"),
    ])
    client = ChatLLM.__new__(ChatLLM)
    client.model_name = "MiniMax-M3"
    client._llm = fake
    text_chunks: list[str] = []
    reasoning_chunks: list[str] = []

    with patch.dict(os.environ, {"LANGCHAIN_PROVIDER": "minimax"}, clear=False):
        response = client.stream_chat(
            [{"role": "user", "content": "hi"}],
            on_text_chunk=text_chunks.append,
            on_reasoning_chunk=reasoning_chunks.append,
        )

    assert text_chunks == ["Final answer"]
    assert reasoning_chunks == ["thinking"]
    assert response.content == "Final answer"
    assert response.reasoning_content == "thinking"
