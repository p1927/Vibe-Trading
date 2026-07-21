"""Tests for compute_hotness decay formula and feature flag switching."""

from __future__ import annotations

import math
from pathlib import Path

import pytest

from src.config.accessor import reset_env_config
from src.memory.persistent import PersistentMemory, compute_hotness


@pytest.fixture(autouse=True)
def _reset_config_cache():
    """Reset env config singleton so monkeypatch.setenv() takes effect."""
    reset_env_config()
    yield
    reset_env_config()


# ---------------------------------------------------------------------------
# Unit tests for compute_hotness formula
# ---------------------------------------------------------------------------


class TestComputeHotness:
    """Validate compute_hotness mathematical properties."""

    def test_zero_access_zero_days_yields_half(self) -> None:
        """sigmoid(log1p(0)) = sigmoid(0) = 0.5; exp(0) = 1 => result ~0.5."""
        result = compute_hotness(0, 0.0)
        assert math.isclose(result, 0.5, rel_tol=1e-9)

    def test_higher_access_count_increases_hotness(self) -> None:
        """More accesses should increase the frequency signal."""
        low = compute_hotness(0, 0.0)
        high = compute_hotness(10, 0.0)
        assert high > low

    def test_seven_day_half_life(self) -> None:
        """After 7 days, recency signal should halve."""
        at_zero = compute_hotness(10, 0.0)
        at_seven = compute_hotness(10, 7.0)
        assert math.isclose(at_seven, at_zero * 0.5, rel_tol=1e-9)

    def test_sigmoid_prevents_explosion(self) -> None:
        """Even with extreme access_count, result stays below 1.0."""
        result = compute_hotness(1000, 0.0)
        assert result < 1.0

    def test_monotone_decay_with_days(self) -> None:
        """Hotness should monotonically decrease as days increase."""
        values = [compute_hotness(5, d) for d in range(0, 30)]
        for i in range(1, len(values)):
            assert values[i] <= values[i - 1]

    def test_large_days_approaches_zero(self) -> None:
        """After many days, hotness should approach zero."""
        result = compute_hotness(100, 365.0)
        assert result < 0.01


# ---------------------------------------------------------------------------
# Feature flag integration test
# ---------------------------------------------------------------------------


def _create_memory_file(
    tmp_path: Path,
    name: str,
    access_count: int = 5,
    quality_score: float = 0.8,
    last_accessed: str = "2020-01-01T00:00:00",
) -> Path:
    """Create a minimal memory file for testing."""
    slug = name.lower().replace(" ", "_")[:40]
    filename = f"project_{slug}.md"
    path = tmp_path / filename
    frontmatter = (
        f"---\n"
        f"name: {name}\n"
        f"description: {name}\n"
        f"type: project\n"
        f"id: aabb11\n"
        f"created_at: 2020-01-01T00:00:00\n"
        f"updated_at: 2020-01-01T00:00:00\n"
        f"keywords: []\n"
        f"quality_score: {quality_score}\n"
        f"access_count: {access_count}\n"
        f"last_accessed: {last_accessed}\n"
        f"importance: 0.5\n"
        f"related_memories: []\n"
        f"---\n\n"
        f"test body for {name}\n"
    )
    path.write_text(frontmatter, encoding="utf-8")
    return path


class TestFeatureFlagSwitching:
    """Verify that VT_MEMORY_HOTNESS_DECAY flag changes _scan_entries formula."""

    def test_default_uses_ebbinghaus(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """With no flags set, importance uses compute_importance (returns quality_score as-is)."""
        monkeypatch.delenv("VT_MEMORY_HOTNESS_DECAY", raising=False)
        monkeypatch.delenv("VT_MEMORY_DECAY", raising=False)
        reset_env_config()
        _create_memory_file(tmp_path, "test_entry")
        pm = PersistentMemory(memory_dir=tmp_path)
        entries = pm.list_entries()
        assert len(entries) == 1
        # With decay disabled, compute_importance returns quality_score directly
        assert entries[0].importance == 0.8

    def test_hotness_flag_changes_formula(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """When VT_MEMORY_HOTNESS_DECAY=1, _scan_entries uses compute_hotness."""
        monkeypatch.setenv("VT_MEMORY_HOTNESS_DECAY", "1")
        reset_env_config()
        _create_memory_file(tmp_path, "test_entry", access_count=5)
        pm = PersistentMemory(memory_dir=tmp_path)
        entries = pm.list_entries()
        assert len(entries) == 1
        # compute_hotness for access_count=5 at many days since access
        # should be different from plain quality_score=0.8
        importance = entries[0].importance
        assert importance != 0.8
        # Should be positive and less than 1
        assert 0.0 < importance < 1.0
