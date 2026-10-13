"""Contract: every HTTP call the Vibe frontend makes must hit a route the real API serves.

A call to a path/method with no route is a 404/405 waiting to happen (the SPA/Vite fallback can even
turn it into a 200 HTML page). Calls are extracted from the client source on every run (no checked-in
snapshot to go stale) and diffed against the real FastAPI app's route table. Run as a script
(`python tests/test_frontend_api_contract.py`) to also print the routes no frontend call reaches.
"""

from __future__ import annotations

import re
from pathlib import Path

from fastapi.routing import APIRoute

import api_server

_SRC = Path(__file__).resolve().parents[2] / "frontend" / "src"
# Every HTTP call goes through api.ts `request()` / `${BASE}` fetch sites, plus the QVeris settings client.
_CLIENT_FILES = ("lib/api.ts", "components/settings/QVerisSettings.tsx")
# Not served by the Vibe API: the exchange is proxied/handled elsewhere (apiAuth.withAuthTicket is checked below).
_CALL = re.compile(r"\b(?:requestJson|request)\s*(?:<)?")
_METHOD = re.compile(r"""method:\s*["'](\w+)["']""")


def _routes(routes):
    for r in routes:
        if hasattr(r, "original_router"):
            yield from _routes(r.original_router.routes)
        elif isinstance(r, APIRoute):
            yield r


def _route_table() -> list[tuple[str, str, re.Pattern[str]]]:
    out = []
    for r in _routes(api_server.app.routes):
        pat = re.compile("^" + re.sub(r"\\\{[^}]*\\\}", "[^/]+", re.escape(r.path)) + "$")
        out += [(m, r.path, pat) for m in r.methods if m not in ("HEAD", "OPTIONS")]
    return out


def _literal_end(text: str, i: int) -> int:
    """Index of the closing quote of the literal opening at text[i] (template `${}` nesting aware)."""
    q, j, depth = text[i], i + 1, 0
    while j < len(text):
        c = text[j]
        if q == "`" and text.startswith("${", j):
            depth += 1
            j += 2
            continue
        if depth and c == "}":
            depth -= 1
        elif not depth and c == q:
            return j
        j += 1
    raise ValueError("unterminated literal")


def _normalise(body: str) -> str:
    """Replace each `${...}` (balanced) with `{x}`, then drop a query-string suffix."""
    out, i = "", 0
    while i < len(body):
        if body.startswith("${", i):
            depth, j = 1, i + 2
            while depth:
                depth += {"{": 1, "}": -1}.get(body[j], 0)
                j += 1
            out += "{x}"
            i = j
        else:
            out += body[i]
            i += 1
    out = out.split("?")[0]
    return re.sub(r"(?<=[^/])\{x\}$", "", out)  # `/path${qs}` -> `/path`


def _method_after(text: str, end: int) -> str:
    """Method option in the enclosing call (GET when absent): scan to its closing paren."""
    depth = 0
    for j in range(end, min(len(text), end + 1500)):
        if text[j] == "(":
            depth += 1
        elif text[j] == ")":
            if depth == 0:
                break
            depth -= 1
    m = _METHOD.search(text[end:j])
    return m.group(1).upper() if m else "GET"


def extract_calls() -> dict[tuple[str, str], str]:
    """{(METHOD, normalised path): file} for every statically-known call in the client files."""
    calls: dict[tuple[str, str], str] = {}
    for rel in _CLIENT_FILES:
        text = (_SRC / rel).read_text()
        starts = []
        for m in _CALL.finditer(text):  # request<T>("/x") / request("/x")
            i = m.end()
            if m.group(0).endswith("<"):  # skip a balanced generic argument
                depth = 1
                while depth:
                    if text[i] == "<":
                        depth += 1
                    elif text[i] == ">" and text[i - 1] != "=":
                        depth -= 1
                    i += 1
            if text[i : i + 1] == "(":
                i += 1
                while text[i].isspace():
                    i += 1
                if text[i] in "`\"'":
                    starts.append((i, False))
        for m in re.finditer(r"`\$\{BASE\}", text):  # fetch(`${BASE}/x`) / withAuthTicket(`${BASE}/x`)
            starts.append((m.start(), True))
        for i, has_base in starts:
            end = _literal_end(text, i)
            body = text[i + 1 : end]
            if has_base:
                body = body[len("${BASE}") :]
            path = _normalise(body)
            if not path.startswith("/") or path == "/{x}" or path.startswith("/{x}"):
                continue  # fully dynamic (e.g. liveLogStreamUrl(path): registry-supplied, server-built)
            via_fetch = has_base and re.search(r"fetch\($", text[max(0, i - 40) : i].rstrip())
            method = _method_after(text, end) if (via_fetch or not has_base) else "GET"  # `${BASE}` outside fetch = EventSource URL
            calls.setdefault((method, path), rel)
    return calls


def test_extractor_finds_the_real_call_surface() -> None:
    calls = extract_calls()
    assert len(calls) > 200, f"extractor regressed: only {len(calls)} calls found in {_CLIENT_FILES}"
    assert ("POST", "/trade/index-prediction/run") in calls or any(p.startswith("/trade/index-prediction") for _, p in calls)


def test_every_frontend_call_has_a_backend_route() -> None:
    table = _route_table()
    missing, wrong_method = [], []
    for (method, path), src in sorted(extract_calls().items()):
        matches = [m for m, _p, pat in table if pat.match(path)]
        if not matches:
            missing.append(f"{method} {path}  ({src})")
        elif method not in matches:
            wrong_method.append(f"{method} {path} -> server has {sorted(matches)}  ({src})")
    assert not missing, "frontend calls with no backend route:\n" + "\n".join(missing)
    assert not wrong_method, "frontend calls with the wrong HTTP method:\n" + "\n".join(wrong_method)


def test_sse_ticket_exchange_route_exists() -> None:
    # apiAuth.withAuthTicket POSTs a literal "/auth/sse-ticket" (not part of api.ts's extracted calls).
    assert any(m == "POST" and p == "/auth/sse-ticket" for m, p, _ in _route_table())


if __name__ == "__main__":
    calls = extract_calls()
    table = _route_table()
    reached = {(m, p) for m, p, pat in table for (cm, cp) in calls if cm == m and pat.match(cp)}
    print(f"{len(calls)} calls, {len(table)} method-routes")
    for m, p, _ in table:
        if (m, p) not in reached:
            print("NO-CALLER", m, p)
