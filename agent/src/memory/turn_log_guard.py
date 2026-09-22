"""Fork-only guard: persistent memory holds durable facts, never turn-by-turn logs (Trade D218).

Sidecar for ``src/memory/persistent.py`` (upstream), which carries only a one-line call into
here, same pattern as ``outage_staleness.refuse_if_outage_report``.

Autonomous agents used ``remember`` as a turn journal: ~50 notes named like "NIFTY bootstrap
turn aa_32695249 -- HOLD, watch_spec drift repaired" padded every later session's context, and
one stale per-tier note ("record_autonomous_decision requires ^NSEI") was followed long after it
was corrected. A turn's outcome belongs in ``record_autonomous_decision`` and the agent's own
ledger; memory keeps what stays true across agents and sessions (an API contract, a user
preference, a verified market convention).

A note is refused when its title or description names one agent run (an ``aa_<hex>`` id) or
reads as a numbered/typed turn report. The body is not checked: a durable fact may cite the
agent where it was observed.
"""

from __future__ import annotations

import re

TURN_LOG_RE = re.compile(
    r"\baa_[0-9a-f]{3,}"
    r"|\bturn\s*(?:#\s*\d+|\(\s*aa_|\d+\b)"
    r"|\b(?:bootstrap|revision|re-?fire|watch|scheduler|research)\s+turn\b",
    re.IGNORECASE,
)

TURN_LOG_REFUSAL_MESSAGE = (
    "refused: this reads like a turn-by-turn log (it names one agent run, aa_..., or reports a "
    "single turn). Persistent memory is for durable facts that stay true across agents and "
    "sessions: API contracts, user preferences, verified market conventions. Record a turn's "
    "decision and outcome with record_autonomous_decision instead; if the turn taught a general "
    "rule, save that rule with a title that does not name the agent or the turn."
)


def refuse_if_turn_log(name: str, description: str) -> None:
    """Raise ValueError if the title/description reads like a per-agent turn log."""
    if TURN_LOG_RE.search(f"{name} {description}"):
        raise ValueError(TURN_LOG_REFUSAL_MESSAGE)
