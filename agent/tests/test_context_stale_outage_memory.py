"""ContextBuilder auto-recall must flag stale outage/error memories.

Regression coverage for
.claude/backlog/items/2026-08-29-autonomous-agent-retry-fix-not-live-effective.md: a recalled
memory describing a past infra failure (a NameError, "backend broken", ...) is a snapshot of
system state at write time, not a durable fact. Past a short trust window, ContextBuilder must
inject it with an explicit "reverify before trusting" wrapper rather than as plain fact — this is
what stopped the model from confidently restating a long-resolved outage instead of calling the
real tool.
"""

from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.agent.context import ContextBuilder
from src.agent.memory import WorkspaceMemory
from src.agent.tools import ToolRegistry
from src.memory.persistent import MemoryEntry


def _entry(title: str, description: str, age_seconds: float) -> MemoryEntry:
    return MemoryEntry(
        path=Path("/tmp/fake.md"),
        title=title,
        description=description,
        memory_type="project",
        body="some body text",
        modified_at=time.time() - age_seconds,
        created_at=time.time() - age_seconds,
    )


def _builder() -> ContextBuilder:
    registry = ToolRegistry()
    memory = MagicMock(spec=WorkspaceMemory)
    return ContextBuilder(registry=registry, memory=memory)


class TestIsStaleOutageMemory:
    def test_fresh_outage_note_not_flagged(self) -> None:
        entry = _entry(
            "OpenAlgo backend os NameError still active",
            "backend broken",
            age_seconds=60,
        )
        assert ContextBuilder._is_stale_outage_memory(entry) is False

    def test_old_outage_note_flagged(self) -> None:
        entry = _entry(
            "OpenAlgo backend os NameError still active",
            "backend broken",
            age_seconds=3 * 3600,
        )
        assert ContextBuilder._is_stale_outage_memory(entry) is True

    def test_old_non_outage_note_not_flagged(self) -> None:
        entry = _entry(
            "User prefers short strangle sizing",
            "risk preference note",
            age_seconds=3 * 3600,
        )
        assert ContextBuilder._is_stale_outage_memory(entry) is False


class TestBuildMessagesWrapsStaleOutageRecall:
    def test_stale_outage_recall_gets_unverified_wrapper(self) -> None:
        stale_entry = _entry(
            "OpenAlgo backend os NameError still active on 25AUG26",
            "OpenAlgo backend os NameError still active on 25AUG26",
            age_seconds=3 * 3600,
        )
        persistent_memory = MagicMock()
        persistent_memory.find_relevant.return_value = [stale_entry]

        builder = _builder()
        builder._persistent_memory = persistent_memory

        messages = builder.build_messages("Please retry now and report tool results.")
        user_content = messages[-1]["content"]

        assert "<recalled-memories>" in user_content
        assert "UNVERIFIED" in user_content
        assert "re-check current state" in user_content

    def test_fresh_recall_has_no_wrapper(self) -> None:
        fresh_entry = _entry(
            "OpenAlgo backend os NameError still active on 29AUG26",
            "OpenAlgo backend os NameError still active on 29AUG26",
            age_seconds=60,
        )
        persistent_memory = MagicMock()
        persistent_memory.find_relevant.return_value = [fresh_entry]

        builder = _builder()
        builder._persistent_memory = persistent_memory

        messages = builder.build_messages("Please retry now and report tool results.")
        user_content = messages[-1]["content"]

        assert "<recalled-memories>" in user_content
        assert "UNVERIFIED" not in user_content
