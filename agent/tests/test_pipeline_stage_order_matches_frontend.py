"""The Prediction page's hand-copied pipeline stage list matches the backend's.

`PredictionPipelinePanel.tsx` copies `pipeline_log.PIPELINE_STAGE_ORDER`; a stage renamed or added on the
backend without the copy following would read in the UI as an unordered row. Also pins every stage the
aggregator logs to the list, so a rename there fails here.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytest.importorskip("trade_integrations")

_TS = Path(__file__).resolve().parents[2] / "frontend" / "src" / "components" / "prediction" / "PredictionPipelinePanel.tsx"


@pytest.mark.skipif(not _TS.is_file(), reason="frontend source not present (standalone vibe-trading)")
def test_frontend_stage_list_equals_backend_order() -> None:
    from trade_integrations.dataflows.index_research.pipeline_log import PIPELINE_STAGE_ORDER

    text = re.sub(r"//[^\n]*", "", _TS.read_text(encoding="utf-8"))
    match = re.search(r"const PIPELINE_STAGE_ORDER = \[(.*?)\] as const;", text, re.S)
    assert match, "PIPELINE_STAGE_ORDER not found in the panel"
    assert tuple(re.findall(r'"([a-z_]+)"', match.group(1))) == PIPELINE_STAGE_ORDER


def test_every_aggregator_stage_is_in_the_order() -> None:
    import trade_integrations.dataflows.index_research.aggregator as aggregator
    from trade_integrations.dataflows.index_research.pipeline_log import PIPELINE_STAGE_ORDER

    src = Path(aggregator.__file__).read_text(encoding="utf-8")
    used = set(re.findall(r'\blog\.(?:stage_timer|info|warn|error|log)\(\s*"([a-z_]+)"', src))
    assert used, "no stage literals found; the pattern needs updating"
    assert not used - set(PIPELINE_STAGE_ORDER), sorted(used - set(PIPELINE_STAGE_ORDER))
