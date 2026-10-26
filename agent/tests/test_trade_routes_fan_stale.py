"""Stale-fan stamping: the read path must flag a fan older than the headline (item 2026-10-26-light-refresh-keeps-stale-forecast-fan)."""
from unittest import mock

from src.api import trade_routes as tr

H = "2026-10-26T02:22:00+00:00"


def test_fan_is_stale_cases():
    assert tr._fan_is_stale(H, "2026-10-25T21:36:00+00:00")  # older: stale
    assert not tr._fan_is_stale(H, H)  # equal as_of
    assert not tr._fan_is_stale(H, "2026-10-26T03:00:00+00:00")  # fan newer than headline
    assert not tr._fan_is_stale(H, "2026-10-26T02:05:00+00:00")  # same run (start vs end stamp)
    assert tr._fan_is_stale(None, H) and tr._fan_is_stale(H, None)  # missing as_of
    assert tr._fan_is_stale("garbage", H)  # unparseable
    assert tr._fan_is_stale("2026-10-26T02:22:00", "2026-10-25T21:36:00+00:00")  # naive vs aware
    assert not tr._fan_is_stale("2026-10-26T02:22:00", "2026-10-26T02:22:00+00:00")
    assert not tr._fan_is_stale("2026-10-26T02:22:00Z", "2026-10-26T02:22:00")


def test_attach_stamps_fan():
    art = {"as_of": H, "prediction": {}}
    band = {"fan_id": "f", "predicted_at": "2026-10-25T21:36:00+00:00", "band": []}
    with mock.patch(
        "trade_integrations.dataflows.index_research.nifty_forecast_fan.load_latest_fan_band",
        return_value=band,
    ):
        tr._attach_latest_forecast_fan(art, "NIFTY")
    fan = art["prediction"]["forecast_fan"]
    assert fan["fan_stale"] is True and fan["fan_as_of"] == "2026-10-25T21:36:00+00:00"
