"""Each OCR completion is one row in Trade's LLM call log, with the provider's token counts."""

from __future__ import annotations

import contextlib
from types import SimpleNamespace

import src.providers.trade_minimax_pace as pace
from src.tools.ocr.llm_vision_ocr import LlmVisionOcrEngine


def test_ocr_completion_hands_usage_to_trades_call_log(monkeypatch):
    seen: dict = {}

    @contextlib.contextmanager
    def span(provider, model, tier="vibe"):
        yield seen
        seen["span"] = (provider, model, tier)

    monkeypatch.setattr(pace, "trade_llm_span", span)
    response = SimpleNamespace(usage=SimpleNamespace(prompt_tokens=11, completion_tokens=4))
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **kw: response)))
    out = LlmVisionOcrEngine._create_logged(client, {"provider": "openai", "model": "m"}, model="m")
    assert out is response
    assert (seen["input_tok"], seen["output_tok"], seen["span"]) == (11, 4, ("openai", "m", "vibe-ocr"))
