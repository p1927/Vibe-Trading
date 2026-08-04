"""Load skill tool: load full skill documentation by name, in pages.

Skill documents *are* the implementation for a large part of this product — the
bond maths, the implied-vol solver, the impact models and the China market
structure all live in markdown the agent reads and executes. A tool result is
capped at :data:`src.config.limits.TOOL_RESULT_LIMIT` characters, and 31 of the 88
bundled skills exceed it: ``tushare`` delivers 9.7% of its 103,037 characters,
``options-payoff`` 33.8%, ``credit-analysis`` 43.0%. The cut used to be silent,
so the agent could not tell a complete document from an amputated one and had no
way to reach the rest.

The envelope therefore always states how much of the document was delivered and
where to resume, and ``offset`` lets the agent page through the remainder.
"""

from __future__ import annotations

import json
from typing import Any

from src.agent.skills import SkillsLoader
from src.agent.tools import BaseTool
from src.config.limits import TOOL_RESULT_LIMIT

# Characters of skill body per page. The cap applies to the whole serialized
# envelope, so this leaves room for the surrounding JSON keys and counters.
_PAGE_CHARS = TOOL_RESULT_LIMIT - 600

# Floor on the shrink loop so a pathologically escape-heavy document still makes
# forward progress rather than paging one character at a time.
_MIN_PAGE_CHARS = 1_000


class LoadSkillTool(BaseTool):
    """Load the full documentation for a named skill."""

    name = "load_skill"
    description = (
        "Load full documentation for a named skill. Use this to learn about "
        "unfamiliar strategy patterns or workflows before starting. Long skills "
        "are returned in pages: when the result reports next_offset, call again "
        "with that offset to read the rest."
    )
    parameters = {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Skill name (e.g. 'strategy-generate', 'momentum')"},
            "offset": {
                "type": "integer",
                "description": (
                    "Character offset to read from; defaults to 0. Pass the "
                    "next_offset value from a previous call to continue."
                ),
            },
        },
        "required": ["name"],
    }
    repeatable = True

    def __init__(self, skills_loader: SkillsLoader | None = None) -> None:
        """Initialize LoadSkillTool.

        Args:
            skills_loader: SkillsLoader instance; creates one automatically if omitted.
        """
        self._loader = skills_loader or SkillsLoader()

    def execute(self, **kwargs: Any) -> str:
        """Load one page of a skill's documentation.

        Args:
            **kwargs: Must include ``name``; may include ``offset``.

        Returns:
            A JSON envelope carrying the page plus ``total_chars``, ``offset``,
            ``next_offset`` and ``complete`` so a partial read is never mistaken
            for the whole document.
        """
        name = kwargs["name"]
        content = self._loader.get_content(name)
        if content.startswith("Error:"):
            return json.dumps({"status": "error", "content": content}, ensure_ascii=False)

        try:
            offset = max(int(kwargs.get("offset") or 0), 0)
        except (TypeError, ValueError):
            return json.dumps(
                {"status": "error", "content": "Error: offset must be an integer"},
                ensure_ascii=False,
            )

        total = len(content)
        if offset >= total and total:
            return json.dumps(
                {
                    "status": "error",
                    "content": (
                        f"Error: offset {offset} is past the end of skill "
                        f"{name!r} ({total} characters)"
                    ),
                },
                ensure_ascii=False,
            )

        # The cap applies to the serialized envelope, and JSON escaping is
        # content-dependent — a newline-dense page costs two characters per
        # line break. Size the page by what it actually serializes to, or a
        # markdown-heavy skill silently loses its tail all over again.
        size = _PAGE_CHARS
        while True:
            page = content[offset : offset + size]
            next_offset = offset + len(page)
            complete = next_offset >= total
            envelope = json.dumps(
                {
                    "status": "ok",
                    "content": page,
                    "total_chars": total,
                    "offset": offset,
                    "next_offset": None if complete else next_offset,
                    "complete": complete,
                },
                ensure_ascii=False,
            )
            if len(envelope) <= TOOL_RESULT_LIMIT or size <= _MIN_PAGE_CHARS:
                return envelope
            # Shrink proportionally to the overflow, then a little further so a
            # page dense in escapes still converges in a couple of rounds.
            size = max(
                _MIN_PAGE_CHARS,
                int(size * TOOL_RESULT_LIMIT / len(envelope)) - 64,
            )
