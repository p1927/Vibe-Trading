"""GET /trade/index-prediction/history is the ledger bundle only: daily + intraday + meta, one read path."""
from fastapi.testclient import TestClient

import api_server
from trade_integrations.dataflows import prediction_ledger_bridge as bridge


def test_history_route_returns_ledger_bundle_without_rows_alias(monkeypatch) -> None:
    seen: dict = {}

    def fake_bundle(ticker, limit, horizon_days):
        seen.update(ticker=ticker, limit=limit, horizon_days=horizon_days)
        return {"daily": [{"predicted_at": "2026-10-12T05:00:00+00:00"}], "intraday": [], "meta": {"unique_days": 1}}

    monkeypatch.setattr(bridge, "list_forecast_history_bundle", fake_bundle)
    client = TestClient(api_server.app, client=("127.0.0.1", 50000))
    r = client.get("/trade/index-prediction/history", params={"ticker": "nifty", "limit": 999, "horizon_days": 14, "daily_last": "false"})
    assert r.status_code == 200
    body = r.json()
    assert body["daily"] == [{"predicted_at": "2026-10-12T05:00:00+00:00"}]
    assert body["meta"] == {"unique_days": 1} and "rows" not in body
    assert seen == {"ticker": "NIFTY", "limit": 200, "horizon_days": 14}  # daily_last is gone: ignored, never switches path
