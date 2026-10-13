"""Thesis-break widget refresh persists through the one store writer (D306), in this tier's home."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.scheduled_research import options_jobs


def _run(monkeypatch, tmp_path, widget_id):
    monkeypatch.setenv("VIBE_TRADING_HOME", str(tmp_path))
    monkeypatch.setattr(options_jobs, "is_options_monitor_active", lambda: True)
    monkeypatch.setattr(options_jobs, "is_options_thesis_break_auto_dispatch_enabled", lambda: False)
    import trade_integrations.context.hub as hub
    import trade_integrations.dataflows.index_research.pipeline_cancel as pc
    import trade_integrations.dataflows.options_research.aggregator as agg
    import trade_integrations.dataflows.options_research.market as mkt
    import trade_integrations.dataflows.options_research.widget_payload as wp
    import trade_integrations.monitor.execution_ledger as led
    import trade_integrations.monitor.service as svc

    monkeypatch.setattr(led, "list_open_entries", lambda: [{"widget_id": "tp_NIFTY_aaaaaaaaaaaa", "underlying": "nifty"}])
    monkeypatch.setattr(svc.MonitorService, "evaluate_position_thesis",
                        lambda self, wid: SimpleNamespace(broken=True, reasons=["r"], severity="high"))
    monkeypatch.setattr(mkt, "is_options_research_eligible", lambda u: True)
    monkeypatch.setattr(agg, "run_options_research", lambda *a, **k: object())
    monkeypatch.setattr(hub, "save_options_research", lambda d: None)
    monkeypatch.setattr(wp, "build_options_trade_widget_from_doc",
                        lambda doc, **k: {"widget_id": widget_id, "type": "trade_plan.widget"})
    monkeypatch.setattr(pc, "emit_stage_event", lambda m: None)
    return options_jobs.run_options_position_monitor_job()


def test_refreshed_widget_lands_in_store_dir(monkeypatch, tmp_path):
    _run(monkeypatch, tmp_path, "tp_NIFTY_bbbbbbbbbbbb")
    assert (tmp_path / "trade_widgets" / "tp_NIFTY_bbbbbbbbbbbb.json").is_file()


def test_invalid_widget_id_fails_loud(monkeypatch, tmp_path):
    with pytest.raises(RuntimeError):
        _run(monkeypatch, tmp_path, "../evil")
    d = tmp_path / "trade_widgets"
    assert not d.exists() or not list(d.iterdir())


class _Bus:
    def __init__(self):
        self.events = []

    def emit(self, sid, typ, data):
        self.events.append((sid, typ, data))


def _patch_emit(monkeypatch, tmp_path, *, allowed=True, presentable=True):
    monkeypatch.setenv("VIBE_TRADING_HOME", str(tmp_path))
    from src.trade import hub_bridge
    import trade_integrations.autonomous_agents.intent_capabilities as ic
    import trade_integrations.dataflows.index_research.widget_payload as ixp
    import trade_integrations.dataflows.options_research.widget_payload as opp
    import trade_integrations.trade_widgets.presentability as pr

    monkeypatch.setattr(hub_bridge, "ensure_trade_stack_path", lambda: None)
    monkeypatch.setattr(hub_bridge, "_emit", lambda bus, sid, typ, data: bus.emit(sid, typ, data))
    monkeypatch.setattr(ic, "resolve_capabilities", lambda session_config=None: {})
    monkeypatch.setattr(ic, "prefetch_widget_intent_allowed", lambda i, c: allowed)
    monkeypatch.setattr(ic, "attach_capabilities_metadata", lambda w, session_config=None: w)
    monkeypatch.setattr(pr, "apply_widget_metadata", lambda w, i: w)
    monkeypatch.setattr(pr, "is_widget_presentable", lambda w, i, session_config=None: presentable)

    def mk(pfx):
        return lambda t, refresh=False, widget_intent="": {"widget_id": f"{pfx}_{t}_cccccccccccc", "type": "trade_plan.widget"}

    monkeypatch.setattr(opp, "build_options_trade_widget", mk("tp"))
    monkeypatch.setattr(ixp, "build_index_trade_widget", mk("ti"))
    hub_bridge.session_widget_emitted.clear()
    return hub_bridge


@pytest.mark.parametrize("kind,wid", [("options", "tp_NIFTY_cccccccccccc"), ("index", "ti_NIFTY_cccccccccccc")])
def test_emit_trade_widget_persists_emits_and_dedups(monkeypatch, tmp_path, kind, wid):
    hb = _patch_emit(monkeypatch, tmp_path)
    bus = _Bus()
    assert hb.emit_trade_widget(bus, "s1", "NIFTY", kind=kind, widget_intent="x") is True
    assert (tmp_path / "trade_widgets" / f"{wid}.json").is_file()
    assert len(bus.events) == 1
    assert hb.emit_trade_widget(bus, "s1", "NIFTY", kind=kind, widget_intent="x") is False
    assert hb.emit_trade_widget(bus, "s1", "NIFTY", kind=kind, widget_intent="x", dedup=False) is True


def test_emit_trade_widget_blocked_or_unknown(monkeypatch, tmp_path):
    hb = _patch_emit(monkeypatch, tmp_path, allowed=False)
    bus = _Bus()
    assert hb.emit_trade_widget(bus, "s1", "NIFTY", kind="options", widget_intent="x") is False
    assert hb.emit_trade_widget(bus, "s1", "NIFTY", kind="bogus", widget_intent="x") is False
    hb = _patch_emit(monkeypatch, tmp_path, presentable=False)
    assert hb.emit_trade_widget(bus, "s1", "NIFTY", kind="index", widget_intent="x") is False
    assert bus.events == []
