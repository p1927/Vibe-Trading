from __future__ import annotations

import json
import stat
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from src.tools import qveris_tool as qt


@pytest.fixture(autouse=True)
def qveris_config_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(qt, "QVERIS_CONFIG_PATH", tmp_path / "qveris.json")
    monkeypatch.delenv("QVERIS_API_KEY", raising=False)
    monkeypatch.delenv("QVERIS_BASE_URL", raising=False)
    return tmp_path / "qveris.json"


class FakeResponse:
    def __init__(self, status_code: int, payload: dict, headers: dict | None = None):
        self.status_code = status_code
        self._payload = payload
        self.headers = headers or {}
        self.content = json.dumps(payload).encode("utf-8")

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self) -> dict:
        return self._payload


class FakeHttpClient:
    def __init__(self, responses: list[FakeResponse]):
        self.responses = responses
        self.calls: list[dict] = []

    def request(self, method, url, **kwargs):
        self.calls.append({"method": method, "url": url, **kwargs})
        return self.responses.pop(0)


def test_config_read_write_masks_key_and_uses_0600(qveris_config_path: Path):
    saved = qt.save_qveris_config(
        qt.QVerisConfig(
            enabled=True,
            base_url="https://example.test/api",
            api_key="sk-test-8TI",
            mode="paid",
            budget_credits_per_session=12.5,
        )
    )

    assert saved.enabled is True
    assert qt.load_qveris_config().api_key == "sk-test-8TI"
    assert qt.mask_api_key("sk-test-8TI") == "sk-t…8TI"
    assert stat.S_IMODE(qveris_config_path.stat().st_mode) == 0o600


def test_env_overrides_file_config(monkeypatch: pytest.MonkeyPatch):
    qt.save_qveris_config(
        qt.QVerisConfig(enabled=True, base_url="https://file.test", api_key="file-key")
    )
    monkeypatch.setenv("QVERIS_API_KEY", "env-key")
    monkeypatch.setenv("QVERIS_BASE_URL", "https://env.test/api")

    cfg = qt.load_qveris_config()

    assert cfg.api_key == "env-key"
    assert cfg.base_url == "https://env.test/api"


def test_free_mode_tools_are_hidden():
    assert qt.QVerisSearchTool.check_available() is False

    qt.save_qveris_config(qt.QVerisConfig(enabled=True, api_key="sk-live", mode="free"))

    assert qt.QVerisSearchTool.check_available() is False

    qt.save_qveris_config(qt.QVerisConfig(enabled=True, api_key="sk-live", mode="paid"))

    assert qt.QVerisSearchTool.check_available() is True


def test_free_mode_execute_is_unavailable(monkeypatch: pytest.MonkeyPatch):
    qt.save_qveris_config(
        qt.QVerisConfig(enabled=True, api_key="sk-live", mode="free")
    )

    class FakeClient:
        def inspect(self, tool_ids, **kwargs):
            return {
                "results": [
                    {
                        "tool_id": tool_ids[0],
                        "expected_cost": "2 credits",
                        "billing_rule": {"kind": "call"},
                    }
                ]
            }

    monkeypatch.setattr(qt.QVerisExecuteTool, "_client", lambda self: FakeClient())

    payload = json.loads(
        qt.QVerisExecuteTool().execute(tool_id="tool_1", parameters={"x": 1})
    )

    assert payload == {
        "ok": False,
        "error": (
            "QVeris paid mode is off; enable it with `vibe-trading data mode paid` "
            "or Settings -> QVeris to use QVeris tools"
        ),
    }


def test_unconfigured_execute_names_missing_api_key():
    payload = json.loads(qt.QVerisSearchTool().execute(query="options iv"))

    assert payload == {
        "ok": False,
        "error": (
            "QVeris is not configured; set QVERIS_API_KEY and enable paid mode "
            "(`vibe-trading data mode paid` or Settings -> QVeris) to use QVeris tools"
        ),
    }


def test_paid_mode_rejects_when_expected_cost_exceeds_budget(monkeypatch):
    qt.save_qveris_config(
        qt.QVerisConfig(
            enabled=True,
            api_key="sk-live",
            mode="paid",
            budget_credits_per_session=1.0,
        )
    )

    class FakeClient:
        def execute(self, *args, **kwargs):  # pragma: no cover - must not call
            raise AssertionError("execute should be blocked by budget")

    monkeypatch.setattr(qt.QVerisExecuteTool, "_client", lambda self: FakeClient())

    payload = json.loads(
        qt.QVerisExecuteTool(default_session_id="s1").execute(
            tool_id="tool_1",
            parameters={},
            expected_cost="2 credits",
        )
    )

    assert payload["status"] == "budget_exceeded"
    assert payload["budget_credits_per_session"] == 1.0


def test_parallel_execute_reserves_shared_session_budget(monkeypatch):
    qt.save_qveris_config(
        qt.QVerisConfig(
            enabled=True,
            api_key="sk-live",
            mode="paid",
            budget_credits_per_session=1.0,
        )
    )
    entered = threading.Event()
    release = threading.Event()

    class FakeClient:
        calls = 0
        lock = threading.Lock()

        def execute(self, *args, **kwargs):
            del args, kwargs
            with self.lock:
                type(self).calls += 1
                call_number = type(self).calls
            if call_number == 1:
                entered.set()
                assert release.wait(timeout=2)
            return {"success": True, "cost": 0.75, "result": {}}

    client = FakeClient()
    monkeypatch.setattr(qt.QVerisExecuteTool, "_client", lambda self: client)
    tool = qt.QVerisExecuteTool(default_session_id="shared")

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(
            tool.execute,
            tool_id="tool_1",
            parameters={},
            expected_cost="0.75 credits",
        )
        assert entered.wait(timeout=2)
        second = pool.submit(
            tool.execute,
            tool_id="tool_2",
            parameters={},
            expected_cost="0.75 credits",
        )
        second_payload = json.loads(second.result(timeout=2))
        release.set()
        first_payload = json.loads(first.result(timeout=2))

    assert first_payload["ok"] is True
    assert second_payload["status"] == "budget_exceeded"
    assert FakeClient.calls == 1
    assert qt._credit_ledger().spent("shared") == pytest.approx(0.75)
    assert qt._credit_ledger().pending("shared") == 0.0


def test_definite_presend_failure_releases_credit_reservation(monkeypatch):
    qt.save_qveris_config(
        qt.QVerisConfig(
            enabled=True,
            api_key="sk-live",
            mode="paid",
            budget_credits_per_session=1.0,
        )
    )

    class FailOnceClient:
        calls = 0

        def execute(self, *args, **kwargs):
            del args, kwargs
            type(self).calls += 1
            if type(self).calls == 1:
                raise qt.httpx.ConnectError("provider failed")
            return {"success": True, "cost": 1.0, "result": {}}

    client = FailOnceClient()
    monkeypatch.setattr(qt.QVerisExecuteTool, "_client", lambda self: client)
    tool = qt.QVerisExecuteTool(default_session_id="retry")
    call = {
        "tool_id": "tool_1",
        "parameters": {},
        "expected_cost": "1 credit",
    }

    with pytest.raises(qt.httpx.ConnectError, match="provider failed"):
        tool.execute(**call)

    assert qt._credit_ledger().pending("retry") == 0.0
    payload = json.loads(tool.execute(**call))
    assert payload["ok"] is True
    assert payload["session_spent_credits"] == pytest.approx(1.0)


def test_429_retries_after_retry_after(monkeypatch: pytest.MonkeyPatch):
    sleeps: list[float] = []
    monkeypatch.setattr(qt.time, "sleep", lambda value: sleeps.append(value))
    fake = FakeHttpClient(
        [
            FakeResponse(429, {"error": "rate"}, headers={"Retry-After": "0"}),
            FakeResponse(200, {"results": [], "remaining_credits": 9}),
        ]
    )
    client = qt.QVerisClient(
        qt.QVerisConfig(enabled=True, api_key="sk-live"),
        client=fake,
        min_interval_seconds=0,
    )

    payload = client.search("aapl", limit=1)

    assert payload["remaining_credits"] == 9
    assert len(fake.calls) == 2
    assert sleeps == [0.0]


def test_execute_hydrates_truncated_full_content():
    fake = FakeHttpClient(
        [
            FakeResponse(
                200,
                {
                    "success": True,
                    "cost": 1.0,
                    "result": {
                        "message": "too long",
                        "full_content_file_url": "https://oss.qveris.cn/file.json",
                    },
                },
            ),
            FakeResponse(200, {"rows": [{"close": 1.23}]}),
        ]
    )
    client = qt.QVerisClient(
        qt.QVerisConfig(enabled=True, api_key="sk-live"),
        client=fake,
        min_interval_seconds=0,
    )

    payload = client.execute("tool_1", parameters={}, max_response_size=10)

    assert payload["result"]["full_content"] == {"rows": [{"close": 1.23}]}
    assert payload["result"]["full_content_downloaded"] is True
    assert fake.calls[1]["headers"] is None


def test_zero_expected_cost_from_the_caller_cannot_clear_the_budget_gate(monkeypatch):
    """A caller-written quote of 0 must not reserve nothing and sail through.

    ``expected_cost`` is an ordinary tool argument, so it is written by an LLM
    on the agent path and by an arbitrary client over MCP. It used to
    short-circuit the marketplace lookup and be adopted verbatim, so 0 cleared
    the pre-check with a zero reservation whatever the real price was.
    """
    qt.save_qveris_config(
        qt.QVerisConfig(
            enabled=True,
            api_key="sk-live",
            mode="paid",
            budget_credits_per_session=1.0,
        )
    )

    class FakeClient:
        def inspect(self, *args, **kwargs):
            del args, kwargs
            return {"results": [{"tool_id": "tool_1", "expected_cost": "5 credits"}]}

        def execute(self, *args, **kwargs):  # pragma: no cover - must not run
            raise AssertionError("execute must be blocked by the budget gate")

    monkeypatch.setattr(qt.QVerisExecuteTool, "_client", lambda self: FakeClient())

    payload = json.loads(
        qt.QVerisExecuteTool(default_session_id="attack").execute(
            tool_id="tool_1",
            parameters={},
            expected_cost=0.0,
        )
    )

    assert payload["status"] == "budget_exceeded"
    assert payload["quote"]["expected_cost"] == 5.0
    assert payload["quote"]["quote_source"] == "server_overrode_lower_caller_quote"
    assert qt._credit_ledger().spent("attack") == 0.0


def test_an_unverifiable_zero_quote_fails_closed(monkeypatch):
    """With no server quote to corroborate it, 0 means unknown, not free."""
    qt.save_qveris_config(
        qt.QVerisConfig(
            enabled=True,
            api_key="sk-live",
            mode="paid",
            budget_credits_per_session=1.0,
        )
    )

    class FakeClient:
        def inspect(self, *args, **kwargs):
            del args, kwargs
            raise RuntimeError("marketplace unreachable")

        def execute(self, *args, **kwargs):  # pragma: no cover - must not run
            raise AssertionError("execute must be blocked by the budget gate")

    monkeypatch.setattr(qt.QVerisExecuteTool, "_client", lambda self: FakeClient())

    payload = json.loads(
        qt.QVerisExecuteTool(default_session_id="offline").execute(
            tool_id="tool_1", parameters={}, expected_cost=0
        )
    )

    assert payload["status"] == "budget_exceeded"


def test_an_honest_caller_quote_is_still_honoured(monkeypatch):
    """The caller hint keeps working when it is not understating the price."""
    qt.save_qveris_config(
        qt.QVerisConfig(
            enabled=True,
            api_key="sk-live",
            mode="paid",
            budget_credits_per_session=10.0,
        )
    )

    class FakeClient:
        def inspect(self, *args, **kwargs):
            del args, kwargs
            return {"results": [{"tool_id": "tool_1", "expected_cost": "1 credit"}]}

        def execute(self, *args, **kwargs):
            del args, kwargs
            return {"success": True, "cost": 1.0, "result": {}}

    monkeypatch.setattr(qt.QVerisExecuteTool, "_client", lambda self: FakeClient())

    payload = json.loads(
        qt.QVerisExecuteTool(default_session_id="honest").execute(
            tool_id="tool_1", parameters={}, expected_cost="2 credits"
        )
    )

    assert payload["ok"] is True
    assert qt._credit_ledger().spent("honest") == pytest.approx(1.0)


def test_model_supplied_session_id_cannot_reset_the_budget(monkeypatch):
    """The cap is keyed on the host-assigned session; rotating a model argument changes nothing."""
    qt.save_qveris_config(
        qt.QVerisConfig(enabled=True, api_key="sk-live", mode="paid", budget_credits_per_session=1.0)
    )

    class FakeClient:
        def execute(self, *args, **kwargs):
            return {"success": True, "cost": 1.0, "result": {}}

    monkeypatch.setattr(qt.QVerisExecuteTool, "_client", lambda self: FakeClient())
    tool = qt.QVerisExecuteTool(default_session_id="host-1")
    first = json.loads(tool.execute(tool_id="t", parameters={}, expected_cost="1 credit"))
    assert first["ok"] is True
    for rotated in ("fresh-1", "fresh-2", ""):
        out = json.loads(
            tool.execute(tool_id="t", parameters={}, expected_cost="1 credit", session_id=rotated)
        )
        assert out["status"] == "budget_exceeded"
    assert qt._credit_ledger().spent("host-1") == pytest.approx(1.0)
    # a host without sessions (MCP) shares ONE default key, not a model-chosen one
    other = qt.QVerisExecuteTool()
    ok = json.loads(other.execute(tool_id="t", parameters={}, expected_cost="1 credit", session_id="x"))
    assert ok["ok"] is True
    again = json.loads(other.execute(tool_id="t", parameters={}, expected_cost="1 credit", session_id="y"))
    assert again["status"] == "budget_exceeded"
    assert qt._credit_ledger().spent("default") == pytest.approx(1.0)


def test_qveris_tools_get_the_host_session_id():
    from src.tools import session_injected_classes

    assert {qt.QVerisSearchTool, qt.QVerisInspectTool, qt.QVerisExecuteTool} <= session_injected_classes()


def test_ambiguous_execute_failure_remains_charged_after_new_ledger(monkeypatch):
    qt.save_qveris_config(qt.QVerisConfig(api_key="fixture", mode="paid", budget_credits_per_session=1))
    class Client:
        def execute(self, *args, **kwargs):
            raise qt.httpx.ReadTimeout("response lost after send")
    monkeypatch.setattr(qt.QVerisExecuteTool, "_client", lambda self: Client())
    tool = qt.QVerisExecuteTool(default_session_id="ambiguous")
    call = {"tool_id": "t", "parameters": {}, "expected_cost": 1}
    with pytest.raises(RuntimeError, match="remain debited") as error:
        tool.execute(**call)
    assert isinstance(error.value.__cause__, qt.httpx.ReadTimeout)
    assert qt._credit_ledger().spent("ambiguous") == 1
    assert qt._credit_ledger().pending("ambiguous") == 0
    assert json.loads(qt.QVerisExecuteTool(default_session_id="ambiguous").execute(**call))["status"] == "budget_exceeded"


def test_pending_reservation_survives_process_death_and_contends_across_processes(tmp_path):
    import os
    import subprocess
    import sys
    from src.tools.qveris_credits import CreditLedger

    path = tmp_path / "credits.sqlite3"
    code = "from pathlib import Path; import os; from src.tools.qveris_credits import CreditLedger; ledger=CreditLedger(Path(os.environ['CREDIT_PATH'])); token,_=ledger.reserve('shared',0.75,1); print(bool(token), flush=True); os._exit(0)"
    env = {**os.environ, "CREDIT_PATH": str(path)}
    processes = [subprocess.Popen([sys.executable, "-c", code], env=env, stdout=subprocess.PIPE, text=True) for _ in range(2)]
    outputs = [p.communicate(timeout=10)[0].strip() for p in processes]
    assert sorted(outputs) == ["False", "True"]
    assert all(p.returncode == 0 for p in processes)
    ledger = CreditLedger(path)
    assert ledger.spent("shared") == ledger.pending("shared") == 0.75
    assert ledger.reserve("shared", 0.75, 1)[0] is None


def test_ledger_write_failure_prevents_paid_call(monkeypatch):
    qt.save_qveris_config(qt.QVerisConfig(api_key="fixture", mode="paid"))
    class Client:
        def execute(self, *a, **k):
            raise AssertionError("must not call provider")
    monkeypatch.setattr(qt.QVerisExecuteTool, "_client", lambda self: Client())
    def fail(*args, **kwargs):
        raise OSError("ledger unavailable")
    monkeypatch.setattr(qt.CreditLedger, "reserve", fail)
    with pytest.raises(OSError, match="ledger unavailable"):
        qt.QVerisExecuteTool().execute(tool_id="t", parameters={}, expected_cost=1)


def test_real_http_lost_paid_response_retains_debit(monkeypatch):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    requests = []
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            requests.append(self.path)
            if self.path.startswith("/tools/execute"):
                self.close_connection = True  # server accepted submission, response was lost
                return
            body = json.dumps({"results": [{"expected_cost": 1}]}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    cfg = qt.QVerisConfig(api_key="owned-fixture", mode="paid", budget_credits_per_session=1,
                         base_url=f"http://127.0.0.1:{server.server_port}")
    qt.save_qveris_config(cfg)
    client = qt.QVerisClient(cfg, min_interval_seconds=0, max_429_retries=0)
    monkeypatch.setattr(qt.QVerisExecuteTool, "_client", lambda self: client)
    try:
        tool = qt.QVerisExecuteTool(default_session_id="real-wire")
        with pytest.raises(RuntimeError, match="billing is uncertain") as error:
            tool.execute(tool_id="fixture", parameters={})
        assert isinstance(error.value.__cause__, qt.httpx.RemoteProtocolError)
        assert qt._credit_ledger().spent("real-wire") == 1
        assert json.loads(tool.execute(tool_id="fixture", parameters={}))["status"] == "budget_exceeded"
        assert len([p for p in requests if p.startswith("/tools/execute")]) == 1
    finally:
        client._client.close()
        server.shutdown()
        server.server_close()
        thread.join(2)


@pytest.mark.parametrize("cost", [None, True, "NaN", -1])
def test_missing_or_invalid_actual_cost_is_explicitly_estimated(monkeypatch, cost):
    qt.save_qveris_config(qt.QVerisConfig(api_key="fixture", mode="paid"))
    class Client:
        def execute(self, *args, **kwargs):
            return {"cost": cost, "result": {}}
    monkeypatch.setattr(qt.QVerisExecuteTool, "_client", lambda self: Client())
    out = json.loads(qt.QVerisExecuteTool(default_session_id="estimated").execute(tool_id="t", parameters={}, expected_cost=2))
    assert out["ok"] is True and out["cost_estimated"] is True
    assert out["cost"] == out["session_spent_credits"] == 2
