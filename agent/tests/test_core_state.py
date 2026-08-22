"""Tests for src.core.state.RunStateStore."""

from __future__ import annotations

import json

from src.core.state import RunStateStore


def test_create_run_dir_makes_expected_subdirs(tmp_path):
    store = RunStateStore()
    run_dir = store.create_run_dir(tmp_path)
    assert run_dir.parent == tmp_path
    assert (run_dir / "code").is_dir()
    assert (run_dir / "logs").is_dir()
    assert (run_dir / "artifacts").is_dir()


def test_create_run_dir_is_unique_across_calls(tmp_path):
    store = RunStateStore()
    first = store.create_run_dir(tmp_path)
    second = store.create_run_dir(tmp_path)
    assert first != second


def test_save_request_writes_prompt_and_context(tmp_path):
    store = RunStateStore()
    run_dir = store.create_run_dir(tmp_path)
    payload = store.save_request(run_dir, "do the thing", {"ticker": "NIFTY"})
    assert payload == {"prompt": "do the thing", "context": {"ticker": "NIFTY"}}
    on_disk = json.loads((run_dir / "req.json").read_text())
    assert on_disk == payload


def test_mark_success_writes_status(tmp_path):
    store = RunStateStore()
    run_dir = store.create_run_dir(tmp_path)
    store.mark_success(run_dir)
    state = json.loads((run_dir / "state.json").read_text())
    assert state == {"status": "success"}


def test_mark_failure_writes_status_and_reason(tmp_path):
    store = RunStateStore()
    run_dir = store.create_run_dir(tmp_path)
    store.mark_failure(run_dir, "boom")
    state = json.loads((run_dir / "state.json").read_text())
    assert state == {"status": "failed", "reason": "boom"}


def test_mark_cancelled_defaults_to_user_reason(tmp_path):
    store = RunStateStore()
    run_dir = store.create_run_dir(tmp_path)
    store.mark_cancelled(run_dir)
    state = json.loads((run_dir / "state.json").read_text())
    assert state == {"status": "cancelled", "reason": "cancelled by user"}


def test_mark_cancelled_accepts_custom_reason(tmp_path):
    store = RunStateStore()
    run_dir = store.create_run_dir(tmp_path)
    store.mark_cancelled(run_dir, reason="user hit stop")
    state = json.loads((run_dir / "state.json").read_text())
    assert state == {"status": "cancelled", "reason": "user hit stop"}


def test_later_state_write_overwrites_earlier_one(tmp_path):
    store = RunStateStore()
    run_dir = store.create_run_dir(tmp_path)
    store.mark_success(run_dir)
    store.mark_failure(run_dir, "actually it failed")
    state = json.loads((run_dir / "state.json").read_text())
    assert state == {"status": "failed", "reason": "actually it failed"}
