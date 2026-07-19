"""Memory system evaluation: retrieval quality metrics.

Validates Phase 1 memory system improvements via synthetic benchmarks
measuring P@5, MRR, and importance-weighting effectiveness.
"""

from __future__ import annotations
import random
import time
from pathlib import Path
from typing import Any

import pytest

from src.config.accessor import reset_env_config
from src.memory.persistent import PersistentMemory


@pytest.fixture(autouse=True)
def _reset_config_cache():
    """Reset env config singleton so monkeypatch.setenv() takes effect."""
    reset_env_config()
    yield
    reset_env_config()


# ---------------------------------------------------------------------------
# Synthetic data generation
# ---------------------------------------------------------------------------

_CATEGORY_WEIGHTS = {"strategy": 0.25, "market_data": 0.15, "backtest_result": 0.20, "decision_log": 0.40}

_TEMPLATES: dict[str, list[dict[str, Any]]] = {
    "strategy": [
        {"name": "dual moving average crossover", "keywords": ["moving_average", "crossover", "trend"],
         "body": "Strategy uses 5d and 20d moving average crossover signals for long entry."},
        {"name": "momentum breakout strategy", "keywords": ["momentum", "breakout", "volume"],
         "body": "Enter long when price breaks above 20d high with volume exceeding 2x average."},
        {"name": "mean reversion bollinger bands", "keywords": ["reversion", "bollinger", "oversold"],
         "body": "Buy when price touches lower Bollinger band and RSI below 30."},
        {"name": "pairs trading statistical arbitrage", "keywords": ["pairs", "arbitrage", "cointegration"],
         "body": "Identify cointegrated stock pairs. Trade spread when z-score exceeds 2 std."},
        {"name": "volatility regime adaptive allocation", "keywords": ["volatility", "regime", "allocation"],
         "body": "Switch portfolios based on VIX regime classification using HMM model."},
    ],
    "market_data": [
        {"name": "tushare daily bar loader config", "keywords": ["tushare", "daily", "loader"],
         "body": "Loads daily OHLCV bars from tushare API. Supports A-share and ETF symbols."},
        {"name": "realtime tick data streaming", "keywords": ["realtime", "tick", "streaming"],
         "body": "WebSocket connection to market data feed with reconnection logic."},
        {"name": "corporate earnings calendar events", "keywords": ["earnings", "calendar", "fundamental"],
         "body": "Fetches quarterly earnings dates for event-driven signal generation."},
    ],
    "backtest_result": [
        {"name": "sharpe ratio optimization results", "keywords": ["sharpe", "optimization", "performance"],
         "body": "Backtest achieved Sharpe 1.8 with max drawdown 12% over 5 years."},
        {"name": "transaction cost sensitivity analysis", "keywords": ["transaction", "cost", "slippage"],
         "body": "Impact of transaction costs: 0.1% commission reduces Sharpe by 0.3."},
        {"name": "walk forward validation report", "keywords": ["walkforward", "validation", "overfit"],
         "body": "Walk-forward analysis with 252d training and 63d test window."},
        {"name": "drawdown recovery time statistics", "keywords": ["drawdown", "recovery", "risk"],
         "body": "Maximum drawdown recovery took 45 trading days average 18 days."},
    ],
    "decision_log": [
        {"name": "reduce position size after volatility spike", "keywords": ["position", "volatility", "risk_management"],
         "body": "Reduce position size by 50% when realized volatility exceeds 2x historical."},
        {"name": "switch data provider from yahoo to tushare", "keywords": ["provider", "tushare", "migration"],
         "body": "Migrated from Yahoo to Tushare for A-share data quality improvement."},
        {"name": "disable overnight holding for intraday strategy", "keywords": ["intraday", "overnight", "holding"],
         "body": "Disabled overnight holding to avoid gap risk. Close before 15:00."},
        {"name": "increase backtest history from 3 to 5 years", "keywords": ["backtest", "history", "sample_size"],
         "body": "Extended backtest period to include bull and bear market cycles."},
        {"name": "adopt ensemble signal for entry confirmation", "keywords": ["ensemble", "signal", "confirmation"],
         "body": "Combined trend momentum and volume signals. Reduced false signals 40%."},
        {"name": "implement stop loss trailing mechanism", "keywords": ["stop_loss", "trailing", "exit"],
         "body": "Trailing stop at 2x ATR below highest price since entry."},
    ],
}

_LANG_SUFFIXES = {
    "en": " Performance validated with historical data.",
    "cn": " 经过历史数据验证有效。",
    "mixed": " Tested on A-share market 回测验证通过。",
}


class SyntheticMemoryFactory:
    """Generates realistic trading-related synthetic memory entries."""

    def __init__(self, memory_dir: Path, seed: int = 42) -> None:
        self._dir = memory_dir
        self._rng = random.Random(seed)
        self._ground_truth: list[dict[str, Any]] = []

    @property
    def ground_truth(self) -> list[dict[str, Any]]:
        """Return query-answer pairs for evaluation."""
        return self._ground_truth

    def generate(self, count: int = 100) -> list[Path]:
        """Generate `count` memory entries with known ground truth."""
        paths: list[Path] = []
        categories = list(_CATEGORY_WEIGHTS.keys())
        weights = list(_CATEGORY_WEIGHTS.values())

        for i in range(count):
            cat = self._rng.choices(categories, weights=weights, k=1)[0]
            template = self._rng.choice(_TEMPLATES[cat])

            # Assign language
            lang = self._rng.choices(
                ["en", "cn", "mixed"], weights=[0.5, 0.2, 0.3], k=1
            )[0]
            suffix = _LANG_SUFFIXES[lang]

            # Unique name
            name = f"{template['name']} v{i}"
            keywords = template["keywords"]
            body = template["body"] + suffix

            # Quality and access attributes vary
            quality_score = round(self._rng.uniform(0.3, 0.9), 2)
            access_count = self._rng.randint(0, 20)
            days_ago = self._rng.randint(0, 60)
            last_accessed = time.strftime(
                "%Y-%m-%dT%H:%M:%S",
                time.gmtime(time.time() - days_ago * 86400),
            )

            path = self._write_entry(
                name=name,
                keywords=keywords,
                body=body,
                quality_score=quality_score,
                access_count=access_count,
                last_accessed=last_accessed,
                idx=i,
            )
            paths.append(path)

        self._build_ground_truth()
        return paths

    def _write_entry(
        self,
        name: str,
        keywords: list[str],
        body: str,
        quality_score: float,
        access_count: int,
        last_accessed: str,
        idx: int,
    ) -> Path:
        """Write a memory file with full frontmatter."""
        slug = name.lower().replace(" ", "_")[:50]
        filename = f"project_{slug}_{idx}.md"
        path = self._dir / filename
        kw_str = ", ".join(keywords)
        entry_id = f"{idx:06x}"[-6:]
        now_iso = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())

        content = (
            f"---\nname: {name}\n"
            f"description: {name}\n"
            f"type: project\n"
            f"id: {entry_id}\n"
            f"created_at: 2025-01-01T00:00:00\n"
            f"updated_at: {now_iso}\n"
            f"keywords: [{kw_str}]\n"
            f"quality_score: {quality_score}\n"
            f"access_count: {access_count}\n"
            f"last_accessed: {last_accessed}\n"
            f"importance: 0.5\n"
            f"related_memories: []\n"
            f"---\n\n{body}"
        )
        path.write_text(content, encoding="utf-8")
        return path

    def _build_ground_truth(self) -> None:
        """Build query-answer pairs from known templates."""
        # Each query targets specific keywords that guarantee matches
        self._ground_truth = [
            {"query": "moving average crossover trend", "relevant_kw": ["crossover", "moving_average"]},
            {"query": "momentum breakout volume signal", "relevant_kw": ["momentum", "breakout"]},
            {"query": "bollinger bands mean reversion", "relevant_kw": ["reversion", "bollinger"]},
            {"query": "pairs trading cointegration arbitrage", "relevant_kw": ["pairs", "arbitrage"]},
            {"query": "volatility regime allocation model", "relevant_kw": ["volatility", "regime"]},
            {"query": "tushare daily data loader", "relevant_kw": ["tushare", "daily"]},
            {"query": "realtime tick streaming websocket", "relevant_kw": ["realtime", "tick"]},
            {"query": "earnings calendar fundamental events", "relevant_kw": ["earnings", "calendar"]},
            {"query": "sharpe ratio optimization backtest", "relevant_kw": ["sharpe", "optimization"]},
            {"query": "transaction cost slippage analysis", "relevant_kw": ["transaction", "slippage"]},
            {"query": "walk forward validation overfit", "relevant_kw": ["walkforward", "validation"]},
            {"query": "drawdown recovery risk statistics", "relevant_kw": ["drawdown", "recovery"]},
            {"query": "position size volatility risk management", "relevant_kw": ["position", "volatility"]},
            {"query": "data provider tushare migration", "relevant_kw": ["provider", "tushare"]},
            {"query": "intraday overnight holding gap", "relevant_kw": ["intraday", "overnight"]},
            {"query": "backtest history sample size", "relevant_kw": ["backtest", "history"]},
            {"query": "ensemble signal confirmation entry", "relevant_kw": ["ensemble", "signal"]},
            {"query": "trailing stop loss exit mechanism", "relevant_kw": ["stop_loss", "trailing"]},
            {"query": "risk drawdown maximum recovery", "relevant_kw": ["drawdown", "risk"]},
            {"query": "strategy crossover moving average", "relevant_kw": ["crossover", "moving_average"]},
        ]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def memory_factory(tmp_path: Path) -> SyntheticMemoryFactory:
    """Create factory with 100 entries in tmp dir."""
    factory = SyntheticMemoryFactory(tmp_path, seed=42)
    factory.generate(100)
    return factory

@pytest.fixture()
def memory_instance(tmp_path: Path, memory_factory: SyntheticMemoryFactory) -> PersistentMemory:
    """PersistentMemory instance backed by synthetic data."""
    return PersistentMemory(memory_dir=tmp_path)


# ---------------------------------------------------------------------------
# Test: Precision@5
# ---------------------------------------------------------------------------

class TestRetrievalPrecision:
    """Evaluate precision@5 on synthetic memory corpus."""

    def _is_relevant(self, entry: Any, relevant_keywords: list[str]) -> bool:
        """Check if entry matches ground-truth relevant keywords."""
        entry_kw_set = set(entry.keywords)
        entry_body_lower = entry.body.lower()
        entry_title_lower = entry.title.lower()
        for kw in relevant_keywords:
            if kw in entry_kw_set:
                return True
            if kw.lower() in entry_body_lower or kw.lower() in entry_title_lower:
                return True
        return False

    def test_precision_at_5(
        self, memory_instance: PersistentMemory, memory_factory: SyntheticMemoryFactory
    ) -> None:
        """P@5 should be >= 0.6 for single-hop keyword queries."""
        precisions: list[float] = []

        for gt in memory_factory.ground_truth:
            results = memory_instance.find_relevant(gt["query"], max_results=5)
            relevant_count = sum(
                1 for r in results if self._is_relevant(r, gt["relevant_kw"])
            )
            # P@5 = relevant_in_top5 / min(5, total_relevant)
            # Since each query maps to multiple entries, use 5 as denominator
            p_at_5 = relevant_count / min(5, max(1, len(results)))
            precisions.append(p_at_5)

        avg_precision = sum(precisions) / len(precisions) if precisions else 0.0
        assert avg_precision >= 0.6, (
            f"Average P@5 = {avg_precision:.3f}, expected >= 0.6"
        )


# ---------------------------------------------------------------------------
# Test: MRR (Mean Reciprocal Rank)
# ---------------------------------------------------------------------------

class TestMRR:
    """Evaluate Mean Reciprocal Rank on synthetic queries."""

    def _is_relevant(self, entry: Any, relevant_keywords: list[str]) -> bool:
        """Check if entry matches ground-truth relevant keywords."""
        entry_kw_set = set(entry.keywords)
        entry_body_lower = entry.body.lower()
        entry_title_lower = entry.title.lower()
        for kw in relevant_keywords:
            if kw in entry_kw_set:
                return True
            if kw.lower() in entry_body_lower or kw.lower() in entry_title_lower:
                return True
        return False

    def test_mrr(
        self, memory_instance: PersistentMemory, memory_factory: SyntheticMemoryFactory
    ) -> None:
        """MRR should be >= 0.7 for keyword-based retrieval."""
        reciprocal_ranks: list[float] = []

        for gt in memory_factory.ground_truth:
            results = memory_instance.find_relevant(gt["query"], max_results=5)
            rr = 0.0
            for rank, entry in enumerate(results, start=1):
                if self._is_relevant(entry, gt["relevant_kw"]):
                    rr = 1.0 / rank
                    break
            reciprocal_ranks.append(rr)

        mrr = sum(reciprocal_ranks) / len(reciprocal_ranks) if reciprocal_ranks else 0.0
        assert mrr >= 0.7, f"MRR = {mrr:.3f}, expected >= 0.7"


# ---------------------------------------------------------------------------
# Test: Importance weighting effectiveness
# ---------------------------------------------------------------------------

class TestImportanceWeighting:
    """Compare importance-weighted vs baseline retrieval."""

    def _create_competing_entries(self, tmp_path: Path) -> None:
        """Create entries where importance should break ties."""
        # High-importance entry: high quality, recent access, many accesses
        now_iso = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())
        high_imp = (
            "---\nname: alpha signal generator\n"
            "description: alpha signal generator\n"
            "type: project\n"
            "id: aaa111\n"
            "created_at: 2025-01-01T00:00:00\n"
            "updated_at: " + now_iso + "\n"
            "keywords: [alpha, signal, factor]\n"
            "quality_score: 0.9\n"
            "access_count: 15\n"
            "last_accessed: " + now_iso + "\n"
            "importance: 0.9\n"
            "related_memories: []\n"
            "---\n\nAlpha signal generator uses factor momentum.\n"
        )
        (tmp_path / "project_alpha_high.md").write_text(high_imp, encoding="utf-8")

        # Low-importance entry: same keywords but low quality, old access
        old_date = "2024-01-01T00:00:00"
        low_imp = (
            "---\nname: alpha signal prototype\n"
            "description: alpha signal prototype\n"
            "type: project\n"
            "id: bbb222\n"
            "created_at: 2024-01-01T00:00:00\n"
            "updated_at: " + old_date + "\n"
            "keywords: [alpha, signal, factor]\n"
            "quality_score: 0.2\n"
            "access_count: 0\n"
            "last_accessed: " + old_date + "\n"
            "importance: 0.1\n"
            "related_memories: []\n"
            "---\n\nAlpha signal prototype for factor research.\n"
        )
        (tmp_path / "project_alpha_low.md").write_text(low_imp, encoding="utf-8")

    def test_importance_boosts_ranking(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """High-importance entries should rank above low-importance with same tokens."""
        monkeypatch.setenv("VT_MEMORY_DECAY", "1")
        self._create_competing_entries(tmp_path)
        mem = PersistentMemory(memory_dir=tmp_path)

        results = mem.find_relevant("alpha signal factor momentum")
        assert len(results) >= 2
        # High-importance entry should be first
        assert "generator" in results[0].title.lower()

    def test_decay_disabled_uses_quality_only(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """With VT_MEMORY_DECAY=0, importance equals quality_score."""
        monkeypatch.setenv("VT_MEMORY_DECAY", "0")
        self._create_competing_entries(tmp_path)
        mem = PersistentMemory(memory_dir=tmp_path)

        results = mem.find_relevant("alpha signal factor")
        # Both should appear; higher quality_score still wins
        assert len(results) >= 2
        assert results[0].quality_score >= results[1].quality_score

    def test_importance_not_worse_than_baseline(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Importance weighting should not degrade precision vs no weighting."""
        factory = SyntheticMemoryFactory(tmp_path, seed=99)
        factory.generate(50)

        # Baseline: decay disabled
        monkeypatch.setenv("VT_MEMORY_DECAY", "0")
        mem_baseline = PersistentMemory(memory_dir=tmp_path)
        baseline_hits = 0
        for gt in factory.ground_truth[:10]:
            results = mem_baseline.find_relevant(gt["query"])
            for r in results:
                if any(kw in r.keywords or kw in r.body.lower() for kw in gt["relevant_kw"]):
                    baseline_hits += 1
                    break

        # Decay enabled
        monkeypatch.setenv("VT_MEMORY_DECAY", "1")
        mem_decay = PersistentMemory(memory_dir=tmp_path)
        decay_hits = 0
        for gt in factory.ground_truth[:10]:
            results = mem_decay.find_relevant(gt["query"])
            for r in results:
                if any(kw in r.keywords or kw in r.body.lower() for kw in gt["relevant_kw"]):
                    decay_hits += 1
                    break

        # Decay-enabled should not be worse
        assert decay_hits >= baseline_hits - 1, (
            f"Decay model hits={decay_hits} vs baseline={baseline_hits}"
        )
