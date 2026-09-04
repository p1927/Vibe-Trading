"""TestClient coverage for `memory_routes.py` (`/memory/*`) —
2026-08-29-memory-management-http-api.

`test_router_is_mounted_on_the_app` mirrors `test_positions_routes.py`'s own regression test
for the "router defined but never `include_router`'d" bug.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import api_server
import src.memory.persistent as persistent_module


@pytest.fixture
def memory_dir(monkeypatch: pytest.MonkeyPatch) -> Path:
    tmp = Path(tempfile.mkdtemp(prefix="memory_routes_test_"))
    monkeypatch.setattr(persistent_module, "MEMORY_BASE", tmp)
    return tmp


@pytest.fixture
def client(memory_dir: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(api_server, "_API_KEY", "")
    return TestClient(api_server.app, client=("127.0.0.1", 50000))


def _add_entry(**kwargs) -> str:
    from src.memory.persistent import PersistentMemory

    pm = PersistentMemory()
    defaults = dict(name="test entry", content="test body", memory_type="project")
    defaults.update(kwargs)
    path = pm.add(**defaults)
    entry = next(e for e in pm.list_entries() if e.path == path)
    return entry.id


def test_router_is_mounted_on_the_app(client: TestClient) -> None:
    response = client.get("/memory/entries")
    assert response.status_code != 404, (
        "GET /memory/entries returned 404 — memory_router is not mounted on the app. Check "
        "api_server.py includes `from src.api.memory_routes import memory_router` + "
        "`app.include_router(memory_router)`."
    )
    assert response.status_code == 200


def test_list_entries_empty(client: TestClient) -> None:
    response = client.get("/memory/entries")
    assert response.status_code == 200
    assert response.json() == {"entries": []}


def test_list_entries_filters_by_agent_id(client: TestClient) -> None:
    _add_entry(name="scoped", agent_id="aa_1")
    _add_entry(name="other-scoped", agent_id="aa_2")
    _add_entry(name="unscoped")

    response = client.get("/memory/entries", params={"agent_id": "aa_1"})
    assert response.status_code == 200
    titles = [e["title"] for e in response.json()["entries"]]
    assert titles == ["scoped"]


def test_list_entries_unscoped_only(client: TestClient) -> None:
    _add_entry(name="scoped", agent_id="aa_1")
    _add_entry(name="unscoped")

    response = client.get("/memory/entries", params={"unscoped_only": True})
    titles = [e["title"] for e in response.json()["entries"]]
    assert titles == ["unscoped"]


def test_list_entries_includes_staleness_signals(client: TestClient) -> None:
    _add_entry(name="signal-test")
    response = client.get("/memory/entries")
    entry = response.json()["entries"][0]
    for field in ("last_accessed", "quality_score", "access_count", "importance"):
        assert field in entry


def test_list_entries_sort_importance_ascending(client: TestClient, memory_dir: Path) -> None:
    def _write(name: str, quality_score: float) -> None:
        (memory_dir / f"{name}.md").write_text(
            "---\n"
            f"name: {name}\n"
            "description: test\n"
            "type: project\n"
            f"quality_score: {quality_score}\n"
            "access_count: 0\n"
            "---\n\nbody\n",
            encoding="utf-8",
        )

    _write("high-quality", 0.9)
    _write("low-quality", 0.1)

    response = client.get("/memory/entries", params={"sort": "importance"})
    titles = [e["title"] for e in response.json()["entries"]]
    assert titles == ["low-quality", "high-quality"]


def test_get_entry_detail_includes_body(client: TestClient) -> None:
    entry_id = _add_entry(name="detail-test", content="the full body text")
    response = client.get(f"/memory/entries/{entry_id}")
    assert response.status_code == 200
    body = response.json()
    assert body["title"] == "detail-test"
    assert "the full body text" in body["body"]


def test_get_entry_unknown_id_is_404(client: TestClient) -> None:
    response = client.get("/memory/entries/deadbe")
    assert response.status_code == 404


def test_patch_entry_updates_body(client: TestClient, memory_dir: Path) -> None:
    entry_id = _add_entry(name="editable", content="original body")

    response = client.patch(f"/memory/entries/{entry_id}", json={"body": "edited body"})

    assert response.status_code == 200
    assert response.json()["body"] == "edited body"
    # Persisted to disk, not just the response.
    files = list(memory_dir.glob("*.md"))
    text = next(f.read_text() for f in files if f.name != "MEMORY.md")
    assert "edited body" in text
    assert "original body" not in text


def test_patch_entry_requires_body_or_description(client: TestClient) -> None:
    entry_id = _add_entry(name="editable2")
    response = client.patch(f"/memory/entries/{entry_id}", json={})
    assert response.status_code == 400


def test_delete_entry_archives_and_hides_from_list(client: TestClient, memory_dir: Path) -> None:
    entry_id = _add_entry(name="to-clear")

    response = client.delete(f"/memory/entries/{entry_id}")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    # No longer in the active list...
    list_response = client.get("/memory/entries")
    assert list_response.json() == {"entries": []}
    # ...but preserved under archive/, not destroyed.
    archived = list((memory_dir / "archive").glob("*.md"))
    assert len(archived) == 1


def test_delete_unknown_entry_is_404(client: TestClient) -> None:
    response = client.delete("/memory/entries/deadbe")
    assert response.status_code == 404


def test_history_returns_commits_after_git_init(client: TestClient, memory_dir: Path) -> None:
    from src.memory.versioning import ensure_repo

    entry_id = _add_entry(name="versioned")
    ensure_repo(memory_dir)
    # A second write after the repo exists produces a real commit for this file.
    from src.memory.persistent import PersistentMemory

    pm = PersistentMemory()
    entry = pm.find_by_id(entry_id)
    pm.update_entry(entry, body="v2 body")

    response = client.get(f"/memory/entries/{entry_id}/history")
    assert response.status_code == 200
    commits = response.json()["commits"]
    assert len(commits) >= 1
    assert any("versioned" in c["message"] or "edit" in c["message"] for c in commits)


def test_history_empty_without_git_repo(client: TestClient) -> None:
    entry_id = _add_entry(name="no-git-yet")
    response = client.get(f"/memory/entries/{entry_id}/history")
    assert response.status_code == 200
    assert response.json()["commits"] == []


def test_diff_between_commits(client: TestClient, memory_dir: Path) -> None:
    from src.memory.versioning import ensure_repo
    from src.memory.persistent import PersistentMemory

    entry_id = _add_entry(name="diffable", content="version one")
    ensure_repo(memory_dir)
    pm = PersistentMemory()
    entry = pm.find_by_id(entry_id)
    pm.update_entry(entry, body="version two")

    history = client.get(f"/memory/entries/{entry_id}/history").json()["commits"]
    assert len(history) >= 2
    newest, oldest = history[0]["sha"], history[-1]["sha"]

    response = client.get(
        f"/memory/entries/{entry_id}/diff", params={"from": oldest, "to": newest}
    )
    assert response.status_code == 200
    diff_text = response.json()["diff"]
    assert "version one" in diff_text or "version two" in diff_text


def test_diff_rejects_invalid_sha(client: TestClient, memory_dir: Path) -> None:
    from src.memory.versioning import ensure_repo

    entry_id = _add_entry(name="bad-sha-test")
    ensure_repo(memory_dir)

    response = client.get(
        f"/memory/entries/{entry_id}/diff",
        params={"from": "--upload-pack=evil", "to": "HEAD"},
    )
    assert response.status_code == 400


def test_invalidate_cache_is_a_noop_when_fts_disabled(client: TestClient) -> None:
    """FTS is off by default (VT_MEMORY_FTS_INDEX unset in tests) — the route must still
    succeed, just reporting nothing reindexed rather than erroring."""
    _add_entry(name="cache-test", content="some content")

    response = client.post("/memory/cache/invalidate")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "reindexed": 0}


def test_invalidate_cache_rebuilds_fts_index(
    client: TestClient, memory_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import src.memory.search_index as si
    from src.config.accessor import reset_env_config

    original_shared = si._shared_index
    si._shared_index = None
    monkeypatch.setattr(si, "_DEFAULT_DB_PATH", tmp_path / "test_fts.db")
    monkeypatch.setenv("VT_MEMORY_FTS_INDEX", "1")
    reset_env_config()
    try:
        entry_id = _add_entry(name="rebuild-me", content="rebuild content")

        response = client.post("/memory/cache/invalidate")

        assert response.status_code == 200
        assert response.json() == {"status": "ok", "reindexed": 1}

        from src.memory.persistent import PersistentMemory

        recalled = PersistentMemory().find_relevant("rebuild content")
        assert any(e.id == entry_id for e in recalled)
    finally:
        reset_env_config()
        if si._shared_index is not None:
            try:
                si._shared_index.close()
            except Exception:
                pass
        si._shared_index = original_shared
