"""Tests for UI-oriented run reconstruction services."""

from __future__ import annotations

import json
import sys
import types
from pathlib import Path

import pandas as pd
import pytest

from src.ui_services import reconstruct_price_series


def test_reconstruct_price_series_preserves_configured_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_dir = tmp_path / "completed-run"
    (run_dir / "code").mkdir(parents=True)
    (run_dir / "code" / "signal_engine.py").write_text(
        "class SignalEngine:\n    pass\n", encoding="utf-8"
    )
    (run_dir / "req.json").write_text(
        json.dumps(
            {
                "prompt": "test",
                "context": {
                    "codes": ["BTC-USDT"],
                    "start_date": "2026-01-01",
                    "end_date": "2026-01-02",
                },
            }
        ),
        encoding="utf-8",
    )
    (run_dir / "config.json").write_text(
        json.dumps(
            {
                "codes": ["BTC-USDT"],
                "start_date": "2026-01-01",
                "end_date": "2026-01-02",
                "source": "okx",
            }
        ),
        encoding="utf-8",
    )

    selected_sources: list[str] = []

    def install_loader(source: str) -> None:
        module = types.ModuleType(f"backtest.loaders.{source}")

        class StubLoader:
            def fetch(self, codes, start_date, end_date):
                selected_sources.append(source)
                index = pd.DatetimeIndex([pd.Timestamp("2026-01-01")])
                return {
                    codes[0]: pd.DataFrame(
                        {
                            "open": [1.0],
                            "high": [1.0],
                            "low": [1.0],
                            "close": [1.0],
                            "volume": [1.0],
                        },
                        index=index,
                    )
                }

        module.DataLoader = StubLoader
        monkeypatch.setitem(sys.modules, module.__name__, module)

    install_loader("okx")
    install_loader("tushare")
    llm_module = types.ModuleType("src.providers.llm")
    llm_module._ensure_dotenv = lambda: None
    monkeypatch.setitem(sys.modules, llm_module.__name__, llm_module)

    rows = reconstruct_price_series(run_dir)

    assert selected_sources == ["okx"]
    assert rows[0]["code"] == "BTC-USDT"
