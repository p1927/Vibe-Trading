"""Cold first-request imports are warmed at startup, and a failed warm-up never raises."""
from __future__ import annotations

import importlib

import pytest

from src.api import lifecycle


@pytest.fixture(autouse=True)
def wiki_calls(monkeypatch):
    """Stub the wiki parse (it reads ~2k real files) and record how often the warm-up asks for it."""
    from trade_integrations.knowledge_engine import query

    calls: list[int] = []
    monkeypatch.setattr(query, "load_wiki_pages", lambda: calls.append(1) or [])
    return calls


def test_warmup_imports_every_listed_module(monkeypatch, wiki_calls):
    seen: list[str] = []
    monkeypatch.setattr(importlib, "import_module", lambda name: seen.append(name))
    lifecycle._warm_route_imports()
    assert seen == list(lifecycle._WARM_ROUTE_IMPORTS)


def test_warmup_survives_a_failing_import(monkeypatch, wiki_calls):
    seen: list[str] = []

    def fake(name):
        seen.append(name)
        raise ImportError(name)

    monkeypatch.setattr(importlib, "import_module", fake)
    lifecycle._warm_route_imports()  # must not raise
    assert seen == list(lifecycle._WARM_ROUTE_IMPORTS)  # one failure does not skip the rest
    assert wiki_calls == [1]  # an import failure does not skip the wiki parse


def test_warmup_parses_the_wiki_and_survives_its_failure(monkeypatch, wiki_calls):
    lifecycle._warm_route_imports()
    assert wiki_calls == [1]

    from trade_integrations.knowledge_engine import query

    def boom():
        raise OSError("wiki dir unreadable")

    monkeypatch.setattr(query, "load_wiki_pages", boom)
    lifecycle._warm_route_imports()  # must not raise
