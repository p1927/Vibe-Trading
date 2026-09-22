"""Fork sidecar: unwrap the ``{"item": ...}`` array envelope some models send (Trade fork).

MiniMax serializes an array argument the way an XML tool-call format spells a list, as
``{"item": [...]}`` (or ``{"item": "one value"}`` when there is a single element), even when the
tool's JSON schema says ``type: array``. The tool's validator then rejects it. Seen on release
for ``record_autonomous_decision.actions_taken`` and ``propose_autonomous_agent.allowed_instruments``
(Trade backlog 2026-09-23-mcp-tool-arg-contracts).

The unwrap happens only where it cannot be misread: the parameter's schema allows an array and
does not allow an object, and the value is a dict whose only key is ``item``.
"""

from __future__ import annotations

from typing import Any


def _allowed_types(schema: Any) -> set[str]:
    if not isinstance(schema, dict):
        return set()
    declared = schema.get("type")
    types = {declared} if isinstance(declared, str) else set(declared or [])
    for key in ("anyOf", "oneOf"):
        for branch in schema.get(key) or []:
            types |= _allowed_types(branch)
    return types


def unwrap_item_arrays(params: dict[str, Any], schema: dict[str, Any] | None) -> dict[str, Any]:
    """Return ``params`` with each ``{"item": X}`` array envelope replaced by its list."""
    properties = (schema or {}).get("properties") or {}
    out = params
    for name, value in params.items():
        if not (isinstance(value, dict) and list(value) == ["item"]):
            continue
        types = _allowed_types(properties.get(name))
        if "array" not in types or "object" in types:
            continue
        inner = value["item"]
        if out is params:
            out = dict(params)
        out[name] = inner if isinstance(inner, list) else [inner]
    return out
