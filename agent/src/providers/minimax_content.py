"""MiniMax M3 think-block helpers.

MiniMax-M3 embeds chain-of-thought in ``content`` when ``reasoning_split`` is
off (``<think>`` / ``<think>`` tags). Vibe Trading expects thinking
in ``reasoning_content`` so the UI can show it in ThinkingTimeline instead of
the answer bubble.
"""

from __future__ import annotations

import re

_THINK_OPEN = re.compile(r"<\s*(?:redacted_)?think(?:ing)?\s*>", re.IGNORECASE)
_THINK_CLOSE = re.compile(r"<\s*/\s*(?:redacted_)?think(?:ing)?\s*>", re.IGNORECASE)
_THINK_BLOCK = re.compile(
    r"<\s*(?:redacted_)?think(?:ing)?\s*>(.*?)<\s*/\s*(?:redacted_)?think(?:ing)?\s*>",
    re.DOTALL | re.IGNORECASE,
)
_OPEN_PREFIXES = tuple(
    prefix
    for tag in ("<think", "<thinking", "<redacted_thinking")
    for i in range(1, len(tag) + 1)
    for prefix in (tag[:i].lower(), tag[:i])
)


def split_minimax_think_blocks(content: str | None) -> tuple[str, str | None]:
    """Return visible answer text and extracted thinking from MiniMax content."""
    if not content:
        return "", None
    parts = [part.strip() for part in _THINK_BLOCK.findall(content) if part.strip()]
    cleaned = _THINK_BLOCK.sub("", content).strip()
    thinking = "\n\n".join(parts) if parts else None
    return cleaned, thinking


def merge_reasoning(existing: str | None, extra: str | None) -> str | None:
    """Join reasoning fragments without duplicating whitespace."""
    chunks = [part.strip() for part in (existing, extra) if part and part.strip()]
    return "\n\n".join(chunks) if chunks else None


def _holds_open_tag_prefix(text: str) -> bool:
    tail = text.lower()
    return any(tail.endswith(prefix) for prefix in _OPEN_PREFIXES)


class ThinkBlockStreamFilter:
    """Strip MiniMax think tags from streamed ``content`` deltas."""

    def __init__(self) -> None:
        self._buffer = ""
        self._in_think = False

    def feed(self, delta: str) -> tuple[str, str]:
        """Return ``(answer_text, reasoning_text)`` for one streamed delta."""
        if not delta:
            return "", ""
        self._buffer += delta
        answer_parts: list[str] = []
        reasoning_parts: list[str] = []

        while self._buffer:
            if self._in_think:
                close = _THINK_CLOSE.search(self._buffer)
                if close is None:
                    break
                reasoning_parts.append(self._buffer[: close.start()])
                self._buffer = self._buffer[close.end() :]
                self._in_think = False
                continue

            open_match = _THINK_OPEN.search(self._buffer)
            if open_match is None:
                if _holds_open_tag_prefix(self._buffer):
                    break
                answer_parts.append(self._buffer)
                self._buffer = ""
                break

            answer_parts.append(self._buffer[: open_match.start()])
            self._buffer = self._buffer[open_match.end() :]
            self._in_think = True

        return "".join(answer_parts), "".join(reasoning_parts)

    def flush(self) -> tuple[str, str]:
        """Drain any trailing buffered text at stream end."""
        if not self._buffer:
            return "", ""
        if self._in_think:
            reasoning = self._buffer
            self._buffer = ""
            self._in_think = False
            return "", reasoning
        text = self._buffer
        self._buffer = ""
        return text, ""
