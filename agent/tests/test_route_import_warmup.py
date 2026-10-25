"""Cold first-request imports are warmed at startup, and a failed warm-up never raises."""
from __future__ import annotations

import importlib

from src.api import lifecycle


def test_warmup_imports_every_listed_module(monkeypatch):
    seen: list[str] = []
    monkeypatch.setattr(importlib, "import_module", lambda name: seen.append(name))
    lifecycle._warm_route_imports()
    assert seen == list(lifecycle._WARM_ROUTE_IMPORTS)


def test_warmup_survives_a_failing_import(monkeypatch):
    seen: list[str] = []

    def fake(name):
        seen.append(name)
        raise ImportError(name)

    monkeypatch.setattr(importlib, "import_module", fake)
    lifecycle._warm_route_imports()  # must not raise
    assert seen == list(lifecycle._WARM_ROUTE_IMPORTS)  # one failure does not skip the rest
