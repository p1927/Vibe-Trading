"""Fork-only outage-memory staleness helpers, used by ``src/memory/persistent.py``
and ``src/agent/context.py`` (both upstream files).

Kept in a sidecar module -- pure, self-contained functions/constants only -- so
those upstream files carry at most a one-line import plus a one-line call into
here instead of this logic being spliced into their bodies. Both call sites
exist for the same reason: a memory whose name/description/content reads like
a point-in-time infra outage or tool-error report (a NameError, a "backend
broken" note, a crash) is not a durable fact worth treating as still-true
indefinitely -- it's a snapshot of system state that may already be stale by
the time it's recalled.

- ``persistent.py``'s ``PersistentMemory.add()`` calls ``refuse_if_outage_report()``
  to reject new entries of this shape outright, so the store stops
  accumulating them going forward.
- ``context.py``'s ``ContextBuilder._is_stale_outage_memory()`` calls
  ``is_stale_outage_entry()`` to flag an already-recalled entry as unverified
  once it's past the trust window, covering entries written before the
  write-side guard existed.

See .claude/backlog/items/2026-08-29-autonomous-agent-retry-fix-not-live-effective.md.
"""

from __future__ import annotations

import re
import time

OUTAGE_MEMORY_RE = re.compile(
    r"\b(nameerror|not defined|traceback|exception|crash(?:ed)?|broken|outage|"
    r"unavailable|fail(?:ed|ure)?|still active|down)\b",
    re.IGNORECASE,
)

# How long a recalled outage-shaped memory is trusted as still-current before
# a read gets the UNVERIFIED wrapper.
STALE_OUTAGE_MEMORY_SECONDS = 2 * 3600.0

OUTAGE_REPORT_REFUSAL_MESSAGE = (
    "refused: this reads like a point-in-time infra outage/tool-error report "
    "(NameError, 'backend broken', a crash, ...), not a durable fact. Persistent "
    "memory survives across sessions and is never re-verified once saved, so a "
    "saved outage note gets treated as still-true long after the fault may be "
    "fixed. Do not save transient tool failures here — mention the failure in "
    "this turn's own reply instead, and re-check with a live tool call on the "
    "next turn rather than relying on a saved note."
)

UNVERIFIED_RECALL_SUFFIX = (
    " [UNVERIFIED — this describes a past infra failure and may be stale; call "
    "the relevant tool to re-check current state before treating it as still true]"
)


def refuse_if_outage_report(name: str, description: str, content: str) -> None:
    """Raise ValueError if name/description/content reads like an infra outage report."""
    if OUTAGE_MEMORY_RE.search(f"{name} {description} {content}"):
        raise ValueError(OUTAGE_REPORT_REFUSAL_MESSAGE)


def is_stale_outage_entry(title: str, description: str, created_at: float) -> bool:
    """True if title/description reads like an outage report older than the trust window."""
    if not OUTAGE_MEMORY_RE.search(f"{title} {description}"):
        return False
    return (time.time() - created_at) > STALE_OUTAGE_MEMORY_SECONDS
