"""Provenance source records exposed to the Vibe Trading UI."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@dataclass
class ProvenanceSource:
    """One user-visible data source used during an agent turn."""

    ref_id: str
    session_id: str
    display_name: str
    summary: str
    category: str = "tool"
    provider: str = "agent"
    source_type: str = "tool_result"
    attempt_id: str | None = None
    tool_name: str | None = None
    retrieved_at: str = field(default_factory=_utc_now_iso)
    data_as_of: str | None = None
    freshness_status: str = "unknown"
    artifact_path: str | None = None
    source_uri: str | None = None
    raw_data: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> ProvenanceSource:
        known = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in payload.items() if k in known})
