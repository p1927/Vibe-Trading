"""Tests for the OPTIONS_AUTO_DISPATCH_THESIS_BREAK env guard.

Default ON (legacy behaviour). Set to a falsey value in dev to keep the
widget-refresh / event-log path while suppressing the per-tick LLM cascade.
"""

from __future__ import annotations

import pytest

from src.scheduled_research.options_jobs import (
    is_options_thesis_break_auto_dispatch_enabled,
)


def test_default_on_when_env_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPTIONS_AUTO_DISPATCH_THESIS_BREAK", raising=False)
    assert is_options_thesis_break_auto_dispatch_enabled() is True


@pytest.mark.parametrize("value", ["0", "false", "no", "off", ""])
def test_falsey_values_disable_auto_dispatch(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("OPTIONS_AUTO_DISPATCH_THESIS_BREAK", value)
    assert is_options_thesis_break_auto_dispatch_enabled() is False


@pytest.mark.parametrize("value", ["1", "true", "yes", "on"])
def test_truthy_values_enable_auto_dispatch(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("OPTIONS_AUTO_DISPATCH_THESIS_BREAK", value)
    assert is_options_thesis_break_auto_dispatch_enabled() is True
