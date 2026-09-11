"""`GET /trade/index-prediction` flags an incomplete index plan loudly via `degraded_reason`
while keeping `status: "ok"` (the frontend's artifact-display gate reads `status`).

See Trade's .claude/backlog/items/2026-08-28-cached-prediction-artifact-prediction-empty.md:
an INDmoney 403 produced an artifact with `prediction={}` and `plan_status="incomplete"` that
was served as a plain 200 + ok, with nothing at the response level saying it was degraded.

No network and no hub reads: the hub loader and forecast-fan enrichment are stubbed.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from src.api.trade_routes import _index_artifact_degraded_reason, get_index_prediction


def _artifact(plan_status: str, warnings: list[str] | None = None) -> dict:
    return {
        "plan_status": plan_status,
        "prediction": {} if plan_status == "incomplete" else {"view": "bearish"},
        "data_warnings": list(warnings or []),
        "horizon": {"days": 14},
    }


@pytest.mark.parametrize(
    ("artifact", "expected"),
    [
        (None, None),
        ({}, None),
        (_artifact("ready"), None),
        (
            _artifact("incomplete", ["Live spot unavailable: Indmoney 403", "second"]),
            "Index prediction incomplete: Live spot unavailable: Indmoney 403",
        ),
        (
            _artifact("incomplete"),
            "Index prediction incomplete: no view/contributors available.",
        ),
    ],
)
def test_degraded_reason_helper(artifact, expected):
    assert _index_artifact_degraded_reason(artifact) == expected


def _get(artifact: dict) -> object:
    with patch("src.trade.hub_bridge.load_hub_plan_artifact", return_value=artifact), patch(
        "src.trade.hub_bridge.prefetch_index_hub_plan",
        side_effect=AssertionError("cached artifact exists; must not prefetch"),
    ), patch("src.api.trade_routes._attach_latest_forecast_fan", return_value=None):
        return get_index_prediction(ticker="nifty", horizon_days=14, refresh=False, _auth=None)


def test_get_index_prediction_incomplete_artifact_is_ok_with_degraded_reason():
    resp = _get(_artifact("incomplete", ["Indmoney API HTTP Error 403"]))
    assert resp.status == "ok"
    assert resp.ticker == "NIFTY"
    assert resp.artifact["plan_status"] == "incomplete"
    assert resp.degraded_reason == "Index prediction incomplete: Indmoney API HTTP Error 403"


def test_get_index_prediction_ready_artifact_has_no_degraded_reason():
    resp = _get(_artifact("ready"))
    assert resp.status == "ok"
    assert resp.degraded_reason is None
