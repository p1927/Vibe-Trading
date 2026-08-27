"""TestClient coverage for `knowledge_engine_routes.py` — module 8's
human-facing wiki/strategy-catalog/news-derived-concept browser routes.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import api_server


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(api_server, "_API_KEY", "")
    return TestClient(api_server.app, client=("127.0.0.1", 50000))


def test_knowledge_wiki_returns_results(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    import trade_integrations.knowledge_engine.query as query_mod

    fake_results = [{"score": 2.0, "slug": "concepts/kelly-criterion", "title": "Kelly Criterion"}]
    captured: dict = {}

    def _fake_query_wiki(*, text=None, tags=None, wiki_type=None, limit=10):
        captured.update(text=text, tags=tags, wiki_type=wiki_type, limit=limit)
        return fake_results

    monkeypatch.setattr(query_mod, "query_wiki", _fake_query_wiki)

    response = client.get("/knowledge/wiki", params={"text": "kelly", "tags": "risk,sizing", "limit": 5})
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["count"] == 1
    assert body["results"] == fake_results
    assert captured == {"text": "kelly", "tags": ["risk", "sizing"], "wiki_type": None, "limit": 5}


def test_knowledge_wiki_query_failure_returns_502(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    import trade_integrations.knowledge_engine.query as query_mod

    def _boom(**kwargs):
        raise RuntimeError("wiki dir missing")

    monkeypatch.setattr(query_mod, "query_wiki", _boom)

    response = client.get("/knowledge/wiki")
    assert response.status_code == 502
    assert response.json()["ok"] is False


def test_knowledge_wiki_page_found(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    import trade_integrations.knowledge_engine.query as query_mod

    fake_page = {
        "slug": "concepts/kelly-criterion",
        "found": True,
        "title": "Kelly Criterion",
        "content": "# Kelly Criterion\n\n...",
    }
    monkeypatch.setattr(query_mod, "get_wiki_page", lambda slug: fake_page)

    response = client.get("/knowledge/wiki/concepts/kelly-criterion")
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["found"] is True
    assert body["content"] == "# Kelly Criterion\n\n..."


def test_knowledge_wiki_page_not_found(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    import trade_integrations.knowledge_engine.query as query_mod

    monkeypatch.setattr(query_mod, "get_wiki_page", lambda slug: {"slug": slug, "found": False})

    response = client.get("/knowledge/wiki/concepts/does-not-exist")
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["found"] is False


def test_knowledge_strategies_returns_results(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    import trade_integrations.knowledge_engine.query as query_mod

    fake_results = [{"key": "momentum", "score": 1.0, "label": "Momentum"}]
    captured: dict = {}

    def _fake_query_strategies(*, market_view=None, risk_profile=None, horizon=None, tags=None, limit=5):
        captured.update(
            market_view=market_view, risk_profile=risk_profile, horizon=horizon, tags=tags, limit=limit
        )
        return fake_results

    monkeypatch.setattr(query_mod, "query_strategies", _fake_query_strategies)

    response = client.get("/knowledge/strategies", params={"market_view": "bullish", "risk_profile": "defined_risk"})
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["results"] == fake_results
    assert captured["market_view"] == "bullish"
    assert captured["risk_profile"] == "defined_risk"
    assert captured["limit"] == 5


def test_knowledge_news_derived_strategies_returns_results(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    import trade_integrations.knowledge_engine.query as query_mod

    fake_results = [{"score": 1.5, "text": "Fade the gap on RBI surprise cuts", "tactic_kind": "fade"}]
    monkeypatch.setattr(query_mod, "query_news_derived_strategies", lambda **kwargs: fake_results)

    response = client.get("/knowledge/news-derived-strategies")
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["results"] == fake_results


def test_knowledge_news_derived_strategies_failure_returns_502(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    import trade_integrations.knowledge_engine.query as query_mod

    def _boom(**kwargs):
        raise RuntimeError("news_hub_bridge unavailable")

    monkeypatch.setattr(query_mod, "query_news_derived_strategies", _boom)

    response = client.get("/knowledge/news-derived-strategies")
    assert response.status_code == 502
    assert response.json()["ok"] is False
