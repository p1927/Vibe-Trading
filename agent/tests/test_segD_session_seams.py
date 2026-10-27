"""segD seams: store id guard, token-count keys in the result sink, dead webui turn helpers gone."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from src.session.store import SessionStore
from src.tools.redaction import ARGUMENTS_SINK, RESULT_SINK, is_sensitive_arg, redact_payload


@pytest.mark.parametrize("bad", ["..", ".", "", "../x", "a/b", "a\\b", "x\x00y", "a/../../b"])
def test_store_rejects_ids_that_escape_the_base_dir(tmp_path: Path, bad: str) -> None:
    store = SessionStore(tmp_path / "s")
    with pytest.raises(ValueError):
        store.delete_session(bad)
    with pytest.raises(ValueError):
        store.get_session(bad)
    with pytest.raises(ValueError):
        store._attempt_dir("ok", bad)


def test_store_accepts_normal_and_channel_style_ids(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "s")
    assert store.get_session("abc123def456") is None
    assert store.get_session("websocket:chat-1") is None


def test_result_sink_keeps_token_counts_but_not_credentials() -> None:
    for k in ("max_tokens", "total_tokens", "inputTokens", "completion_tokens"):
        assert not is_sensitive_arg(k, sink=RESULT_SINK), k
        assert is_sensitive_arg(k, sink=ARGUMENTS_SINK), k  # strict sink unchanged
    for k in ("tokens", "api_token", "access_token", "token", "total_tokens_secret"):
        assert is_sensitive_arg(k, sink=RESULT_SINK), k
    out = redact_payload({"total_tokens": 5, "access_token": "x"}, sink=RESULT_SINK)
    assert out == {"total_tokens": 5, "access_token": "[redacted]"}


def test_dead_webui_turn_helpers_are_gone() -> None:
    assert importlib.util.find_spec("src.session.webui_turns") is None


def test_report_audit_keeps_negative_signs() -> None:
    from src.tools.report_audit_tool import extract_data_points

    md = "Net income: -25.5亿\nRevenue: 40亿\n\n| Item | Q1 | Q2 |\n|---|---|---|\n| Profit | -3.2 | 4.1 |\n| Margin | −5% | 7% |\n"
    vals = {p["label"]: p["reported_value"] for p in extract_data_points(md)}
    assert vals["Net income"] == -25.5
    assert vals["Revenue"] == 40.0
    assert vals["Profit · Q1"] == -3.2 and vals["Profit · Q2"] == 4.1
    assert vals["Margin · Q1"] == -5.0 and vals["Margin · Q2"] == 7.0


def test_lot_size_unknown_stock_is_none_not_one(monkeypatch) -> None:
    from trade_integrations.stock_simulator import master_contract as mc

    from src.tools import india_options_chain_tool as t

    from trade_integrations.stock_simulator.lot_sizes import LotSizeUnavailable, lot_size

    assert t._lot_size_for("NIFTY") == lot_size("NIFTY")
    # the chain's own expiry decides: the 2025-01-02 weekly is 75 beside the January monthly at 25
    assert t._lot_size_for("NIFTY", "2025-01-02") == 75 and t._lot_size_for("NIFTY", "2025-01-30") == 25
    with pytest.raises(LotSizeUnavailable):  # before the first confirmed row: typed error, never today's lot
        t._lot_size_for("NIFTY", "2020-01-30")
    envelope = json.loads(t._success("NIFTY", "stock_simulator", {"expiry_date": "2020-01-30", "chain": []}, underlying="NIFTY"))
    assert envelope["ok"] is False and "lot size" in envelope["error"]

    def boom(sym):
        raise mc.StockLotSizeUnavailable("none")

    monkeypatch.setattr(mc, "_stock_option_lotsize", boom)
    assert t._lot_size_for("RELIANCE") is None
    monkeypatch.setattr(mc, "_stock_option_lotsize", lambda s: 250)
    assert t._lot_size_for("RELIANCE") == 250


def test_factor_analysis_and_cashflow_paths_are_confined(tmp_path: Path, monkeypatch) -> None:
    import json

    from src.tools.cashflow_analytics_tool import CashFlowPerformanceTool
    from src.tools.factor_analysis_tool import FactorAnalysisTool

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("VIBE_TRADING_ALLOWED_FILE_ROOTS", str(tmp_path))
    monkeypatch.setenv("VIBE_TRADING_ALLOWED_RUN_ROOTS", str(tmp_path))
    out = json.loads(FactorAnalysisTool().execute(factor_csv="/etc/passwd", return_csv="/etc/hosts", output_dir="/etc/segD_out"))
    assert out["status"] == "error" and "invalid path" in out["error"]
    assert not Path("/etc/segD_out").exists()
    res = json.loads(CashFlowPerformanceTool().execute(
        valuations=[{"date": "2025-01-01", "value": 100}, {"date": "2025-02-01", "value": 110}],
        flows_path="/etc/passwd"))
    assert res["status"] == "error" and "outside allowed" in json.dumps(res)


def test_derived_q4_skips_per_share_and_share_count_concepts() -> None:
    from src.tools import financial_statements_tool as fs

    def q(start, end, rev, eps, sh):
        return {"PERIOD_TYPE": fs.sec_frames.QUARTER, "PERIOD_START": start, "REPORT_DATE": end, "FILED": "x",
                "Revenues": rev, "EPS": eps, "Shares": sh}

    frames = {
        ("2024-01-01", "2024-03-31"): q("2024-01-01", "2024-03-31", 10, 1.0, 100),
        ("2024-04-01", "2024-06-30"): q("2024-04-01", "2024-06-30", 10, 1.0, 100),
        ("2024-07-01", "2024-09-30"): q("2024-07-01", "2024-09-30", 10, 1.0, 100),
    }
    annual = {("2024-01-01", "2024-12-31"): {
        "PERIOD_TYPE": "annual", "PERIOD_START": "2024-01-01", "REPORT_DATE": "2024-12-31", "FILED": "y", "FISCAL_YEAR": 2024,
        "FORM": "10-K", "ACCESSION": "a", "Revenues": 50, "EPS": 4.5, "Shares": 110,
        "_units": {"Revenues": "USD", "EPS": "USD/shares", "Shares": "shares"}}}
    fs._synthesize_fiscal_q4(frames, annual)
    q4 = frames[("2024-09-30", "2024-12-31")]
    assert q4["Revenues"] == 20
    assert "EPS" not in q4 and "Shares" not in q4


def test_weekly_and_monthly_indicator_fetch_window_covers_lookback_bars(monkeypatch) -> None:
    import json
    from datetime import datetime

    from src.tools import technical_indicator_tool as t

    seen = {}

    def fake_fetch(*args, **kw):
        seen["start"] = kw.get("start_date") or args[1]
        raise RuntimeError("stop")

    monkeypatch.setattr(t, "fetch_market_data", fake_fetch)
    for interval, min_days in (("1wk", 50 * 7), ("1mo", 50 * 31), ("1d", 100)):
        seen.clear()
        out = json.loads(t.TechnicalIndicatorTool().execute(symbol="RELIANCE.NS", interval=interval, lookback=50))
        assert out["ok"] is False and "start" in seen
        span = (datetime.now() - datetime.strptime(str(seen["start"])[:10], "%Y-%m-%d")).days
        assert span >= min_days, (interval, span)


def test_decay_check_and_scan_share_one_history_window() -> None:
    root = Path(__file__).resolve().parents[1] / "src" / "tools"
    for name in ("sdm_status_tool.py", "sdm_decay_scan_tool.py"):
        assert "limit=DECAY_HISTORY_LIMIT" in (root / name).read_text(), name


def test_openalex_total_outage_raises_instead_of_reporting_missing(monkeypatch) -> None:
    from src.tools import research_papers_tool as rp

    def down(*a, **k):
        raise OSError("connection refused")

    monkeypatch.setattr(rp, "_openalex_get", down)
    with pytest.raises(RuntimeError, match="unavailable"):
        rp._openalex_fetch("openalex_id", ["W1", "W2"])
    out = json.loads(rp.ResearchPapersTool().execute(mode="read", source="openalex", paper_ids=["W123"]))
    assert out["ok"] is False

    calls = iter([OSError("batch down"), {"id": "https://openalex.org/W1"}, OSError("one id 404")])

    def flaky(*a, **k):
        v = next(calls)
        if isinstance(v, Exception):
            raise v
        return v

    monkeypatch.setattr(rp, "_openalex_get", flaky)
    assert [w["id"] for w in rp._openalex_fetch("openalex_id", ["W1", "W2"])] == ["https://openalex.org/W1"]


def test_disposition_effect_with_intraday_winners_is_not_a_fabricated_high() -> None:
    import pandas as pd

    from src.tools.trade_journal_tool import _disposition_effect

    rts = pd.DataFrame({"pnl": [100.0, 50.0, -80.0], "hold_days": [0.0, 0.0, 5.0]})
    out = _disposition_effect(rts)
    assert out["severity"] == "low" and "ratio_loss_to_win_hold" not in out
    rts = pd.DataFrame({"pnl": [100.0, -80.0], "hold_days": [2.0, 5.0]})
    assert _disposition_effect(rts)["severity"] == "high"
