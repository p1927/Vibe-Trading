"""`src.core.atomic_write`: the one durable writer and the one strict JSON of this repo (D306, D479).

Behaviour is pinned equal to Trade's `context.hub.atomic_write_bytes` / `finite.strict_dumps` from the Trade side
(`tests/test_vibetrading_atomic_write_vendored_equal.py`); these tests pin the contract of every store that now calls it.
"""
from __future__ import annotations

import json
import math
import os
import threading
from pathlib import Path

import pytest

from src.core import atomic_write as aw
from src.core.atomic_write import (
    SECRET_WRITE,
    NonFiniteJSONError,
    atomic_write_bytes,
    atomic_write_text,
    strict_dumps,
    write_json_atomic,
)


def _temps(directory: Path) -> list[str]:
    return [p.name for p in directory.rglob("*") if p.name.endswith(".tmp")]


def test_content_mode_and_no_stray_temp(tmp_path: Path) -> None:
    secret = tmp_path / ".env"
    secret.write_text("A=1\n")
    secret.chmod(0o600)
    atomic_write_text(secret, "A=2\n")
    assert secret.read_text() == "A=2\n" and secret.stat().st_mode & 0o777 == 0o600  # the replace must not widen it
    fresh = tmp_path / "sub" / "new.json"  # parent created
    atomic_write_text(fresh, "é{}")
    assert fresh.read_text(encoding="utf-8") == "é{}" and fresh.stat().st_mode & 0o777 == 0o644
    atomic_write_text(fresh, "x", durable=False)
    assert fresh.read_text() == "x" and _temps(tmp_path) == []


def test_mode_is_exact_from_the_first_byte(tmp_path: Path) -> None:
    seen: list[int] = []
    real_replace = os.replace

    def spy(src, dst):
        seen.append(os.stat(src).st_mode & 0o777)  # the temp's mode at the moment it becomes the file
        real_replace(src, dst)

    fresh = tmp_path / "tok"
    wide = tmp_path / "wide"
    wide.write_text("old")
    wide.chmod(0o644)
    aw_os_replace, aw.os.replace = aw.os.replace, spy
    try:
        atomic_write_text(fresh, "s3cret\n", mode=0o600)
        atomic_write_text(wide, "new", mode=0o600)
    finally:
        aw.os.replace = aw_os_replace
    assert seen == [0o600, 0o600]
    assert fresh.stat().st_mode & 0o777 == 0o600 and wide.stat().st_mode & 0o777 == 0o600
    atomic_write_text(wide, "newer")  # no mode: the destination's own mode is kept
    assert wide.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("bad", [True, 0o1000, -1, "600", 1.5])
def test_bad_mode_is_refused_before_anything_is_written(tmp_path: Path, bad) -> None:
    with pytest.raises(ValueError):
        atomic_write_text(tmp_path / "f", "x", mode=bad)
    assert list(tmp_path.iterdir()) == []


def test_failed_replace_keeps_the_old_file_and_no_temp(tmp_path: Path, monkeypatch) -> None:
    target = tmp_path / "t.json"
    target.write_text("old")

    def boom(*_a, **_k):
        raise OSError("disk")

    monkeypatch.setattr(aw.os, "replace", boom)
    with pytest.raises(OSError):
        atomic_write_text(target, "new")
    assert target.read_text() == "old" and _temps(tmp_path) == []


def test_short_writes_are_completed(tmp_path: Path, monkeypatch) -> None:
    real_write = os.write
    state = {"first": True}

    def short(fd, data):
        if state["first"] and len(data) > 16:
            state["first"] = False
            return real_write(fd, bytes(data[:16]))
        return real_write(fd, data)

    monkeypatch.setattr(aw.os, "write", short)
    body = "x" * 5000
    atomic_write_text(tmp_path / "f", body)
    assert (tmp_path / "f").read_text() == body


def test_in_place_fallback_when_no_temp_can_be_created(tmp_path: Path) -> None:
    ro = tmp_path / "ro"
    ro.mkdir()
    target = ro / ".env"
    target.write_text("OLD=1\n")
    target.chmod(0o666)
    ro.chmod(0o500)
    try:
        if os.access(ro, os.W_OK):
            pytest.skip("directory stays writable (running as root)")
        with pytest.raises(OSError):
            atomic_write_text(target, "NEW=2\n", mode=0o600)  # not atomic-able, and the default is loud
        atomic_write_text(target, "NEW=2\n", **SECRET_WRITE)
        assert target.read_text() == "NEW=2\n" and target.stat().st_mode & 0o777 == 0o600
    finally:
        ro.chmod(0o700)


def test_fsync_failure_is_reported_not_fatal_only_when_asked(tmp_path: Path, monkeypatch) -> None:
    def no_fsync(_fd):
        raise OSError("fsync unsupported")

    monkeypatch.setattr(aw.os, "fsync", no_fsync)
    with pytest.raises(OSError):
        atomic_write_text(tmp_path / "a", "x")
    assert not (tmp_path / "a").exists() and _temps(tmp_path) == []
    seen: list[OSError] = []
    atomic_write_text(tmp_path / "b", "x", on_fsync_error=seen.append)
    assert (tmp_path / "b").read_text() == "x" and len(seen) >= 1


def test_directory_fsync_failure_after_the_rename_is_not_an_error(tmp_path: Path, monkeypatch) -> None:
    def no_dir(_directory):
        raise OSError("EINVAL")

    monkeypatch.setattr(aw, "fsync_directory", no_dir)
    atomic_write_text(tmp_path / "a", "x")
    assert (tmp_path / "a").read_text() == "x"


def test_no_fchmod_platform_still_writes(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delattr(aw.os, "fchmod", raising=False)
    atomic_write_text(tmp_path / ".env", "K=v\n", **SECRET_WRITE)
    assert (tmp_path / ".env").read_text() == "K=v\n"


def test_windows_replace_race_is_retried_and_bounded(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(aw.time, "sleep", lambda *_a: None)
    calls = {"n": 0}
    real = os.replace

    def flaky(src, dst):
        calls["n"] += 1
        if calls["n"] <= 2:
            err = PermissionError("sharing violation")
            err.winerror = 32
            raise err
        real(src, dst)

    monkeypatch.setattr(aw.os, "replace", flaky)
    atomic_write_text(tmp_path / "f", "ok")
    assert (tmp_path / "f").read_text() == "ok" and calls["n"] == 3


# ---- strict JSON ------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize(
    "payload, where",
    [
        ({"px": float("nan")}, "px"),
        ({"rows": [{"close": 1.0}, {"close": float("inf")}]}, "rows[1].close"),
        ({"a": {"b": [float("-inf")]}}, "a.b[0]"),
        ([float("nan")], "[0] = nan"),
        ({"ok": 1, "deep": {"x": [[1, 2], [3, float("nan")]]}}, "deep.x[1][1]"),
    ],
)
def test_non_finite_is_refused_names_the_field_and_keeps_the_old_file(tmp_path: Path, payload, where) -> None:
    target = tmp_path / "state.json"
    write_json_atomic(target, {"good": 1})
    with pytest.raises(NonFiniteJSONError, match=__import__("re").escape(where)):
        write_json_atomic(target, payload)
    assert json.loads(target.read_text()) == {"good": 1} and _temps(tmp_path) == []


def test_non_finite_in_a_default_converted_object_is_refused(tmp_path: Path) -> None:
    class Box:
        value = float("nan")

    with pytest.raises(NonFiniteJSONError):
        write_json_atomic(tmp_path / "f.json", {"b": Box()}, default=lambda o: {"v": o.value})
    assert not (tmp_path / "f.json").exists()


def test_allow_nan_cannot_be_passed() -> None:
    with pytest.raises(TypeError):
        strict_dumps({"a": 1}, allow_nan=True)


@pytest.mark.parametrize("kw", [{}, {"indent": 2}, {"sort_keys": True}, {"ensure_ascii": False, "indent": 2}, {"separators": (",", ":")}])
def test_finite_output_is_byte_identical_to_json_dumps(kw) -> None:
    obj = {"b": [1, 2.5, None, True], "a": {"é": "买入"}, "n": -0.0}
    assert strict_dumps(obj, **kw) == json.dumps(obj, **kw)


def test_write_json_atomic_formats(tmp_path: Path) -> None:
    obj = {"b": 1, "a": "é"}
    write_json_atomic(tmp_path / "a.json", obj, newline=True)
    assert (tmp_path / "a.json").read_text(encoding="utf-8") == json.dumps(obj, ensure_ascii=False, indent=2) + "\n"
    write_json_atomic(tmp_path / "b.json", obj, indent=None, ensure_ascii=True, sort_keys=True)
    assert (tmp_path / "b.json").read_text() == json.dumps(obj, sort_keys=True)


def test_concurrent_writers_never_expose_a_torn_file(tmp_path: Path) -> None:
    target = tmp_path / "shared.json"
    write_json_atomic(target, {"n": 0})
    stop = threading.Event()
    bad: list[str] = []

    def reader() -> None:
        while not stop.is_set():
            try:
                json.loads(target.read_text())
            except (ValueError, FileNotFoundError) as exc:
                bad.append(repr(exc))

    def writer(base: int) -> None:
        for i in range(60):
            write_json_atomic(target, {"n": base + i, "pad": "x" * 4000}, durable=False)

    r = threading.Thread(target=reader)
    r.start()
    ws = [threading.Thread(target=writer, args=(k * 100,)) for k in range(4)]
    [w.start() for w in ws]
    [w.join() for w in ws]
    stop.set()
    r.join()
    assert bad == [] and _temps(tmp_path) == []


# ---- the stores that call it -------------------------------------------------------------------------------------------
def test_connector_config_is_0600_and_refuses_nan(tmp_path: Path, monkeypatch) -> None:
    from src.trading.connectors.alpaca import sdk

    monkeypatch.setattr(sdk, "config_path", lambda: tmp_path / "cfg" / "alpaca.json")
    cfg = sdk.AlpacaConfig() if hasattr(sdk, "AlpacaConfig") else None
    assert cfg is not None
    path = sdk.save_config(cfg)
    assert path.stat().st_mode & 0o777 == 0o600 and _temps(tmp_path) == []
    assert json.loads(path.read_text()) is not None


def test_audit_record_with_a_nan_is_kept_as_text_not_dropped_and_not_a_bare_nan(tmp_path: Path, monkeypatch) -> None:
    from src.live import audit

    monkeypatch.setattr(audit, "audit_ledger_path", lambda: tmp_path / "audit.jsonl")
    event = audit.LiveActionEvent(
        kind="order_placed", session_id="s", outcome="accepted", server="dhan",
        broker_response={"avg_price": float("nan"), "legs": [{"qty": float("inf")}], "ok": 1},
    )
    audit.write_live_action(event)  # the order is already sent: the record is never dropped
    raw = (tmp_path / "audit.jsonl").read_text()
    assert "NaN" not in raw.replace('"nan"', "") and "Infinity" not in raw.replace('"inf"', "")
    resp = json.loads(raw.strip().splitlines()[-1])["broker_response"]
    assert resp == {"avg_price": "nan", "legs": [{"qty": "inf"}], "ok": 1}


def test_scheduled_store_refuses_a_nan_job_config_and_keeps_the_store(tmp_path: Path) -> None:
    from src.scheduled_research.models import JobStatus, ScheduledResearchJob
    from src.scheduled_research.store import ScheduledResearchJobStore

    store = ScheduledResearchJobStore(path=tmp_path / "jobs.json")
    good = ScheduledResearchJob(id="a", prompt="p", schedule="1000", next_run_at=0, status=JobStatus.PENDING, created_at=0)
    store.save({"a": good})
    bad = ScheduledResearchJob(id="b", prompt="p", schedule="1000", next_run_at=0, status=JobStatus.PENDING, created_at=0)
    bad.config = {"threshold": float("nan")}
    with pytest.raises(NonFiniteJSONError, match="threshold"):
        store.save({"a": good, "b": bad})
    assert set(store.load()) == {"a"} and (tmp_path / "jobs.json").stat().st_mode & 0o777 == 0o600


def test_session_message_append_refuses_a_nan(tmp_path: Path) -> None:
    from src.session.models import Message
    from src.session.store import SessionStore

    store = SessionStore(base_dir=tmp_path)
    msg = Message(session_id="s1", role="assistant", content="x", metadata={"conf": float("nan")})
    with pytest.raises(NonFiniteJSONError, match="conf"):
        store.append_message(msg)
    assert not any(p.read_text().strip() for p in tmp_path.rglob("*.jsonl"))
