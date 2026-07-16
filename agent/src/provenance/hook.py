"""Build provenance records from agent events."""

from __future__ import annotations

import uuid
from typing import Any

from src.provenance.formatters import (
    summarize_debate_artifact,
    summarize_goal_evidence,
    summarize_hub_artifact,
    summarize_tool_result,
)
from src.provenance.models import ProvenanceSource
from src.provenance.store import get_provenance_store


def _new_ref(prefix: str = "src") -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


def record_tool_result(
    session_id: str,
    *,
    tool: str,
    raw: str,
    status: str = "ok",
    attempt_id: str | None = None,
    tool_call_id: str | None = None,
) -> ProvenanceSource | None:
    """Record a tool execution as a provenance source."""
    if not session_id or not tool:
        return None
    if tool in {"compact"}:
        return None

    display, summary, category = summarize_tool_result(tool, raw, status=status)
    ref_id = _new_ref()
    if tool_call_id:
        ref_id = f"src_{tool_call_id[:12]}"

    source = ProvenanceSource(
        ref_id=ref_id,
        session_id=session_id,
        attempt_id=attempt_id,
        display_name=display,
        summary=summary,
        category=category,
        provider=tool.split("_")[0] if tool.startswith("mcp_") else "agent_tool",
        source_type="tool_result",
        tool_name=tool,
        freshness_status="live" if status == "ok" else "unknown",
        raw_data=raw or "",
    )
    return get_provenance_store().add(source)


def record_hub_artifact(
    session_id: str,
    *,
    ticker: str,
    artifact: dict[str, Any],
    attempt_id: str | None = None,
) -> ProvenanceSource | None:
    if not session_id or not ticker:
        return None
    display, summary = summarize_hub_artifact(ticker, artifact)
    ref_id = f"src_hub_{ticker.strip().upper()}"
    source = ProvenanceSource(
        ref_id=ref_id,
        session_id=session_id,
        attempt_id=attempt_id,
        display_name=display,
        summary=summary,
        category="research",
        provider="hub",
        source_type="hub_research",
        artifact_path=f"reports/hub/{ticker.upper()}",
        freshness_status="fresh",
        raw_data=_safe_json(artifact),
    )
    return get_provenance_store().add(source)


def record_goal_evidence(
    session_id: str,
    *,
    evidence: dict[str, Any],
    attempt_id: str | None = None,
) -> ProvenanceSource | None:
    if not session_id or not evidence:
        return None
    evidence_id = str(evidence.get("evidence_id") or "").strip()
    display, summary = summarize_goal_evidence(evidence)
    ref_id = f"src_ev_{evidence_id[:12]}" if evidence_id else _new_ref("src_ev")
    source = ProvenanceSource(
        ref_id=ref_id,
        session_id=session_id,
        attempt_id=attempt_id,
        display_name=display,
        summary=summary,
        category="evidence",
        provider=str(evidence.get("source_provider") or "agent"),
        source_type=str(evidence.get("source_type") or "goal_evidence"),
        data_as_of=str(evidence.get("data_as_of") or "") or None,
        freshness_status=str(evidence.get("freshness_status") or "unknown"),
        artifact_path=str(evidence.get("artifact_path") or "") or None,
        source_uri=str(evidence.get("source_uri") or "") or None,
        raw_data=_safe_json(evidence),
    )
    return get_provenance_store().add(source)


def record_debate_artifact(
    session_id: str,
    *,
    ticker: str,
    debate: dict[str, Any],
    attempt_id: str | None = None,
) -> ProvenanceSource | None:
    if not session_id or not ticker:
        return None
    display, summary = summarize_debate_artifact(ticker, debate)
    ref_id = f"src_debate_{ticker.strip().upper()}"
    source = ProvenanceSource(
        ref_id=ref_id,
        session_id=session_id,
        attempt_id=attempt_id,
        display_name=display,
        summary=summary,
        category="research",
        provider="tradingagents",
        source_type="agent_debate",
        artifact_path=f"reports/hub/{ticker.upper()}/agent_debate",
        freshness_status="fresh",
        raw_data=_safe_json(debate),
    )
    return get_provenance_store().add(source)


def record_from_event(
    session_id: str,
    event_type: str,
    data: dict[str, Any],
    *,
    attempt_id: str | None = None,
    full_result: str | None = None,
) -> ProvenanceSource | None:
    """Map known SSE payloads to provenance sources."""
    if event_type == "tool_result":
        tool = str(data.get("tool") or "")
        status = str(data.get("status") or "ok")
        raw = full_result if full_result is not None else str(data.get("preview") or "")
        return record_tool_result(
            session_id,
            tool=tool,
            raw=raw,
            status=status,
            attempt_id=attempt_id or str(data.get("attempt_id") or "") or None,
        )

    if event_type == "research.artifact":
        ticker = str(data.get("ticker") or "")
        artifact = data.get("artifact")
        if isinstance(artifact, dict):
            return record_hub_artifact(
                session_id,
                ticker=ticker,
                artifact=artifact,
                attempt_id=attempt_id,
            )
        return None

    if event_type == "goal.evidence":
        evidence = data.get("evidence")
        if isinstance(evidence, dict):
            return record_goal_evidence(
                session_id,
                evidence=evidence,
                attempt_id=attempt_id,
            )
        return None

    if event_type == "research.debate":
        ticker = str(data.get("ticker") or "")
        debate = data.get("debate")
        if isinstance(debate, dict) and ticker:
            return record_debate_artifact(
                session_id,
                ticker=ticker,
                debate=debate,
                attempt_id=attempt_id,
            )
        return None

    return None


def _safe_json(payload: Any) -> str:
    import json

    try:
        return json.dumps(payload, ensure_ascii=False, indent=2)
    except (TypeError, ValueError):
        return str(payload)
