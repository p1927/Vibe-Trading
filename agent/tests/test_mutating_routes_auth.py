"""Every mutating route must carry an auth dependency (a key-configured server must not
let an unauthenticated peer apply model weights, create/delete watches, etc.)."""

from __future__ import annotations

from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

import api_server

# Authorises inline (not via Depends): POST /system/shutdown calls _require_shutdown_authorization.
_INLINE_AUTH = {("POST", "/system/shutdown")}
_SAFE = {"GET", "HEAD", "OPTIONS"}


def _routes(routes):
    for r in routes:
        if hasattr(r, "original_router"):
            yield from _routes(r.original_router.routes)
        elif isinstance(r, APIRoute):
            yield r


def _dep_names(dependant, acc):
    for d in dependant.dependencies:
        acc.add(getattr(d.call, "__name__", str(d.call)))
        _dep_names(d, acc)
    return acc


def test_every_mutating_route_has_an_auth_dependency() -> None:
    open_routes = []
    for r in _routes(api_server.app.routes):
        for method in r.methods - _SAFE:
            if (method, r.path) in _INLINE_AUTH:
                continue
            names = _dep_names(r.dependant, set())
            if not any(n.startswith("require") or "auth" in n.lower() for n in names):
                open_routes.append(f"{method} {r.path}")
    assert not open_routes, f"mutating routes without auth: {sorted(open_routes)}"


# Read routes that are open by convention (no auth dependency). Frozen: a new unauthenticated
# GET fails the test below instead of silently exposing a route. Whether these reads (agent P&L,
# board, memory, positions) need auth is a maintainer decision
# (backlog 2026-10-13-review-vibe-api-dead-and-gaps item 6); until then this list only shrinks.
_OPEN_READS = {
    # probes and docs (docs/redoc authorise inline)
    "/live", "/health", "/ready", "/api", "/docs", "/redoc",
    "/watches", "/watches/live",
    "/autonomous-agents", "/autonomous-agents/stack-health", "/autonomous-agents/proposals/latest",
    "/autonomous-agents/drafts", "/autonomous-agents/commit", "/autonomous-agents/{agent_id}",
    "/autonomous-agents/{agent_id}/pnl-history", "/autonomous-agents/{agent_id}/exit-evaluations",
    "/autonomous-agents/{agent_id}/simulation",
    "/board/agent/{agent_id}/summary", "/board/agent/{agent_id}/wealth-curve",
    "/board/agent/{agent_id}/hindsight", "/board/agent/{agent_id}/hindsight-curves",
    "/board/agent/{agent_id}/decision-quality", "/board/agent/{agent_id}/decisions",
    "/board/model-version-timeline", "/board/weight-proposals", "/board/weight-proposals/applied",
    "/board/retrain-proposals", "/board/retrain-proposals/applied", "/board/learning-activity",
    "/board/advisory/candidates", "/board/advisory/candidate-detail", "/board/advisory/forecast",
    "/board/positions/{agent_id}",
    "/memory/entries", "/memory/entries/{entry_id}", "/memory/entries/{entry_id}/history",
    "/memory/entries/{entry_id}/diff",
}


def test_no_new_unauthenticated_read_route() -> None:
    open_reads = set()
    for r in _routes(api_server.app.routes):
        if "GET" not in r.methods:
            continue
        names = _dep_names(r.dependant, set())
        if not any(n.startswith("require") or "auth" in n.lower() for n in names):
            open_reads.add(r.path)
    assert open_reads <= _OPEN_READS, f"new GET routes without auth: {sorted(open_reads - _OPEN_READS)}"


def test_keyed_server_rejects_unauthenticated_proposal_apply(monkeypatch) -> None:
    monkeypatch.setenv("API_AUTH_KEY", "secret")
    monkeypatch.setattr(api_server, "_API_KEY", "secret")
    client = TestClient(api_server.app, client=("127.0.0.1", 50000))
    for method, path in [
        ("post", "/board/weight-proposals/x/apply"),
        ("post", "/board/retrain-proposals/x/apply"),
        ("delete", "/watches/x"),
    ]:
        assert getattr(client, method)(path).status_code == 401, path
