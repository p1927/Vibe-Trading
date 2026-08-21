"""News Hub boundary adapter for ``stock_news_tool.py``.

Per the Hub boundary contract (``docs/news-hub-bridge.md``), ``StockNewsTool``
never hands vendor articles straight to the caller: every fetch is ingested
into ``trade_integrations.dataflows.news_hub_bridge`` and the response is read
back from the Hub. Extracted from ``stock_news_tool.py`` — a fork-only
addition on top of upstream's own vendor-fetch/tool-envelope code in that
file.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from src.tools.stock_news_tool import _snippet


def _article_to_hub_row(article: dict[str, Any], *, vendor: str) -> dict[str, Any]:
    """Project a compact ``{title,url,source,published,snippet}`` article into
    the row shape ``news_hub_bridge.ingest_rows_to_hub`` expects.
    """
    url = str(article.get("url") or "")
    source = str(article.get("source") or vendor)
    published = str(article.get("published") or "")
    return {
        "title": str(article.get("title") or ""),
        "summary": str(article.get("snippet") or ""),
        "url": url,
        "source": source,
        "published_at": published,
        "sources": [
            {
                "vendor": vendor,
                "publisher": source,
                "url": url,
                "fetched_at": datetime.now(timezone.utc).isoformat(),
            }
        ],
    }


def _hub_headline_to_article(item: dict[str, Any]) -> dict[str, Any]:
    """Map a hub headline dict (distilled event or staging ref) back into the
    tool's stable ``{title,url,source,published,snippet}`` article shape.
    """
    sources = item.get("sources") or []
    first_source = sources[0] if sources and isinstance(sources[0], dict) else {}
    return {
        "title": _snippet(item.get("title")) or str(item.get("title") or ""),
        "url": item.get("url") or first_source.get("url") or "",
        "source": item.get("source") or first_source.get("publisher") or "",
        "published": item.get("published_at") or item.get("publish_day") or "",
        "snippet": _snippet(item.get("content_summary") or item.get("summary") or ""),
    }


def _ingest_and_read_from_hub(
    articles: list[dict[str, Any]],
    *,
    ticker: str,
    market: str,
    vendor: str,
    limit: int,
) -> list[dict[str, Any]]:
    """Ingest freshly fetched vendor articles into the Hub, then read the
    response back from the Hub (never serve vendor articles directly).

    Raises:
        Exception: Any Hub-side failure (trade stack unavailable, ingest gate
            blocked, etc.) — propagated to the caller, which turns it into the
            tool's error envelope. There is no silent fallback to raw vendor
            data; per the Hub boundary contract, this tool only serves what
            the Hub has ingested and enriched.
    """
    from trade_integrations.dataflows import news_hub_bridge as hub

    rows = [
        _article_to_hub_row(a, vendor=vendor)
        for a in articles
        if isinstance(a, dict) and a.get("title")
    ]
    if rows:
        collection_day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        hub.ingest_rows_to_hub(
            rows,
            ticker=ticker,
            market=market,
            collection_day=collection_day,
        )
    items = hub.query_with_staging(ticker=ticker, market=market, limit=limit)
    return [_hub_headline_to_article(item) for item in items][:limit]
