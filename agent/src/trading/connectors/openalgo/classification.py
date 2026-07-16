"""Curated read/write classification for OpenAlgo REST operations."""

from __future__ import annotations

from src.live.classification import ToolClass

OPENALGO_TOOL_CLASS: dict[str, ToolClass] = {
    # READ
    "funds": ToolClass.READ,
    "positionbook": ToolClass.READ,
    "orderbook": ToolClass.READ,
    "quotes": ToolClass.READ,
    "history": ToolClass.READ,
    "analyzer": ToolClass.READ,
    # WRITE
    "placeorder": ToolClass.WRITE,
    "cancelorder": ToolClass.WRITE,
    "analyzer/toggle": ToolClass.WRITE,
}
