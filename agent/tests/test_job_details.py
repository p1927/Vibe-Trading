"""Unit tests for scheduled_research.job_details's description/preview registry."""

from __future__ import annotations

from src.scheduled_research import job_details


def test_unknown_job_type_returns_generic_description_with_no_preview():
    detail = job_details.job_type_detail("not_a_real_job_type")

    assert detail.description == job_details.GENERIC_DESCRIPTION
    assert detail.preview is None


def test_empty_job_type_returns_generic_description():
    detail = job_details.job_type_detail("")

    assert detail.description == job_details.GENERIC_DESCRIPTION
    assert detail.preview is None


def test_hub_news_ingest_has_a_live_preview():
    detail = job_details.job_type_detail("hub_news_ingest")

    assert detail.description
    assert detail.preview is not None

    result = detail.preview({"market": "IN", "ticker": "NIFTY", "mode": "light", "sources": "rss"})

    assert result["items"], "expected at least one resolved RSS URL"
    assert all(url.startswith("http") for url in result["items"])
    assert "mode=light" in result["note"]


def test_hub_news_ingest_preview_notes_non_rss_sources_without_enumerating_them():
    detail = job_details.job_type_detail("hub_news_ingest")

    result = detail.preview(
        {"market": "IN", "ticker": "NIFTY", "mode": "full", "sources": "rss,searxng"}
    )

    assert "searxng" in result["note"]


def test_hub_capture_factor_snapshot_preview_lists_only_due_capture_enabled_entities(
    monkeypatch,
):
    fake_registry = {
        "entities": [
            {"id": "NIFTY", "capture_enabled": True, "factor_groups": ["flows", "vol"]},
            {"id": "BANKNIFTY", "capture_enabled": False, "factor_groups": ["flows"]},
            {"id": "RELIANCE", "capture_enabled": True, "factor_groups": ["derivatives"]},
        ]
    }

    def _fake_load_registry(*, create: bool = True):
        return fake_registry

    def _fake_should_capture(entity_id: str, series_type: str, *, registry=None) -> bool:
        entity = next(e for e in fake_registry["entities"] if e["id"] == entity_id)
        return entity["capture_enabled"] and series_type in entity["factor_groups"]

    from trade_integrations.hub_capture import gate as hub_capture_gate
    from trade_integrations.hub_capture import registry as hub_capture_registry

    monkeypatch.setattr(hub_capture_registry, "load_registry", _fake_load_registry)
    monkeypatch.setattr(hub_capture_gate, "should_capture", _fake_should_capture)

    detail = job_details.job_type_detail("hub_capture_factor_snapshot")
    result = detail.preview({})

    entity_ids = {item["entity_id"] for item in result["items"]}
    assert entity_ids == {"NIFTY"}
    assert result["items"][0]["factors"] == ["flows"]


def test_hub_capture_factor_snapshot_preview_filters_by_entity_id(monkeypatch):
    fake_registry = {
        "entities": [
            {"id": "NIFTY", "capture_enabled": True, "factor_groups": ["flows", "vol"]},
            {"id": "BANKNIFTY", "capture_enabled": True, "factor_groups": ["flows"]},
        ]
    }

    def _fake_load_registry(*, create: bool = True):
        return fake_registry

    def _fake_should_capture(entity_id: str, series_type: str, *, registry=None) -> bool:
        entity = next(e for e in fake_registry["entities"] if e["id"] == entity_id)
        return entity["capture_enabled"] and series_type in entity["factor_groups"]

    from trade_integrations.hub_capture import gate as hub_capture_gate
    from trade_integrations.hub_capture import registry as hub_capture_registry

    monkeypatch.setattr(hub_capture_registry, "load_registry", _fake_load_registry)
    monkeypatch.setattr(hub_capture_gate, "should_capture", _fake_should_capture)

    detail = job_details.job_type_detail("hub_capture_factor_snapshot")
    result = detail.preview({"entity_id": "banknifty"})

    assert [item["entity_id"] for item in result["items"]] == ["BANKNIFTY"]


def test_hub_capture_factor_snapshot_preview_empty_when_nothing_due(monkeypatch):
    def _fake_load_registry(*, create: bool = True):
        return {"entities": []}

    from trade_integrations.hub_capture import registry as hub_capture_registry

    monkeypatch.setattr(hub_capture_registry, "load_registry", _fake_load_registry)

    detail = job_details.job_type_detail("hub_capture_factor_snapshot")
    result = detail.preview({})

    assert result["items"] == []
    assert result["note"]
