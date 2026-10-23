"""Exercise the actual process dedup state, including inactive sessions and churn."""
import pytest

from src.trade import hub_bridge as hb


@pytest.fixture(autouse=True)
def isolated_cache(monkeypatch):
    monkeypatch.setattr(hb, "session_widget_emitted", {})
    monkeypatch.setattr(hb, "_widget_pruned_at", float("-inf"))


def test_expired_sessions_are_pruned_by_another_session():
    hb._record_widget_emitted("inactive", "NIFTY", 100)
    hb._record_widget_emitted("active", "NIFTY", 650)
    assert not hb._should_emit_widget("active", "NIFTY", 650)
    assert hb._should_emit_widget("new", "NIFTY", 711)
    assert "inactive" not in hb.session_widget_emitted
    assert "active" in hb.session_widget_emitted


def test_capacity_eviction_keeps_recent_sessions_and_keys(monkeypatch):
    monkeypatch.setattr(hb, "_WIDGET_MAX_SESSIONS", 3)
    monkeypatch.setattr(hb, "_WIDGET_MAX_KEYS_PER_SESSION", 2)
    for sid in ["a", "b", "c", "a", "d"]:
        hb._record_widget_emitted(sid, "NIFTY", 100)
    assert list(hb.session_widget_emitted) == ["c", "a", "d"]
    for key in ["X", "Y", "X", "Z"]:
        hb._record_widget_emitted("d", key, 100)
    assert list(hb.session_widget_emitted["d"]) == ["X:options", "Z:options"]
    assert hb._should_emit_widget("d", "Y", 100)
    assert not hb._should_emit_widget("d", "X", 100)


def test_clock_rollback_does_not_suppress_widget_indefinitely():
    hb._record_widget_emitted("s", "NIFTY", 1000)
    assert hb._should_emit_widget("s", "NIFTY", 500)
    assert hb.session_widget_emitted == {}


def test_boundary_ttl_and_widget_kinds_remain_independent():
    hb._record_widget_emitted("s", "NIFTY", 100, widget_kind="index")
    assert not hb._should_emit_widget("s", "nifty", 699, widget_kind="index")
    assert hb._should_emit_widget("s", "nifty", 699, widget_kind="options")
    assert hb._should_emit_widget("s", "NIFTY", 700, widget_kind="index")
