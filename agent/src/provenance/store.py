"""In-memory provenance registry keyed by session."""

from __future__ import annotations

import threading
from typing import Dict, List

from src.provenance.models import ProvenanceSource

_MAX_RAW = 12_000


class ProvenanceStore:
    """Thread-safe session provenance list."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._by_session: Dict[str, List[ProvenanceSource]] = {}

    def add(self, source: ProvenanceSource) -> ProvenanceSource:
        raw = source.raw_data or ""
        if len(raw) > _MAX_RAW:
            source.raw_data = raw[:_MAX_RAW]
        with self._lock:
            bucket = self._by_session.setdefault(source.session_id, [])
            for idx, existing in enumerate(bucket):
                if existing.ref_id == source.ref_id:
                    bucket[idx] = source
                    return source
            bucket.append(source)
            return source

    def list_session(self, session_id: str) -> list[ProvenanceSource]:
        with self._lock:
            return list(self._by_session.get(session_id, []))

    def get(self, session_id: str, ref_id: str) -> ProvenanceSource | None:
        with self._lock:
            for item in self._by_session.get(session_id, []):
                if item.ref_id == ref_id:
                    return item
        return None

    def clear_session(self, session_id: str) -> None:
        with self._lock:
            self._by_session.pop(session_id, None)


_store = ProvenanceStore()


def get_provenance_store() -> ProvenanceStore:
    return _store
