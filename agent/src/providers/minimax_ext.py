"""MiniMax-specific ``extra_body`` handling for ``build_llm`` (an upstream file).

Kept in a sidecar function rather than inline in ``llm.py`` so the
upstream ``build_llm`` body only carries a single call into here.
"""

from __future__ import annotations

from typing import Any


def resolve_extra_body(
    *,
    effort: str,
    use_responses_api: bool,
    openrouter_reasoning_body: bool,
    minimax_reasoning_split: bool,
) -> dict[str, Any] | None:
    """Build the ``extra_body`` kwarg for ``ChatOpenAI``, layering MiniMax on top.

    MiniMax M3 emits chain-of-thought inline in ``content`` unless asked to
    split it into a separate reasoning channel via ``reasoning_split`` /
    ``thinking``.
    """
    extra_body: dict[str, Any] | None = (
        {"reasoning": {"effort": effort}}
        if effort and not use_responses_api and openrouter_reasoning_body
        else None
    )
    if minimax_reasoning_split:
        extra_body = {
            **(extra_body or {}),
            "reasoning_split": True,
            "thinking": {"type": "adaptive"},
        }
    return extra_body
