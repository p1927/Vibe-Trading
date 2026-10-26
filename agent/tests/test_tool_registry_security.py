"""Security regression tests for default tool exposure."""

from __future__ import annotations

import pytest

from src.tools import build_registry


def test_shell_tools_absent_from_default_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("VIBE_TRADING_ENABLE_SHELL_TOOLS", raising=False)

    registry = build_registry()

    assert "bash" not in registry.tool_names
    assert "background_run" not in registry.tool_names


def test_shell_tools_require_registry_opt_in(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("VIBE_TRADING_ENABLE_SHELL_TOOLS", raising=False)

    registry = build_registry(include_shell_tools=True)

    assert "bash" in registry.tool_names
    assert "background_run" in registry.tool_names


def test_tool_exception_text_is_redacted_for_the_model_and_state() -> None:
    from src.agent.tools import BaseTool, ToolRegistry

    class _Boom(BaseTool):
        name = "boom"
        description = "x"
        parameters = {"type": "object", "properties": {}, "required": []}

        def execute(self, **kwargs):
            raise RuntimeError("401 for url: https://api.example.com/v1/q?apikey=SUPERSECRET1234567890&x=1")

    registry = ToolRegistry()
    registry.register(_Boom())
    out = registry.execute("boom", {})
    assert "SUPERSECRET1234567890" not in out
    assert '"status": "error"' in out
