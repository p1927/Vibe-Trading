"""The one 502 mapping the trade routes share (`helpers.bad_gateway_on_error`)."""

from __future__ import annotations

import logging
from unittest.mock import patch

import pytest
from fastapi import HTTPException

from src.api.helpers import bad_gateway_on_error, redacted_error_detail

log = logging.getLogger("test_bad_gateway_on_error")


def test_failure_in_the_body_becomes_a_502_with_a_redacted_detail_and_is_logged(caplog):
    with caplog.at_level(logging.ERROR, logger=log.name):
        with pytest.raises(HTTPException) as caught:
            with bad_gateway_on_error(log, "thing %s failed", "X"):
                raise RuntimeError("boom reading /Users/someone/private/file.json")
    assert caught.value.status_code == 502
    assert caught.value.detail == redacted_error_detail(RuntimeError("boom reading /Users/someone/private/file.json"))
    assert isinstance(caught.value.__cause__, RuntimeError)
    assert any("thing X failed" in r.getMessage() and r.exc_info for r in caplog.records)


def test_return_inside_the_body_passes_through():
    def handler():
        with bad_gateway_on_error(log, "unused"):
            return "ok"

    assert handler() == "ok"


def test_an_http_exception_in_the_body_is_converted_too_like_the_inline_blocks_did():
    with pytest.raises(HTTPException) as caught:
        with bad_gateway_on_error(log, "unused"):
            raise HTTPException(status_code=404, detail="x")
    assert caught.value.status_code == 502


def test_a_converted_route_maps_an_upstream_failure_to_502():
    from src.api.trade_routes import get_index_prediction_jobs

    with patch("src.trade.index_prediction_jobs.list_index_prediction_jobs", side_effect=RuntimeError("down")):
        with pytest.raises(HTTPException) as caught:
            get_index_prediction_jobs(_auth=None)
    assert caught.value.status_code == 502 and "down" in str(caught.value.detail)
