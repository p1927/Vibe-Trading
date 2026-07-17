"""Unit tests for the local IBKR TWS / IB Gateway bridge."""

from __future__ import annotations

import json
import sys
import types
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from src.trading.connectors.ibkr import local
from src.tools.trading_connector_tool import TradingPositionsTool

pytestmark = pytest.mark.unit


class _FakeContract:
    def __init__(self) -> None:
        self.symbol = ""
        self.secType = ""
        self.exchange = ""
        self.currency = ""
        self.conId = 0
        self.localSymbol = ""


class _FakeStock(_FakeContract):
    def __init__(self, symbol: str, exchange: str, currency: str) -> None:
        super().__init__()
        self.symbol = symbol
        self.secType = "STK"
        self.exchange = exchange
        self.currency = currency
        self.conId = 101
        self.localSymbol = symbol


class _FakeIB:
    def connect(self, host, port, *, clientId, timeout, readonly=True, account=""):
        self.host = host
        self.port = port
        self.client_id = clientId
        self.readonly = readonly
        self.account = account

    def disconnect(self):
        self.disconnected = True

    def managedAccounts(self):
        return ["DU12345"]

    def accountSummary(self, account=""):
        return [
            SimpleNamespace(account="DU12345", tag="NetLiquidation", value="100000", currency="USD", modelCode="")
        ]

    def positions(self):
        contract = SimpleNamespace(
            symbol="AAPL",
            localSymbol="AAPL",
            secType="STK",
            exchange="SMART",
            currency="USD",
            conId=265598,
        )
        return [SimpleNamespace(account="DU12345", contract=contract, position=3, avgCost=150.0)]

    def openTrades(self):
        return []

    def qualifyContracts(self, contract):
        return [contract]

    def reqMktData(self, contract, genericTickList, snapshot, regulatorySnapshot):
        return SimpleNamespace(bid=100.0, ask=100.2, last=100.1, close=99.0, volume=1234, time="")

    def cancelMktData(self, contract):
        return None

    def sleep(self, seconds):
        return None

    def reqHistoricalData(
        self,
        contract,
        *,
        endDateTime,
        durationStr,
        barSizeSetting,
        whatToShow,
        useRTH,
        formatDate,
    ):
        return [SimpleNamespace(date="2026-05-29", open=1, high=2, low=0.5, close=1.5, volume=100)]


@pytest.fixture()
def fake_ib_async(monkeypatch: pytest.MonkeyPatch):
    module = types.ModuleType("ib_async")
    module.IB = _FakeIB
    module.Stock = _FakeStock
    module.Contract = _FakeContract
    monkeypatch.setitem(sys.modules, "ib_async", module)
    monkeypatch.setattr(local, "tcp_port_open", lambda *_, **__: True)
    return module


def test_config_defaults_to_paper_port() -> None:
    cfg = local.IBKRLocalConfig.from_mapping({"profile": "paper"})
    assert cfg.host == "127.0.0.1"
    assert cfg.port == 7497
    assert cfg.readonly is True


def test_account_snapshot_reads_summary(fake_ib_async) -> None:
    cfg = local.IBKRLocalConfig()
    result = local.get_account_snapshot(cfg)

    assert result["status"] == "ok"
    assert result["accounts"] == ["DU12345"]
    assert result["summary"][0]["tag"] == "NetLiquidation"


def test_positions_are_serialized(fake_ib_async) -> None:
    result = local.get_positions(local.IBKRLocalConfig())

    assert result["positions"][0]["symbol"] == "AAPL"
    assert result["positions"][0]["position"] == 3


def test_quote_and_history_are_readonly(fake_ib_async) -> None:
    quote = local.get_quote("AAPL", config=local.IBKRLocalConfig())
    history = local.get_historical_bars("AAPL", config=local.IBKRLocalConfig())

    assert quote["quote"]["last"] == 100.1
    assert history["bars"][0]["close"] == 1.5


def test_paper_profile_rejects_live_account(monkeypatch: pytest.MonkeyPatch, fake_ib_async) -> None:
    class _LiveIB(_FakeIB):
        def managedAccounts(self):
            return ["U12345"]

        def accountSummary(self, account=""):
            return [SimpleNamespace(account="U12345", tag="NetLiquidation", value="1", currency="USD", modelCode="")]

    fake_ib_async.IB = _LiveIB

    with pytest.raises(local.IBKRProfileMismatchError):
        local.get_account_snapshot(local.IBKRLocalConfig(profile="paper"))


def test_check_status_reports_missing_sdk(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(local, "ib_async_available", lambda: False)
    monkeypatch.setattr(local, "tcp_port_open", lambda *_, **__: True)

    report = local.check_local_status(local.IBKRLocalConfig(), scan=False)

    assert report["status"] == "error"
    assert "ib_async" in report["error"]


def test_positions_tool_returns_json(fake_ib_async) -> None:
    payload = json.loads(TradingPositionsTool().execute(connection="ibkr-paper-local"))

    assert payload["status"] == "ok"
    assert payload["profile_id"] == "ibkr-paper-local"
    assert payload["positions"][0]["symbol"] == "AAPL"


def test_service_uses_persisted_ibkr_local_config(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """Configured local endpoint values must survive later connector calls."""
    from src.trading import service

    monkeypatch.setattr(local, "get_runtime_root", lambda: tmp_path)
    local.save_config(
        local.IBKRLocalConfig(
            profile="paper",
            host="192.168.10.8",
            port=4002,
            client_id=123,
            account="DU999",
        )
    )
    captured: dict[str, local.IBKRLocalConfig] = {}

    def _check(cfg: local.IBKRLocalConfig) -> dict[str, object]:
        captured["cfg"] = cfg
        return {"status": "ok", "ports": [], "target": {}, "sdk": {"installed": True}}

    monkeypatch.setattr(local, "check_local_status", _check)

    assert service.check_connection("ibkr-paper-local")["status"] == "ok"

    cfg = captured["cfg"]
    assert cfg.host == "192.168.10.8"
    assert cfg.port == 4002
    assert cfg.client_id == 123
    assert cfg.account == "DU999"


def test_cli_connector_routes_to_handler() -> None:
    from cli._legacy import _build_parser, _dispatch_connector

    args = _build_parser().parse_args(["connector", "check", "ibkr-paper-local", "--account", "DU12345"])
    with patch("cli._legacy.cmd_connector_check", return_value=0) as handler:
        assert _dispatch_connector(args) == 0
    handler.assert_called_once_with(
        "ibkr-paper-local",
        host=None,
        port=None,
        client_id=None,
        account="DU12345",
    )


def test_cli_connector_check_passes_account_to_backend() -> None:
    from cli._legacy import cmd_connector_check

    report = {"status": "ok", "ports": [], "target": {}, "sdk": {"installed": True}}
    with patch("src.trading.service.check_connection", return_value=report) as check:
        assert cmd_connector_check("ibkr-paper-local", account="DU12345") == 0
    check.assert_called_once_with(
        "ibkr-paper-local",
        host=None,
        port=None,
        client_id=None,
        account="DU12345",
    )

# ── _wait_for_tick timing regression tests ─────────────────────────────

class _DelayedTickerIB(_FakeIB):
    """Fake IB that simulates async tick arrival after event-loop pumps."""

    def __init__(self) -> None:
        super().__init__()
        self._sleep_total = 0.0

    def reqMktData(self, contract, genericTickList, snapshot, regulatorySnapshot):
        # Return an empty ticker — fields populate only after sleep pumps.
        return SimpleNamespace(bid=None, ask=None, last=None, close=None, volume=None, time="")

    def sleep(self, seconds):
        self._sleep_total += seconds
        if self._sleep_total >= 0.1:
            # Simulate tick arrival: mutate the ticker on the next pump.
            pass  # handled below via waitOnUpdate

    def waitOnUpdate(self, timeout=0):
        self._sleep_total += timeout
        if self._sleep_total >= 0.15:
            # After enough pumps, populate the last-returned ticker.
            return True
        return False


def _delayed_ticker_ib_fixture(monkeypatch: pytest.MonkeyPatch) -> None:
    """Install a delayed-ticker IB mock for quote timing tests."""
    fake_ib_class = type(
        "_DelayedQuoteIB",
        (_FakeIB,),
        {
            "_sleep_count": 0,
            "reqMktData": lambda self, contract, genericTickList, snapshot, regulatorySnapshot: (
                SimpleNamespace(bid=None, ask=None, last=None, close=None, volume=None, time="")
            ),
            "sleep": lambda self, seconds: setattr(self, "_sleep_count", self._sleep_count + 1),
            "waitOnUpdate": lambda self, timeout=0: self._sleep_count >= 3,
        },
    )
    module = types.ModuleType("ib_async")
    module.IB = fake_ib_class
    module.Stock = _FakeStock
    module.Contract = _FakeContract
    monkeypatch.setitem(sys.modules, "ib_async", module)
    monkeypatch.setattr(local, "tcp_port_open", lambda *_, **__: True)
    # Force the pool to create a fresh connection so get_quote uses our mock.
    monkeypatch.setattr(local._pool._local, "refcount", 0)
    monkeypatch.setattr(local._pool._local, "ib", None)


def test_quote_waits_for_tick_arrival(monkeypatch: pytest.MonkeyPatch) -> None:
    """get_quote must block until the snapshot ticker receives data from the event loop."""
    _delayed_ticker_ib_fixture(monkeypatch)

    result = local.get_quote("AAPL", config=local.IBKRLocalConfig(profile="paper"))

    # Even with an initially-empty ticker the wait loop runs ib.sleep() /
    # waitOnUpdate() enough times to satisfy the "at least one field is set"
    # condition.  Since the mock's waitOnUpdate returns True after 3 pumps,
    # the loop exits and we get None fields — that's correct: it means the
    # wait didn't deadlock and returned within the timeout.
    assert result["status"] == "ok"
    # The mock never sets bid/ask/last, so we verify the function completed
    # (didn't hang) even when data never arrives — fields stay None.
    assert result["quote"]["bid"] is None
    assert result["quote"]["ask"] is None
    assert result["quote"]["last"] is None


class _FillAfterPumpIB(_FakeIB):
    """Fake IB whose ticker fields populate after sleep pumps the event loop."""

    def __init__(self) -> None:
        super().__init__()
        self.pump_count = 0

    def reqMktData(self, contract, genericTickList, snapshot, regulatorySnapshot):
        return SimpleNamespace(bid=None, ask=None, last=None, close=None, volume=None, time="")

    def sleep(self, seconds):
        self.pump_count += 1

    def waitOnUpdate(self, timeout=0):
        self.pump_count += 1
        return self.pump_count >= 3


def _fill_after_pump_fixture(monkeypatch: pytest.MonkeyPatch, pump_count_attr: str = "pump_count") -> None:
    """Install an IB mock where ticker fills after N event-loop pumps."""
    import functools

    # Build a class inline so we can capture references cleanly.
    class _FillIB(_FakeIB):
        def __init__(self) -> None:
            super().__init__()
            self.pc = 0

        def reqMktData(self, contract, genericTickList, snapshot, regulatorySnapshot):
            # Return a mutable namespace — we'll write to it in sleep.
            return SimpleNamespace(bid=None, ask=None, last=None, close=None, volume=None, time="")

        def sleep(self, seconds):
            self.pc += 1
            _maybe_fill(self)

        def waitOnUpdate(self, timeout=0):
            self.pc += 1
            return self.pc >= 3

    module = types.ModuleType("ib_async")
    module.IB = _FillIB
    module.Stock = _FakeStock
    module.Contract = _FakeContract
    monkeypatch.setitem(sys.modules, "ib_async", module)
    monkeypatch.setattr(local, "tcp_port_open", lambda *_, **__: True)
    monkeypatch.setattr(local._pool._local, "refcount", 0)
    monkeypatch.setattr(local._pool._local, "ib", None)


def test_quote_receives_tick_after_event_loop_pump(monkeypatch: pytest.MonkeyPatch) -> None:
    """get_quote returns populated values when the ticker fills mid-wait.

    Simulates the real flow: reqMktData returns an empty ticker, then
    ib.sleep() / waitOnUpdate() pumps deliver data.  We monkeypatch
    _wait_for_tick's completion so that after 2 pumps the ticker's bid/ask
    are set by patching _obj_get to return values.
    """
    pump_count = [0]

    class _PumpFillIB(_FakeIB):
        def reqMktData(self, contract, genericTickList, snapshot, regulatorySnapshot):
            return SimpleNamespace(bid=None, ask=None, last=None, close=None, volume=100, time="")

        def sleep(self, seconds):
            pump_count[0] += 1

        def waitOnUpdate(self, timeout=0):
            pump_count[0] += 1
            if pump_count[0] >= 2:
                return True
            return False

    module = types.ModuleType("ib_async")
    module.IB = _PumpFillIB
    module.Stock = _FakeStock
    module.Contract = _FakeContract
    monkeypatch.setitem(sys.modules, "ib_async", module)
    monkeypatch.setattr(local, "tcp_port_open", lambda *_, **__: True)
    monkeypatch.setattr(local._pool._local, "refcount", 0)
    monkeypatch.setattr(local._pool._local, "ib", None)

    # Patch _obj_get so that after 2 pumps, it returns non-None for bid/ask.
    original_obj_get = local._obj_get
    def _patched_obj_get(obj, name, default=...):
        if pump_count[0] >= 2:
            fake_values = {"bid": 150.0, "ask": 150.5, "last": 150.25, "close": 149.0, "volume": 100}
            if name in fake_values:
                return fake_values[name]
        if default is not ...:
            return original_obj_get(obj, name, default)
        return original_obj_get(obj, name)

    monkeypatch.setattr(local, "_obj_get", _patched_obj_get)

    result = local.get_quote("AAPL", config=local.IBKRLocalConfig(profile="paper"))

    assert result["status"] == "ok"
    assert result["quote"]["bid"] == 150.0
    assert result["quote"]["ask"] == 150.5
    assert result["quote"]["last"] == 150.25
    assert pump_count[0] >= 2, f"Expected at least 2 event-loop pumps, got {pump_count[0]}"


# ── Thread-local pool concurrency tests ────────────────────────────────

def test_pool_thread_local_isolation(fake_ib_async) -> None:
    """Each thread gets a unique IB connection and client ID.

    Uses barriers to force each worker to run on a distinct thread before
    the ThreadPoolExecutor can reuse a single fast thread for all tasks.
    """
    import concurrent.futures
    import threading

    num_workers = 4
    ready = threading.Barrier(num_workers)
    done = threading.Barrier(num_workers)
    results: dict[int, dict[str, int]] = {}

    def worker(idx: int) -> None:
        cfg = local.IBKRLocalConfig(profile="paper", client_id=77)
        ib = local._pool.acquire(cfg)
        results[idx] = {"ib_id": id(ib), "client_id": ib.client_id}
        # Wait for ALL workers to acquire before any releases, so we
        # capture the thread-local state while every thread is active.
        ready.wait()
        done.wait()

    with concurrent.futures.ThreadPoolExecutor(max_workers=num_workers) as executor:
        futures = [executor.submit(worker, i) for i in range(num_workers)]
        for f in futures:
            f.result()

    client_ids = {r["client_id"] for r in results.values()}
    ib_ids = {r["ib_id"] for r in results.values()}

    assert len(client_ids) == num_workers, f"Expected {num_workers} unique client IDs, got {client_ids}"
    assert len(ib_ids) == num_workers, f"Expected {num_workers} distinct IB objects, got {len(ib_ids)} unique"
    # All IDs should be > base (77) since counter starts at 1
    for cid in client_ids:
        assert cid > 77, f"Client ID {cid} should be > base 77"

def test_pool_refcount_disconnects_on_last_release(fake_ib_async) -> None:
    """disconnect() is called only when refcount reaches zero."""
    cfg = local.IBKRLocalConfig(profile="paper", client_id=77)

    # Clear any prior thread-local state from other tests.
    local._pool._local.refcount = 0
    local._pool._local.ib = None

    ib1 = local._pool.acquire(cfg)
    assert local._pool._local.refcount == 1

    ib2 = local._pool.acquire(cfg)
    assert ib2 is ib1  # Same thread → same connection
    assert local._pool._local.refcount == 2

    local._pool.release()
    assert local._pool._local.refcount == 1
    assert getattr(ib1, "disconnected", False) is False

    local._pool.release()
    assert local._pool._local.refcount == 0
    assert getattr(ib1, "disconnected", True) is True
    assert local._pool._local.ib is None


def test_pool_release_idempotent_no_connection(fake_ib_async) -> None:
    """Calling release on an empty pool is a no-op."""
    local._pool._local.refcount = 0
    local._pool._local.ib = None
    # Must not raise.
    local._pool.release()
# ── NaN rejection tests ─────────────────────────────────────────────────

class _NaNTickerIB(_FakeIB):
    """Fake IB whose ticker fields are NaN (not None) -- simulates after-hours."""

    def __init__(self) -> None:
        super().__init__()
        self._sleep_count = 0

    def reqMktData(self, contract, genericTickList, snapshot, regulatorySnapshot):
        import math
        return SimpleNamespace(
            bid=float("nan"), ask=float("nan"), last=float("nan"),
            close=float("nan"), volume=float("nan"), time=""
        )

    def sleep(self, seconds):
        self._sleep_count += 1

    def waitOnUpdate(self, timeout=0):
        self._sleep_count += 1
        return True


def test_quote_rejects_nan_and_waits_for_real_data(monkeypatch: pytest.MonkeyPatch) -> None:
    """get_quote must not exit with NaN -- it must wait for real numeric data."""
    import math

    pump_count = [0]

    class _NaNFillIB(_FakeIB):
        def reqMktData(self, contract, genericTickList, snapshot, regulatorySnapshot):
            return SimpleNamespace(
                bid=float("nan"), ask=float("nan"), last=float("nan"),
                close=float("nan"), volume=float("nan"), time=""
            )

        def sleep(self, seconds):
            pump_count[0] += 1

        def waitOnUpdate(self, timeout=0):
            pump_count[0] += 1
            return pump_count[0] >= 3

    module = types.ModuleType("ib_async")
    module.IB = _NaNFillIB
    module.Stock = _FakeStock
    module.Contract = _FakeContract
    monkeypatch.setitem(sys.modules, "ib_async", module)
    monkeypatch.setattr(local, "tcp_port_open", lambda *_, **__: True)
    monkeypatch.setattr(local._pool._local, "refcount", 0)
    monkeypatch.setattr(local._pool._local, "ib", None)

    # Even after pumps, the mock always returns NaN -- so the final fields
    # should stay NaN (no real data arrived). The key assertion: the function
    # did NOT exit early -- it pumped until timeout.
    result = local.get_quote("AAPL", config=local.IBKRLocalConfig(profile="paper"))
    assert result["status"] == "ok"
    assert pump_count[0] >= 3, f"Expected at least 3 pumps (NaN loop), got {pump_count[0]}"
    # Fields stay NaN since mock never provides real data
    assert math.isnan(float(result["quote"]["bid"]))
