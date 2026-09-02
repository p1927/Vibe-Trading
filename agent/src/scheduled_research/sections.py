"""Maps a job's ``job_type`` to the section it's grouped under in the Scheduler tab.

Each pipeline module already owns a frozenset of the job types it dispatches
(``INDEX_JOB_TYPES``, ``OPTIONS_JOB_TYPES``, ...); this module is the one
place that turns those into a human-facing grouping key, so the Scheduler
UI's tabs and any future cross-service registry entry agree on section
labels without each having to know every pipeline module's job types itself.
"""

from __future__ import annotations

from typing import Dict

from .autonomous_agent_jobs import AUTONOMOUS_JOB_TYPES
from .capture_jobs import HUB_CAPTURE_JOB_TYPES
from .financial_knowledge_jobs import FINANCIAL_KNOWLEDGE_JOB_TYPES
from .hub_calibration_jobs import HUB_CALIBRATION_JOB_TYPES
from .index_jobs import INDEX_JOB_TYPES
from .options_jobs import OPTIONS_JOB_TYPES
from .recording_wake_jobs import RECORDING_WAKE_JOB_TYPES
from .trade_data_jobs import TRADE_DATA_JOB_TYPES

GENERAL_SECTION = "general"

# Order matters only in that each job type must appear in exactly one set;
# INDEX_JOB_TYPES already covers the hub_news_* types (they feed the index
# prediction pipeline), so hub calibration/capture are their own "hub"
# section rather than folded into "prediction".
_SECTION_JOB_TYPES: Dict[str, frozenset] = {
    "prediction": INDEX_JOB_TYPES,
    "options": OPTIONS_JOB_TYPES,
    "trade_data": TRADE_DATA_JOB_TYPES,
    "hub": HUB_CALIBRATION_JOB_TYPES | HUB_CAPTURE_JOB_TYPES,
    "knowledge": FINANCIAL_KNOWLEDGE_JOB_TYPES,
    "autonomous_agent": AUTONOMOUS_JOB_TYPES,
    "recording": RECORDING_WAKE_JOB_TYPES,
}

_JOB_TYPE_TO_SECTION: Dict[str, str] = {
    job_type: section
    for section, job_types in _SECTION_JOB_TYPES.items()
    for job_type in job_types
}


def job_section(job_type: str) -> str:
    """Return the section label for a job's ``config["job_type"]``.

    Args:
        job_type: The job's ``job_type`` config value, or ``""`` for an
            ad-hoc job with no pipeline type (a plain chat-prompt monitor).

    Returns:
        A section label. Unrecognized or empty types return
        :data:`GENERAL_SECTION` rather than raising, since an unknown type
        must still render somewhere in the grouped view.
    """
    return _JOB_TYPE_TO_SECTION.get(job_type, GENERAL_SECTION)
