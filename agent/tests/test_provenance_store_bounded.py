"""The in-memory provenance registry is bounded per session and cleared with its session."""

from __future__ import annotations

from src.provenance import store as store_module
from src.provenance.models import ProvenanceSource
from src.provenance.store import ProvenanceStore


def _source(session: str, ref: str, raw: str = "x") -> ProvenanceSource:
    return ProvenanceSource(ref_id=ref, session_id=session, display_name=ref, summary="", raw_data=raw)


def test_oldest_sources_drop_past_the_cap(monkeypatch) -> None:
    monkeypatch.setattr(store_module, "_MAX_SOURCES_PER_SESSION", 3)
    store = ProvenanceStore()
    for i in range(7):
        store.add(_source("s1", f"r{i}"))

    assert [s.ref_id for s in store.list_session("s1")] == ["r4", "r5", "r6"]


def test_replacing_a_ref_keeps_position_and_does_not_grow(monkeypatch) -> None:
    monkeypatch.setattr(store_module, "_MAX_SOURCES_PER_SESSION", 3)
    store = ProvenanceStore()
    for ref in ("a", "b", "c"):
        store.add(_source("s1", ref))
    store.add(_source("s1", "b", raw="new"))

    assert [s.ref_id for s in store.list_session("s1")] == ["a", "b", "c"]
    assert store.get("s1", "b").raw_data == "new"


def test_sessions_are_capped_independently() -> None:
    store = ProvenanceStore()
    store.add(_source("s1", "a"))
    store.add(_source("s2", "a"))
    store.clear_session("s1")

    assert store.list_session("s1") == []
    assert len(store.list_session("s2")) == 1
