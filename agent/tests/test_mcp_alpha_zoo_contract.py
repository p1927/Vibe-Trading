from __future__ import annotations

import asyncio
import inspect
import json
import re
from pathlib import Path

import mcp_server
from src.tools.alpha_bench_tool import AlphaBenchTool
from src.tools.alpha_zoo_tool import AlphaZooTool

_ALPHA_ZOO = getattr(mcp_server.alpha_zoo, "fn", None) or getattr(
    mcp_server.alpha_zoo, "__wrapped__", mcp_server.alpha_zoo
)
_ALPHA_BENCH = getattr(mcp_server.alpha_bench, "fn", None) or getattr(
    mcp_server.alpha_bench, "__wrapped__", mcp_server.alpha_bench
)


class _RecordingRegistry:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def execute(self, name: str, args: dict) -> str:
        self.calls.append((name, args))
        return '{"status":"ok"}'


def test_wrapper_signatures_match_registered_contracts() -> None:
    for wrapper, tool in (
        (_ALPHA_ZOO, AlphaZooTool),
        (_ALPHA_BENCH, AlphaBenchTool),
    ):
        signature = inspect.signature(wrapper)
        assert set(signature.parameters) == set(tool.parameters["properties"])
        required = {
            name for name, parameter in signature.parameters.items() if parameter.default is inspect.Parameter.empty
        }
        assert required == set(tool.parameters["required"])


def test_alpha_zoo_forwards_filters(monkeypatch) -> None:
    registry = _RecordingRegistry()
    monkeypatch.setattr(mcp_server, "_get_registry", lambda: registry)

    _ALPHA_ZOO(
        action="list_alphas",
        zoo="alpha101",
        theme="momentum",
        universe="equity_us",
        limit=25,
    )

    assert registry.calls == [
        (
            "alpha_zoo",
            {
                "action": "list_alphas",
                "limit": 25,
                "zoo": "alpha101",
                "theme": "momentum",
                "universe": "equity_us",
            },
        )
    ]


def test_alpha_bench_forwards_selection(monkeypatch) -> None:
    registry = _RecordingRegistry()
    monkeypatch.setattr(mcp_server, "_get_registry", lambda: registry)
    output_dir = Path.home() / ".vibe-trading" / "reports" / "alpha-zoo"

    _ALPHA_BENCH(
        universe="sp500",
        period="2018-2025",
        zoo="alpha101",
        top=10,
        output_dir=str(output_dir),
    )

    assert registry.calls == [
        (
            "alpha_bench",
            {
                "universe": "sp500",
                "period": "2018-2025",
                "top": 10,
                "zoo": "alpha101",
                "output_dir": str(output_dir.resolve()),
            },
        )
    ]


def test_skill_inventory_matches_static_mcp_registration() -> None:
    # Given the runtime MCP registration and the bundled SKILL inventory.
    registered = {tool.name for tool in asyncio.run(mcp_server.mcp.list_tools())}
    mirrored = {tool_cls.name for tool_cls in mcp_server._mirrored_tool_classes()}
    static = registered - mirrored
    skill_text = (Path(mcp_server.__file__).parent / "SKILL.md").read_text(encoding="utf-8")

    # When the SKILL header and table are read.
    header = re.search(r"^## Available MCP Tools \((\d+)\)$", skill_text, re.MULTILINE)

    # Then they describe exactly the non-mirrored MCP surface.
    assert header is not None
    tool_section = skill_text[header.end() :].split("\n## ", maxsplit=1)[0]
    listed = set(re.findall(r"^\| `([a-z_]+)` \|", tool_section, re.MULTILINE))
    assert int(header.group(1)) == len(static)
    assert listed == static


def test_alpha_bench_requires_a_bounded_selection(monkeypatch) -> None:
    registry = _RecordingRegistry()
    monkeypatch.setattr(mcp_server, "_get_registry", lambda: registry)

    result = json.loads(_ALPHA_BENCH(universe="sp500", period="2018-2025"))

    assert result["status"] == "error"
    assert "alpha_id or zoo" in result["error"]
    assert registry.calls == []


def test_alpha_bench_rejects_periods_over_ten_years(monkeypatch) -> None:
    registry = _RecordingRegistry()
    monkeypatch.setattr(mcp_server, "_get_registry", lambda: registry)

    result = json.loads(_ALPHA_BENCH(universe="sp500", period="2010-2025", zoo="alpha101"))

    assert result["status"] == "error"
    assert "10 years" in result["error"]
    assert registry.calls == []


def test_alpha_bench_rejects_conflicting_selection(monkeypatch) -> None:
    registry = _RecordingRegistry()
    monkeypatch.setattr(mcp_server, "_get_registry", lambda: registry)

    result = json.loads(
        _ALPHA_BENCH(
            universe="sp500",
            period="2018-2025",
            alpha_id="alpha101_001",
            zoo="alpha101",
        )
    )

    assert result["status"] == "error"
    assert "mutually exclusive" in result["error"]
    assert registry.calls == []


def test_alpha_bench_rejects_unbounded_top(monkeypatch) -> None:
    registry = _RecordingRegistry()
    monkeypatch.setattr(mcp_server, "_get_registry", lambda: registry)

    result = json.loads(
        _ALPHA_BENCH(
            universe="sp500",
            period="2018-2025",
            zoo="alpha101",
            top=101,
        )
    )

    assert result["status"] == "error"
    assert "between 1 and 100" in result["error"]
    assert registry.calls == []


def test_alpha_bench_rejects_output_outside_allowed_roots(monkeypatch) -> None:
    registry = _RecordingRegistry()
    monkeypatch.setattr(mcp_server, "_get_registry", lambda: registry)

    result = json.loads(
        _ALPHA_BENCH(
            universe="sp500",
            period="2018-2025",
            alpha_id="alpha101_001",
            output_dir="/tmp/alpha-bench-reports",
        )
    )

    assert result["status"] == "error"
    assert "allowed" in result["error"]
    assert registry.calls == []
