"""Agent core module: ReAct AgentLoop, tool registry, context, workspace memory, skills."""

from importlib import import_module

# Resolved on first attribute access (PEP 562), not at package import: `src.agent.tools` is imported
# by every tool module, and importing this package eagerly pulled `AgentLoop` -> `src.providers.llm`
# -> langchain_openai (~1.6 s CPU) into every process that only needed `BaseTool`, the Vibe API's
# startup included (Trade backlog 2026-09-23-vibe-api-import-cost).
_EXPORTS = {
    "AgentLoop": "src.agent.loop",
    "WorkspaceMemory": "src.agent.memory",
    "SkillsLoader": "src.agent.skills",
    "BaseTool": "src.agent.tools",
    "ToolRegistry": "src.agent.tools",
}


def __getattr__(name: str):
    if name in _EXPORTS:
        return getattr(import_module(_EXPORTS[name]), name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ["AgentLoop", "WorkspaceMemory", "SkillsLoader", "BaseTool", "ToolRegistry"]
