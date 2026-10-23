"""One durable writer, one strict JSON (D306, D479): `src.core.atomic_write` owns the unique-temp + fsync + os.replace write
and the NaN-refusing `strict_dumps`. Two whole-tree guards keep it that way, with the allow-list rule of the class-sweep
guards (`file: (count, reason)`: a new site fails, a listed file that lost its sites fails: trim the entry).

1. TEMP + REPLACE  `os.replace(` / `mkstemp(` / a ".tmp" literal outside `core/atomic_write.py` (a second copy of the writer).
2. JSON SINK       a `json.dump(s)` result that is written to a file / DB column in the same function (a store that could
                   persist the bare token `NaN`). Wire / prompt / hash uses are not sinks and are not listed.
"""
from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
OWNER = "core/atomic_write.py"
_SKIP = ("/tests/", "/node_modules", "/.venv", "/frontend/", "/skills/")

#: file -> (count, reason)
WRITER_ALLOWED = {
    "channels/qq.py": (1, "streams a size-capped download to a temp file chunk by chunk, then renames: not an in-memory write"),
    "memory/compression.py": (3, "shutil.copy2 archive copy (keeps mtime / mode of the original), not a text write"),
    "memory/versioning.py": (1, "the text of a .gitignore line ('*.tmp'), not a temp file"),
    "scheduled_research/store.py": (1, "os.replace to quarantine a corrupt store aside (a rename, not a write)"),
    "live/runtime/jobstore.py": (1, "os.replace to quarantine a corrupt store aside (a rename, not a write)"),
}
SINK_ALLOWED = {
    "agent/trace.py": (1, "diagnostic JSONL append read back by python json only: refusing a NaN would lose the trace (same policy as its sidecar fsync)"),
    "core/runner.py": (1, "mootdx defaults handed to a child process as an env wire value, not stored"),
    "live/runtime/sweep_latch.py": (2, "O_EXCL claim / latch markers holding a timestamp, an episode id and a pid: no float"),
    "scheduled_research/child_dispatch.py": (2, "temp-dir hand-off to the supervised child and back (read by json.loads in the same run); the job record it feeds is saved by the strict store"),
    "trade/recording_jobs.py": (2, "per-date claim marker holding a job id and a timestamp: no float"),
}
WRITE_ATTRS = {"write_text", "write", "write_bytes", "writelines", "execute", "executemany", "atomic_write_text", "atomic_write_bytes"}


def _files() -> list[Path]:
    return [
        p for p in sorted(SRC.rglob("*.py"))
        if not any(s in p.as_posix() for s in _SKIP) and not p.name.startswith("test_")
    ]


def _rel(p: Path) -> str:
    return p.relative_to(SRC).as_posix()


def _writer_sites(tree: ast.AST) -> int:
    n = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            n += (node.value.id, node.attr) in {("os", "replace"), ("tempfile", "mkstemp"), ("tempfile", "NamedTemporaryFile")}
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) and ".tmp" in node.value:
            n += 1
    return n


def _is_dumps(n: ast.AST) -> bool:
    return (
        isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr in ("dumps", "dump")
        and isinstance(n.func.value, ast.Name) and n.func.value.id == "json"
    )


def _sink_sites(tree: ast.AST) -> int:
    sites: set[int] = set()
    for fn in [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Module))]:
        nodes = list(ast.walk(fn))
        assigned = {
            t.id: n.lineno for n in nodes if isinstance(n, ast.Assign) and any(_is_dumps(x) for x in ast.walk(n.value))
            for t in n.targets if isinstance(t, ast.Name)
        }
        for n in nodes:
            if _is_dumps(n) and n.func.attr == "dump":
                sites.add(n.lineno)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr in WRITE_ATTRS:
                for a in list(n.args) + [k.value for k in n.keywords]:
                    sites.update(x.lineno for x in ast.walk(a) if _is_dumps(x))
                    sites.update(assigned[x.id] for x in ast.walk(a) if isinstance(x, ast.Name) and x.id in assigned)
    return len(sites)


def _check(counter, allowed: dict, owner_skip: bool, hint: str) -> None:
    found = {}
    for p in _files():
        if owner_skip and _rel(p) == OWNER:
            continue
        n = counter(ast.parse(p.read_text(encoding="utf-8")))
        if n:
            found[_rel(p)] = n
    bad = {f: n for f, n in found.items() if f not in allowed or allowed[f][0] != n}
    stale = [f for f in allowed if f not in found]
    assert not bad, f"{hint}; unlisted or changed: {bad}"
    assert not stale, f"allow-list entries with no site left (trim them): {stale}"


def test_no_second_temp_and_replace_writer() -> None:
    _check(_writer_sites, WRITER_ALLOWED, True, "use src.core.atomic_write (atomic_write_text / write_json_atomic), the one durable writer")


def test_every_stored_json_goes_through_strict_dumps() -> None:
    _check(_sink_sites, SINK_ALLOWED, False, "route a stored json.dump(s) through src.core.atomic_write (write_json_atomic / strict_dumps): a NaN must be refused, not stored")
