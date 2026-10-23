"""A load, mutate, save on one stored file holds the store's lock (`src.core.file_lock`), so concurrent callers do not erase
each other's change (Trade backlog 2026-10-23-vibetrading-fork-atomic-write-copies, finding 4)."""
from __future__ import annotations

import threading
from pathlib import Path

from src.hypotheses.registry import HypothesisRegistry
from src.trading.connections import ConnectionStore


def _run(n: int, fn) -> list[BaseException]:
    errors: list[BaseException] = []
    gate = threading.Barrier(n)

    def target(i: int) -> None:
        try:
            gate.wait()
            fn(i)
        except BaseException as exc:  # noqa: BLE001 - the test reports any failure
            errors.append(exc)

    threads = [threading.Thread(target=target, args=(i,)) for i in range(n)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    return errors


def test_concurrent_hypothesis_creates_all_land(tmp_path: Path) -> None:
    path = tmp_path / "hyp.json"
    errors = _run(12, lambda i: HypothesisRegistry(path=path).create(title=f"h{i}", thesis="t"))
    assert errors == []
    assert len(HypothesisRegistry(path=path).list()) == 12


def test_concurrent_hypothesis_link_backtest_and_update_keep_both(tmp_path: Path) -> None:
    path = tmp_path / "hyp.json"
    hid = HypothesisRegistry(path=path).create(title="h", thesis="t").hypothesis_id

    def work(i: int) -> None:
        reg = HypothesisRegistry(path=path)
        if i % 2:
            reg.link_backtest(hid, run_card_path=f"/r/{i}.json")
        else:
            reg.update(hid, invalidation_notes=f"n{i}")

    assert _run(10, work) == []
    got = HypothesisRegistry(path=path).list()[0]
    assert len(got.run_cards) == 5


def test_concurrent_connection_creates_all_land(tmp_path: Path) -> None:
    class Vault:
        def delete(self, *_a):
            return None

    from src.trading.connections import is_portfolio_connection_profile
    from src.trading.profiles import list_profiles

    profile = next(p.id for p in list_profiles() if is_portfolio_connection_profile(p))
    path = tmp_path / "connections.json"

    def work(i: int) -> None:
        ConnectionStore(path=path, credential_store=Vault()).create(f"c{i}", profile, f"c{i}")

    assert _run(10, work) == []
    assert len(ConnectionStore(path=path, credential_store=Vault()).list()) == 10


def test_concurrent_task_completions_unblock_every_dependent(tmp_path: Path) -> None:
    from src.swarm.models import SwarmTask, TaskStatus
    from src.swarm.task_store import TaskStore, resolve_dependencies

    store = TaskStore(tmp_path)
    ids = [f"d{i}" for i in range(6)]
    for i in ids:
        store.save_task(SwarmTask(id=i, agent_id="a", prompt_template="p", status=TaskStatus.blocked, blocked_by=["x", "y"]))
    errors = _run(2, lambda i: resolve_dependencies(store._tasks_dir, ["x", "y"][i]))
    assert errors == []
    for i in ids:
        got = store.load_task(i)
        assert got.blocked_by == [] and got.status == TaskStatus.pending, (i, got.blocked_by)
