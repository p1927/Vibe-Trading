"""Shared fixtures for memory benchmark tests."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

# Project root (Vibe-Trading/)
PROJECT_ROOT = Path(__file__).resolve().parents[4]
CORPUS_DIR = PROJECT_ROOT / "tmp" / "benchmark_corpus"


@pytest.fixture(scope="session")
def memories_corpus() -> list[dict[str, Any]]:
    """Load the 200-entry memory corpus with lifecycle metadata."""
    path = CORPUS_DIR / "memories_with_lifecycle.json"
    with open(path, encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture(scope="session")
def queries_dataset() -> list[dict[str, Any]]:
    """Load the 50-query evaluation dataset with ground-truth top-5."""
    path = CORPUS_DIR / "queries.json"
    with open(path, encoding="utf-8") as f:
        return json.load(f)
