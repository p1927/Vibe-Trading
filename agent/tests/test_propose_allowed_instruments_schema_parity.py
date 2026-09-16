"""`propose_autonomous_agent` has three LLM-facing schemas for `allowed_instruments`
(openalgo MCP tool, this in-process orchestrator tool, and the Trade validator's
`ALLOWED_INSTRUMENT_VALUES`). They drifted once already: the in-process tool's enum was
`["equity", "options"]` while the validator and MCP tool both accepted "futures" too. See
Trade's `.claude/backlog/items/2026-09-11-inprocess-propose-tool-omits-futures.md` and the
closed `.claude/backlog/archive/items/2026-09-07-mcp-propose-allowed-instruments-schema-mismatch.md`.

`ALLOWED_INSTRUMENT_VALUES` in `trade_integrations.autonomous_agents.proposals` is the single
source of truth; this pins this tool's schema to it so a future edit to either side that lets
them diverge fails a test instead of silently reaching an LLM.
"""

from __future__ import annotations

from trade_integrations.autonomous_agents.proposals import ALLOWED_INSTRUMENT_VALUES

from src.tools.propose_autonomous_agent_tool import ProposeAutonomousAgentTool


def test_allowed_instruments_enum_matches_the_validator_source_of_truth() -> None:
    schema_enum = ProposeAutonomousAgentTool.parameters["properties"]["allowed_instruments"][
        "items"
    ]["enum"]

    assert set(schema_enum) == set(ALLOWED_INSTRUMENT_VALUES)
