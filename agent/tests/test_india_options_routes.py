"""TestClient coverage for `india_options_routes.py`
(`GET /options/india/underlyings`, `GET /options/research`, `GET /options/india/pop-overlay`)
— previously untested.

Domain: `2026-08-21-agent-api-route-coverage-audit`. Confirmed correctly mounted (via
`register_india_options_routes()`, called from `register_options_routes()` in
`options_routes.py`, itself mounted by `api_server.py`). Both routes wrap tools that make real
market-data/vendor calls (`list_india_underlyings`, `IndiaOptionsResearchTool`) — mocked at the
module-attribute level rather than exercised live. `pop-overlay` is module 4 of the
options-profitability-prediction-platform backlog item (see
.claude/backlog/items/2026-08-22-options-profitability-pop-engine.md) wiring real chain/
forecast data into `pop_engine.compute_chain_pop_overlay`.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

import api_server
import src.api.india_options_routes as india_options_routes


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(api_server, "_API_KEY", "")
    return TestClient(api_server.app, client=("127.0.0.1", 50000))


def test_underlyings_returns_tool_data(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        india_options_routes,
        "list_india_underlyings",
        lambda source: {"indexes": ["NIFTY", "BANKNIFTY"], "equities": ["RELIANCE"]},
    )
    response = client.get("/options/india/underlyings")
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["data"]["indexes"] == ["NIFTY", "BANKNIFTY"]


def test_underlyings_passes_through_source_query_param(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen = {}

    def fake_list(source):
        seen["source"] = source
        return {"indexes": [], "equities": None}

    monkeypatch.setattr(india_options_routes, "list_india_underlyings", fake_list)
    response = client.get("/options/india/underlyings", params={"source": "live"})
    assert response.status_code == 200
    assert seen["source"] == "live"


def test_underlyings_tool_error_returns_502(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(source):
        raise RuntimeError("nselib is down")

    monkeypatch.setattr(india_options_routes, "list_india_underlyings", boom)
    response = client.get("/options/india/underlyings")
    assert response.status_code == 502
    assert response.json() == {"ok": False, "error": "underlyings lookup failed"}


def test_research_requires_ticker(client: TestClient) -> None:
    response = client.get("/options/research")
    assert response.status_code == 400
    assert "ticker" in response.json()["error"]


def test_research_rejects_blank_ticker(client: TestClient) -> None:
    response = client.get("/options/research", params={"ticker": "   "})
    assert response.status_code == 400


class _FakeResearchTool:
    def execute(self, **kwargs):
        return json.dumps({"ok": True, "ticker": kwargs["ticker"], "strategies": []})


def test_research_success_returns_tool_envelope(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(india_options_routes, "IndiaOptionsResearchTool", _FakeResearchTool)
    response = client.get("/options/research", params={"ticker": "NIFTY"})
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["ticker"] == "NIFTY"


class _FakeFailingResearchTool:
    def execute(self, **kwargs):
        return json.dumps({"ok": False, "error": "no strategies found"})


def test_research_tool_reported_failure_returns_502(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(india_options_routes, "IndiaOptionsResearchTool", _FakeFailingResearchTool)
    response = client.get("/options/research", params={"ticker": "NIFTY"})
    assert response.status_code == 502
    assert response.json()["ok"] is False


class _FakeRaisingResearchTool:
    def execute(self, **kwargs):
        raise RuntimeError("yfinance timed out")


def test_research_tool_exception_returns_502(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(india_options_routes, "IndiaOptionsResearchTool", _FakeRaisingResearchTool)
    response = client.get("/options/research", params={"ticker": "NIFTY"})
    assert response.status_code == 502
    assert response.json() == {"ok": False, "error": "options research request failed"}


# --- GET /options/india/pop-overlay -----------------------------------------------------


def _future_expiry_epoch(days_ahead: int = 14) -> int:
    dt = datetime.now(timezone.utc) + timedelta(days=days_ahead)
    return int(dt.replace(hour=0, minute=0, second=0, microsecond=0).timestamp())


class _FakeChainTool:
    """Stand-in for IndiaOptionsChainTool.execute — returns a fixed envelope."""

    envelope: dict = {}

    def execute(self, **kwargs):
        return json.dumps(self.envelope)


def _chain_envelope(*, expiration: int | None, underlying_ltp: float | None = 24000.0) -> dict:
    return {
        "ok": True,
        "market": "india_equity",
        "source": "stock_simulator",
        "data": {
            "ticker": "NIFTY",
            "expiration": expiration,
            "expirations": [expiration] if expiration else [],
            "underlying_ltp": underlying_ltp,
            "lot_size": 75,
            "calls_count": 1,
            "puts_count": 1,
            "calls": [{"strike": 24000.0, "last_price": 150.0, "implied_volatility": 0.15}],
            "puts": [{"strike": 24000.0, "last_price": 140.0, "implied_volatility": 0.16}],
        },
    }


def test_pop_overlay_requires_ticker(client: TestClient) -> None:
    response = client.get("/options/india/pop-overlay")
    assert response.status_code == 422  # FastAPI's own required-query-param rejection


def test_pop_overlay_blank_ticker_returns_400(client: TestClient) -> None:
    response = client.get("/options/india/pop-overlay", params={"ticker": "   "})
    assert response.status_code == 400


def test_pop_overlay_chain_fetch_error_returns_502(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    class _Boom:
        def execute(self, **kwargs):
            return json.dumps({"ok": False, "error": "no chain data"})

    monkeypatch.setattr(india_options_routes, "IndiaOptionsChainTool", _Boom)
    response = client.get("/options/india/pop-overlay", params={"ticker": "NIFTY"})
    assert response.status_code == 502
    assert response.json()["ok"] is False


def test_pop_overlay_missing_expiration_returns_502(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    tool = _FakeChainTool()
    tool.envelope = _chain_envelope(expiration=None)
    monkeypatch.setattr(india_options_routes, "IndiaOptionsChainTool", lambda: tool)
    response = client.get("/options/india/pop-overlay", params={"ticker": "NIFTY"})
    assert response.status_code == 502
    assert "expiry" in response.json()["error"]


def test_pop_overlay_missing_spot_returns_502(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    tool = _FakeChainTool()
    tool.envelope = _chain_envelope(expiration=_future_expiry_epoch(), underlying_ltp=None)
    monkeypatch.setattr(india_options_routes, "IndiaOptionsChainTool", lambda: tool)
    response = client.get("/options/india/pop-overlay", params={"ticker": "NIFTY"})
    assert response.status_code == 502
    assert "spot" in response.json()["error"]


def test_pop_overlay_expiry_in_past_returns_400(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    tool = _FakeChainTool()
    tool.envelope = _chain_envelope(expiration=_future_expiry_epoch(days_ahead=-5))
    monkeypatch.setattr(india_options_routes, "IndiaOptionsChainTool", lambda: tool)
    response = client.get("/options/india/pop-overlay", params={"ticker": "NIFTY"})
    assert response.status_code == 400
    assert "future" in response.json()["error"]


def test_pop_overlay_forecast_unavailable_returns_502(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    tool = _FakeChainTool()
    tool.envelope = _chain_envelope(expiration=_future_expiry_epoch())
    monkeypatch.setattr(india_options_routes, "IndiaOptionsChainTool", lambda: tool)

    def _boom(ticker, horizon_days):
        raise RuntimeError("module 3's quantile_fusion forecast is unavailable right now")

    monkeypatch.setattr(india_options_routes, "_quantile_forecast_for_pop", _boom)
    response = client.get("/options/india/pop-overlay", params={"ticker": "NIFTY"})
    assert response.status_code == 502
    assert response.json()["ok"] is False


def test_pop_overlay_success_returns_scored_overlay(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    tool = _FakeChainTool()
    tool.envelope = _chain_envelope(expiration=_future_expiry_epoch(days_ahead=14))
    monkeypatch.setattr(india_options_routes, "IndiaOptionsChainTool", lambda: tool)

    from trade_integrations.dataflows.options_research.pop_engine import QuantileForecast

    fake_forecast = QuantileForecast(horizon_days=7, quantiles={"p10": -2.0, "p50": 0.0, "p90": 2.0})
    seen_forecast_args = {}

    def _fake_forecast_fn(ticker, horizon_days):
        seen_forecast_args["ticker"] = ticker
        seen_forecast_args["horizon_days"] = horizon_days
        return fake_forecast

    monkeypatch.setattr(india_options_routes, "_quantile_forecast_for_pop", _fake_forecast_fn)

    seen_overlay_kwargs = {}

    def _fake_overlay(**kwargs):
        seen_overlay_kwargs.update(kwargs)
        return [
            {"strike": 24000.0, "option_type": "CE", "pop": {"probability_of_profit": 0.5}},
            {"strike": 24000.0, "option_type": "PE", "pop": {"probability_of_profit": 0.4}},
        ]

    import trade_integrations.dataflows.options_research.pop_engine as pop_engine_mod

    monkeypatch.setattr(pop_engine_mod, "compute_chain_pop_overlay", _fake_overlay)

    seen_event_window_args = {}

    def _fake_event_window(ticker, expiry_days):
        seen_event_window_args["ticker"] = ticker
        seen_event_window_args["expiry_days"] = expiry_days
        return [{"symbol": "RELIANCE", "event_type": "earnings", "days_from_now": 3}]

    monkeypatch.setattr(pop_engine_mod, "event_window_for_ticker", _fake_event_window)

    response = client.get(
        "/options/india/pop-overlay",
        params={"ticker": "NIFTY", "horizon_days": 7, "n_paths": 1000},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["ticker"] == "NIFTY"
    assert body["distribution_type"] == "physical"
    assert body["apply_liquidity_discount"] is False
    assert body["horizon_days"] == 7
    assert body["underlying_ltp"] == 24000.0
    assert len(body["overlay"]) == 2
    assert body["forecast_quantiles"] == {"p10": -2.0, "p50": 0.0, "p90": 2.0}
    assert body["event_risks"] == [{"symbol": "RELIANCE", "event_type": "earnings", "days_from_now": 3}]

    assert seen_forecast_args == {"ticker": "NIFTY", "horizon_days": 7}
    assert seen_overlay_kwargs["spot"] == 24000.0
    assert seen_overlay_kwargs["n_paths"] == 1000
    assert seen_overlay_kwargs["expiry_days"] > 0
    assert seen_overlay_kwargs["apply_liquidity_discount"] is False
    assert len(seen_overlay_kwargs["calls"]) == 1
    assert len(seen_overlay_kwargs["puts"]) == 1
    assert seen_event_window_args["ticker"] == "NIFTY"
    assert seen_event_window_args["expiry_days"] == seen_overlay_kwargs["expiry_days"]


def test_pop_overlay_passes_through_apply_liquidity_discount(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    tool = _FakeChainTool()
    tool.envelope = _chain_envelope(expiration=_future_expiry_epoch(days_ahead=14))
    monkeypatch.setattr(india_options_routes, "IndiaOptionsChainTool", lambda: tool)

    from trade_integrations.dataflows.options_research.pop_engine import QuantileForecast

    fake_forecast = QuantileForecast(horizon_days=7, quantiles={"p10": -2.0, "p50": 0.0, "p90": 2.0})
    monkeypatch.setattr(
        india_options_routes, "_quantile_forecast_for_pop", lambda ticker, horizon_days: fake_forecast
    )

    seen_overlay_kwargs = {}

    def _fake_overlay(**kwargs):
        seen_overlay_kwargs.update(kwargs)
        return []

    import trade_integrations.dataflows.options_research.pop_engine as pop_engine_mod

    monkeypatch.setattr(pop_engine_mod, "compute_chain_pop_overlay", _fake_overlay)
    monkeypatch.setattr(pop_engine_mod, "event_window_for_ticker", lambda ticker, expiry_days: [])

    response = client.get(
        "/options/india/pop-overlay",
        params={"ticker": "NIFTY", "source": "indmoney", "apply_liquidity_discount": True},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["apply_liquidity_discount"] is True
    assert seen_overlay_kwargs["apply_liquidity_discount"] is True


# --- GET /options/india/selector --------------------------------------------------------


def _fake_stage_result(*, expiry_date: str | None, underlying_ltp: float | None = 24000.0):
    from datetime import datetime as _dt
    from datetime import timezone as _tz

    from trade_integrations.dataflows.company_research.models import StageResult

    if expiry_date is None:
        return StageResult(
            stage="chain", status="error", vendor="stock_simulator",
            fetched_at=_dt.now(_tz.utc), errors=["no live option chain for NIFTY"],
        )
    return StageResult(
        stage="chain",
        status="ok",
        vendor="stock_simulator",
        fetched_at=_dt.now(_tz.utc),
        data={
            "underlying": "NIFTY",
            "underlying_ltp": underlying_ltp,
            "expiry_date": expiry_date,
            "atm_strike": 24000.0,
            "chain": [{"strike": 24000.0, "ce": {"ltp": 150.0, "iv": 15.0}, "pe": {"ltp": 140.0, "iv": 15.0}}],
        },
    )


def _future_expiry_date_str(days_ahead: int = 14) -> str:
    from datetime import datetime as _dt
    from datetime import timedelta as _td
    from datetime import timezone as _tz

    return (_dt.now(_tz.utc) + _td(days=days_ahead)).date().isoformat()


def _patch_chain_stage(monkeypatch, result):
    import trade_integrations.dataflows.options_research.sources.chain_openalgo as chain_mod

    monkeypatch.setattr(chain_mod, "fetch_chain_stage", lambda instrument, **kw: result)


def test_selector_requires_ticker(client: TestClient) -> None:
    response = client.get("/options/india/selector", params={"target_profit": 500})
    assert response.status_code == 422  # FastAPI's own required-query-param rejection


def test_selector_requires_target_profit(client: TestClient) -> None:
    response = client.get("/options/india/selector", params={"ticker": "NIFTY"})
    assert response.status_code == 422


def test_selector_blank_ticker_returns_400(client: TestClient) -> None:
    response = client.get(
        "/options/india/selector", params={"ticker": "   ", "target_profit": 500}
    )
    assert response.status_code == 400


def test_selector_ineligible_ticker_returns_400(client: TestClient) -> None:
    response = client.get(
        "/options/india/selector", params={"ticker": "AAPL", "target_profit": 500}
    )
    assert response.status_code == 400
    assert response.json()["ok"] is False


def test_selector_chain_fetch_error_returns_502(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_chain_stage(monkeypatch, _fake_stage_result(expiry_date=None))
    response = client.get(
        "/options/india/selector", params={"ticker": "NIFTY", "target_profit": 500}
    )
    assert response.status_code == 502
    assert response.json()["ok"] is False


def test_selector_missing_spot_returns_502(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_chain_stage(
        monkeypatch,
        _fake_stage_result(expiry_date=_future_expiry_date_str(), underlying_ltp=None),
    )
    response = client.get(
        "/options/india/selector", params={"ticker": "NIFTY", "target_profit": 500}
    )
    assert response.status_code == 502
    assert "spot" in response.json()["error"]


def test_selector_expiry_in_past_returns_400(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_chain_stage(monkeypatch, _fake_stage_result(expiry_date=_future_expiry_date_str(days_ahead=-5)))
    response = client.get(
        "/options/india/selector", params={"ticker": "NIFTY", "target_profit": 500}
    )
    assert response.status_code == 400
    assert "future" in response.json()["error"]


def test_selector_no_candidates_returns_502(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_chain_stage(monkeypatch, _fake_stage_result(expiry_date=_future_expiry_date_str()))

    import trade_integrations.dataflows.options_research.candidate_generator as cg_mod

    monkeypatch.setattr(cg_mod, "generate_candidates", lambda instrument, chain_snapshot: [])
    response = client.get(
        "/options/india/selector", params={"ticker": "NIFTY", "target_profit": 500}
    )
    assert response.status_code == 502
    assert "candidates" in response.json()["error"]


def test_selector_success_returns_ranked_candidates(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_chain_stage(monkeypatch, _fake_stage_result(expiry_date=_future_expiry_date_str(days_ahead=14)))

    import trade_integrations.dataflows.options_research.candidate_generator as cg_mod

    fake_candidate = {
        "name": "long_call",
        "legs": [
            {
                "side": "BUY", "option_type": "CE", "strike": 24000.0, "price": 150.0,
                "quantity": 50, "iv": 15.0,
            }
        ],
        "rationale": "test",
    }
    monkeypatch.setattr(cg_mod, "generate_candidates", lambda instrument, chain_snapshot: [fake_candidate])

    from trade_integrations.dataflows.options_research.pop_engine import QuantileForecast

    fake_forecast = QuantileForecast(horizon_days=7, quantiles={"p10": -2.0, "p50": 0.0, "p90": 2.0})
    monkeypatch.setattr(
        india_options_routes, "_quantile_forecast_for_pop", lambda ticker, horizon_days: fake_forecast
    )

    seen_select_kwargs = {}

    import trade_integrations.dataflows.options_research.options_selector as selector_mod
    from trade_integrations.dataflows.options_research.options_selector import SelectorCandidateResult

    def _fake_select_candidates(candidates, **kwargs):
        seen_select_kwargs.update(kwargs)
        return [
            SelectorCandidateResult(
                name="long_call", legs=fake_candidate["legs"], max_profit=1000.0, max_loss=-7500.0,
                probability_of_profit=0.4, expected_pnl=100.0, entry_iv=0.15,
                risk_reward_ratio=15.0, pop_per_risk=0.00005, meets_target=True,
            )
        ]

    monkeypatch.setattr(selector_mod, "select_candidates", _fake_select_candidates)

    from trade_integrations.dataflows.index_research.horizon import resolve_horizon
    from trade_integrations.dataflows.index_research.models import ConstituentSignal
    from trade_integrations.dataflows.index_research.prediction_algorithms.types import TrackContext

    fake_ctx = TrackContext(
        ticker="NIFTY",
        spot=24000.0,
        horizon=resolve_horizon(7),
        macro_factors={"india_vix": 14.0},
        signals=[ConstituentSignal(symbol="RELIANCE", weight=0.1)],
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.prediction_algorithms.context_builder.context_from_hub",
        lambda ticker, horizon_days=None: fake_ctx,
    )

    response = client.get(
        "/options/india/selector",
        params={"ticker": "NIFTY", "target_profit": 500, "horizon_days": 7, "n_paths": 1000, "rank_by": "pop_per_risk"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["ticker"] == "NIFTY"
    assert body["target_profit"] == 500
    assert body["rank_by"] == "pop_per_risk"
    assert body["distribution_type"] == "physical"
    assert len(body["candidates"]) == 1
    assert body["candidates"][0]["name"] == "long_call"
    assert body["candidates"][0]["meets_target"] is True

    assert seen_select_kwargs["spot"] == 24000.0
    assert seen_select_kwargs["target_profit"] == 500
    assert seen_select_kwargs["n_paths"] == 1000
    assert seen_select_kwargs["rank_by"] == "pop_per_risk"
    assert seen_select_kwargs["expiry_days"] > 0
    # event-window wiring: signals/macro_factors now reach select_candidates instead of
    # always being None (previously the route never passed them at all).
    assert seen_select_kwargs["signals"] == fake_ctx.signals
    assert seen_select_kwargs["macro_factors"] == fake_ctx.macro_factors


def test_selector_degrades_gracefully_without_hub_context(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No cached hub doc for the ticker -> signals/macro_factors stay None, matching
    select_candidates' own no-event-risks default, not a request failure."""
    _patch_chain_stage(monkeypatch, _fake_stage_result(expiry_date=_future_expiry_date_str(days_ahead=14)))

    import trade_integrations.dataflows.options_research.candidate_generator as cg_mod

    fake_candidate = {
        "name": "long_call",
        "legs": [
            {
                "side": "BUY", "option_type": "CE", "strike": 24000.0, "price": 150.0,
                "quantity": 50, "iv": 15.0,
            }
        ],
        "rationale": "test",
    }
    monkeypatch.setattr(cg_mod, "generate_candidates", lambda instrument, chain_snapshot: [fake_candidate])

    from trade_integrations.dataflows.options_research.pop_engine import QuantileForecast

    fake_forecast = QuantileForecast(horizon_days=7, quantiles={"p10": -2.0, "p50": 0.0, "p90": 2.0})
    monkeypatch.setattr(
        india_options_routes, "_quantile_forecast_for_pop", lambda ticker, horizon_days: fake_forecast
    )

    seen_select_kwargs = {}

    import trade_integrations.dataflows.options_research.options_selector as selector_mod
    from trade_integrations.dataflows.options_research.options_selector import SelectorCandidateResult

    def _fake_select_candidates(candidates, **kwargs):
        seen_select_kwargs.update(kwargs)
        return [
            SelectorCandidateResult(
                name="long_call", legs=fake_candidate["legs"], max_profit=1000.0, max_loss=-7500.0,
                probability_of_profit=0.4, expected_pnl=100.0, entry_iv=0.15,
                risk_reward_ratio=15.0, pop_per_risk=0.00005, meets_target=True,
            )
        ]

    monkeypatch.setattr(selector_mod, "select_candidates", _fake_select_candidates)
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.prediction_algorithms.context_builder.context_from_hub",
        lambda ticker, horizon_days=None: None,
    )

    response = client.get(
        "/options/india/selector",
        params={"ticker": "NIFTY", "target_profit": 500, "horizon_days": 7},
    )
    assert response.status_code == 200
    assert seen_select_kwargs["signals"] is None
    assert seen_select_kwargs["macro_factors"] is None


def test_selector_invalid_rank_by_returns_422(client: TestClient) -> None:
    response = client.get(
        "/options/india/selector",
        params={"ticker": "NIFTY", "target_profit": 500, "rank_by": "not_a_real_mode"},
    )
    assert response.status_code == 422
