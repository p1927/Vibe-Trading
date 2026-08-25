"""TestClient coverage for `uploads_routes.py` (`/upload`, `/shadow-reports/{id}`) —
previously untested.

Domain: `2026-08-21-agent-api-route-coverage-audit`. Confirmed correctly mounted via
`register_uploads_routes(app)` in `api_server.py`.
"""

from __future__ import annotations

import io
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import api_server


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    uploads_dir = Path(tempfile.mkdtemp(prefix="uploads_routes_test_"))
    home_dir = Path(tempfile.mkdtemp(prefix="uploads_routes_home_"))
    monkeypatch.setattr(api_server, "UPLOADS_DIR", uploads_dir)
    # /shadow-reports reads Path.home() / ".vibe-trading" / "shadow_reports" directly (not a
    # host-module attribute), so isolating it means patching Path.home itself.
    monkeypatch.setattr(Path, "home", staticmethod(lambda: home_dir))
    monkeypatch.setattr(api_server, "_API_KEY", "")
    return TestClient(api_server.app, client=("127.0.0.1", 50000))


def test_upload_rejects_missing_filename(client: TestClient) -> None:
    response = client.post("/upload", files={"file": ("", io.BytesIO(b"data"), "text/plain")})
    assert response.status_code in (400, 422)


@pytest.mark.parametrize("filename", ["malware.exe", "script.sh", "archive.zip", "config.yaml"])
def test_upload_rejects_blocked_extensions(client: TestClient, filename: str) -> None:
    response = client.post("/upload", files={"file": (filename, io.BytesIO(b"data"), "application/octet-stream")})
    assert response.status_code == 400
    assert "not allowed" in response.json()["detail"]


@pytest.mark.parametrize("filename", ["Dockerfile", "dockerfile", "Containerfile"])
def test_upload_rejects_blocked_names(client: TestClient, filename: str) -> None:
    response = client.post("/upload", files={"file": (filename, io.BytesIO(b"FROM x"), "text/plain")})
    assert response.status_code == 400


def test_upload_accepts_allowed_file_and_stores_it(client: TestClient) -> None:
    response = client.post(
        "/upload", files={"file": ("notes.txt", io.BytesIO(b"hello world"), "text/plain")}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["filename"] == "notes.txt"
    assert body["file_path"].startswith("uploads/")
    stored_name = body["file_path"].split("/", 1)[1]
    stored_path = api_server.UPLOADS_DIR / stored_name
    assert stored_path.exists()
    assert stored_path.read_bytes() == b"hello world"


def test_upload_strips_directory_traversal_from_filename(client: TestClient) -> None:
    response = client.post(
        "/upload",
        files={"file": ("../../etc/passwd_notes.txt", io.BytesIO(b"data"), "text/plain")},
    )
    assert response.status_code == 200
    assert response.json()["filename"] == "passwd_notes.txt"


def test_upload_rejects_file_over_size_limit(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(api_server, "MAX_UPLOAD_SIZE", 10)
    monkeypatch.setattr(api_server, "_UPLOAD_CHUNK_SIZE", 4)
    response = client.post(
        "/upload", files={"file": ("big.txt", io.BytesIO(b"x" * 100), "text/plain")}
    )
    assert response.status_code == 413
    leftover = list(api_server.UPLOADS_DIR.glob("*.txt"))
    assert leftover == []


@pytest.mark.parametrize("shadow_id", ["not-a-valid-id", "shadow_short", "shadow_toolongforthis1"])
def test_shadow_report_rejects_malformed_id(client: TestClient, shadow_id: str) -> None:
    response = client.get(f"/shadow-reports/{shadow_id}")
    assert response.status_code == 400


def test_shadow_report_rejects_bad_format(client: TestClient) -> None:
    response = client.get("/shadow-reports/shadow_12345678", params={"format": "docx"})
    assert response.status_code == 400


def test_shadow_report_missing_returns_404(client: TestClient) -> None:
    response = client.get("/shadow-reports/shadow_12345678")
    assert response.status_code == 404


def test_shadow_report_html_served_when_present(client: TestClient) -> None:
    reports_dir = Path.home() / ".vibe-trading" / "shadow_reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    (reports_dir / "shadow_12345678.html").write_text("<html>ok</html>", encoding="utf-8")

    response = client.get("/shadow-reports/shadow_12345678")
    assert response.status_code == 200
    assert response.text == "<html>ok</html>"
    assert response.headers["content-type"].startswith("text/html")


def test_upload_does_not_block_health_while_disk_write_is_slow(monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression test for [[2026-08-25-uploads-route-blocking-file-write.md]]: the disk write
    in `upload_file` used to run synchronously on the request coroutine, so a slow write would
    have stalled every other request, including /health — the same failure mode as the original
    /correlation bug.

    Uses ``with TestClient(...) as client`` (not a plain, non-context-managed client) so requests
    share one persistent anyio portal/event loop — without ``__enter__``, `TestClient` opens a
    fresh portal per request, so two "concurrent" calls from different threads land on two
    independent event loops and a blocking call in one is never observed stalling the other. See
    [[2026-08-25-blocking-io-regression-tests-not-portal-shared]].
    """
    import threading
    import time

    uploads_dir = Path(tempfile.mkdtemp(prefix="uploads_routes_test_"))
    monkeypatch.setattr(api_server, "UPLOADS_DIR", uploads_dir)
    monkeypatch.setattr(api_server, "_API_KEY", "")

    # Mocks `Path.mkdir`, not `Path.write_bytes` — both the fixed and pre-fix `upload_file`
    # call `uploads_dir.mkdir(...)` with the same signature, so this mock exercises the same
    # code path either way. A `write_bytes`-based mock was tried first and found to be a false
    # positive: the pre-fix code never calls `Path.write_bytes` at all (it uses
    # `dest.open("wb")` + `handle.write(chunk)`), so that mock silently did nothing against the
    # regression this test is supposed to catch — confirmed via revert-and-rerun, see this
    # item's own Attempts log.
    real_mkdir = Path.mkdir

    def _slow_mkdir(self: Path, *a, **kw) -> None:
        time.sleep(1.5)
        return real_mkdir(self, *a, **kw)

    monkeypatch.setattr(Path, "mkdir", _slow_mkdir)

    with TestClient(api_server.app, client=("127.0.0.1", 50000)) as client:
        results: dict[str, object] = {}

        def _call_upload() -> None:
            results["upload"] = client.post(
                "/upload", files={"file": ("note.txt", io.BytesIO(b"hello"), "text/plain")}
            )

        thread = threading.Thread(target=_call_upload)
        thread.start()
        time.sleep(0.3)  # let the slow upload request actually start first

        started = time.monotonic()
        health_resp = client.get("/health")
        health_elapsed = time.monotonic() - started

        thread.join(timeout=5)

    assert health_resp.status_code == 200
    assert health_elapsed < 1.0
    assert results["upload"].status_code == 200
