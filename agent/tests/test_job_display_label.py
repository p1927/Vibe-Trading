"""Per-market jobs of one type must not share a display label (the Prediction tab list showed nine identical rows)."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.trade.index_prediction_jobs import _job_display_label


def _label(**config):
    return _job_display_label(SimpleNamespace(id="job-1", config=config))


@pytest.mark.parametrize(
    "config, expected",
    [
        ({"job_type": "hub_news_ingest", "mode": "full"}, "Hub news ingest (full)"),  # India: unqualified
        ({"job_type": "hub_news_ingest", "mode": "full", "market": "IN"}, "Hub news ingest (full)"),
        ({"job_type": "hub_news_ingest", "mode": "full", "market": "US"}, "Hub news ingest (full) [US]"),
        ({"job_type": "hub_news_ingest", "mode": "light", "market": "latam"}, "Hub news ingest (light) [LATAM]"),
        ({"job_type": "hub_news_entity", "mode": "drain", "market": "EU"}, "Hub news entity pipeline (drain) [EU]"),
        ({"job_type": "index_research", "market": " jp "}, "Full index research (weekly) [JP]"),
        ({"job_type": "hub_news_ingest", "mode": "tight", "market": ""}, "Hub news ingest (tight)"),
        ({"job_type": "unknown_type"}, "unknown_type"),
    ],
)
def test_label_names_the_market(config, expected):
    assert _label(**config) == expected


def test_all_market_variants_of_a_job_have_distinct_labels():
    labels = {_label(job_type="hub_news_ingest", mode="full", market=m) for m in ("US", "JP", "CN", "RU", "ME", "LATAM", "EU")}
    labels.add(_label(job_type="hub_news_ingest", mode="full"))
    assert len(labels) == 8
