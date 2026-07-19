"""Memory system evaluation: latency benchmarks and GC accuracy.

Validates search performance under load and garbage collection precision
for identifying stale vs active memory entries.
"""

from __future__ import annotations

import random
import time
from pathlib import Path

import pytest

from src.memory.lifecycle import MemoryLifecycle
from src.memory.persistent import PersistentMemory


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_QUERY_CORPUS = [
    "moving average crossover strategy",
    "momentum breakout volume signal",
    "bollinger bands mean reversion oversold",
    "sharpe ratio optimization backtest",
    "transaction cost slippage analysis",
    "tushare daily data loader config",
    "realtime tick streaming websocket",
    "pairs trading arbitrage cointegration",
    "volatility regime allocation portfolio",
    "drawdown recovery risk management",
    "stop loss trailing exit mechanism",
    "ensemble signal confirmation entry",
    "position sizing volatility spike",
    "backtest history sample size period",
    "walk forward validation overfit detection",
    "intraday overnight holding gap risk",
    "earnings calendar fundamental events",
    "alpha factor momentum signal",
    "data provider migration tushare",
    "portfolio rebalance frequency optimal",
    "market microstructure order flow",
    "risk parity allocation weights",
    "sector rotation strategy timing",
    "machine learning feature selection",
    "sentiment analysis news trading",
    "options implied volatility surface",
    "futures basis spread arbitrage",
    "high frequency market making",
    "statistical factor model returns",
    "correlation matrix regime change",
    "liquidity premium small cap",
    "dividend yield value strategy",
    "relative strength momentum rank",
    "breakout confirmation volume filter",
    "mean reversion zscore threshold",
    "trend following turtle rules",
    "pairs spread cointegration halflife",
    "kelly criterion position sizing",
    "monte carlo simulation drawdown",
    "black scholes option pricing",
    "GARCH volatility forecast model",
    "Kalman filter signal extraction",
    "reinforcement learning trading agent",
    "genetic algorithm parameter tuning",
    "cross sectional momentum factor",
    "time series momentum lookback",
    "market neutral long short equity",
    "event driven merger arbitrage",
    "convertible bond arbitrage spread",
    "fixed income duration matching",
]

_BODY_TEMPLATES = [
    "Uses {kw1} combined with {kw2} for signal generation. Validated across multiple market regimes.",
    "Implementation of {kw1} strategy. Parameters optimized via {kw2} framework over 5 years.",
    "Research note on {kw1}: strong performance when combined with {kw2} filter.",
    "Decision to apply {kw1} approach. Backtested {kw2} confirms improvement.",
    "Configuration for {kw1} module. Integrates with {kw2} data pipeline.",
]

_KEYWORDS_POOL = [
    "momentum", "trend", "reversion", "volatility", "breakout",
    "allocation", "optimization", "sharpe", "drawdown", "risk",
    "factor", "alpha", "signal", "backtest", "portfolio",
    "tushare", "streaming", "loader", "arbitrage", "cointegration",
    "bollinger", "crossover", "moving_average", "position", "stop_loss",
]


def _generate_entries(memory_dir: Path, count: int, seed: int = 42) -> None:
    """Generate `count` synthetic memory entries in `memory_dir`."""
    rng = random.Random(seed)
    now = time.time()

    for i in range(count):
        kw_sample = rng.sample(_KEYWORDS_POOL, k=rng.randint(3, 5))
        template = rng.choice(_BODY_TEMPLATES)
        body = template.format(kw1=kw_sample[0], kw2=kw_sample[1])
        name = f"memory_entry_{i:04d}"
        kw_str = ", ".join(kw_sample)
        quality = round(rng.uniform(0.3, 0.9), 2)
        access = rng.randint(0, 15)
        days_ago = rng.randint(0, 45)
        last_acc = time.strftime(
            "%Y-%m-%dT%H:%M:%S", time.gmtime(now - days_ago * 86400)
        )
        entry_id = f"{i:06x}"[-6:]
        slug = f"project_{name}.md"
        path = memory_dir / slug
        content = (
            f"---\nname: {name}\n"
            f"description: {name} about {kw_sample[0]} and {kw_sample[1]}\n"
            f"type: project\n"
            f"id: {entry_id}\n"
            f"created_at: 2025-01-01T00:00:00\n"
            f"updated_at: {last_acc}\n"
            f"keywords: [{kw_str}]\n"
            f"quality_score: {quality}\n"
            f"access_count: {access}\n"
            f"last_accessed: {last_acc}\n"
            f"importance: 0.5\n"
            f"related_memories: []\n"
            f"---\n\n{body}"
        )
        path.write_text(content, encoding="utf-8")


def _generate_gc_entries(
    memory_dir: Path, active_count: int = 15, stale_count: int = 15
) -> tuple[list[str], list[str]]:
    """Generate active and stale entries. Returns (active_names, stale_names)."""
    now = time.time()
    active_names: list[str] = []
    stale_names: list[str] = []

    # Active entries: high quality, recent access, many accesses
    for i in range(active_count):
        name = f"active_entry_{i:03d}"
        active_names.append(name)
        last_acc = time.strftime(
            "%Y-%m-%dT%H:%M:%S", time.gmtime(now - 86400)  # 1 day ago
        )
        path = memory_dir / f"project_{name}.md"
        content = (
            f"---\nname: {name}\n"
            f"description: Active memory about trading signals\n"
            f"type: project\n"
            f"id: {i:06x}\n"
            f"created_at: 2024-06-01T00:00:00\n"
            f"updated_at: {last_acc}\n"
            f"keywords: [active, trading, signal]\n"
            f"quality_score: 0.7\n"
            f"access_count: 10\n"
            f"last_accessed: {last_acc}\n"
            f"importance: 0.7\n"
            f"related_memories: []\n"
            f"---\n\nActive entry with recent access and high quality."
        )
        path.write_text(content, encoding="utf-8")

    # Stale entries: low quality, old access, no accesses
    for i in range(stale_count):
        name = f"stale_entry_{i:03d}"
        stale_names.append(name)
        # 45 days ago
        last_acc = time.strftime(
            "%Y-%m-%dT%H:%M:%S", time.gmtime(now - 45 * 86400)
        )
        created = time.strftime(
            "%Y-%m-%dT%H:%M:%S", time.gmtime(now - 90 * 86400)
        )
        path = memory_dir / f"project_{name}.md"
        content = (
            f"---\nname: {name}\n"
            f"description: Stale memory about old experiment\n"
            f"type: project\n"
            f"id: {(i + active_count):06x}\n"
            f"created_at: {created}\n"
            f"updated_at: {last_acc}\n"
            f"keywords: [stale, old, deprecated]\n"
            f"quality_score: 0.1\n"
            f"access_count: 0\n"
            f"last_accessed: {last_acc}\n"
            f"importance: 0.05\n"
            f"related_memories: []\n"
            f"---\n\nStale entry not accessed in over 30 days with low quality."
        )
        path.write_text(content, encoding="utf-8")

    return active_names, stale_names


# ---------------------------------------------------------------------------
# Test: Search Latency
# ---------------------------------------------------------------------------


class TestSearchLatency:
    """Benchmark find_relevant latency with 500 entries."""

    @pytest.mark.benchmark
    def test_p50_latency(self, tmp_path: Path) -> None:
        """p50 latency of find_relevant should be < 200ms with 500 entries."""
        _generate_entries(tmp_path, count=500, seed=123)
        mem = PersistentMemory(memory_dir=tmp_path)
        rng = random.Random(77)
        queries = [rng.choice(_QUERY_CORPUS) for _ in range(50)]

        latencies: list[float] = []
        for q in queries:
            start = time.perf_counter()
            mem.find_relevant(q)
            elapsed_ms = (time.perf_counter() - start) * 1000
            latencies.append(elapsed_ms)

        latencies.sort()
        p50 = latencies[len(latencies) // 2]
        assert p50 < 200, f"p50 latency = {p50:.1f}ms, expected < 200ms"

    @pytest.mark.benchmark
    def test_p95_latency(self, tmp_path: Path) -> None:
        """p95 latency of find_relevant should be < 500ms with 500 entries."""
        _generate_entries(tmp_path, count=500, seed=456)
        mem = PersistentMemory(memory_dir=tmp_path)
        rng = random.Random(88)
        queries = [rng.choice(_QUERY_CORPUS) for _ in range(50)]

        latencies: list[float] = []
        for q in queries:
            start = time.perf_counter()
            mem.find_relevant(q)
            elapsed_ms = (time.perf_counter() - start) * 1000
            latencies.append(elapsed_ms)

        latencies.sort()
        p95_idx = int(len(latencies) * 0.95)
        p95 = latencies[p95_idx]
        assert p95 < 500, f"p95 latency = {p95:.1f}ms, expected < 500ms"


# ---------------------------------------------------------------------------
# Test: GC Accuracy
# ---------------------------------------------------------------------------


class TestGCAccuracy:
    """Verify GC correctly identifies stale entries without false positives."""

    def test_stale_removal_accuracy(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """GC should flag >90% of stale entries for archival."""
        monkeypatch.setenv("VT_MEMORY_GC", "1")
        monkeypatch.setenv("VT_MEMORY_DECAY", "1")

        _, stale_names = _generate_gc_entries(tmp_path, active_count=15, stale_count=15)
        mem = PersistentMemory(memory_dir=tmp_path)
        lifecycle = MemoryLifecycle(mem)
        # Lower capacity threshold so GC evaluates in dry_run mode
        monkeypatch.setattr(lifecycle, "MAX_MEMORY_COUNT", 10)

        actions = lifecycle.run_gc(dry_run=True)
        flagged_names = {a["name"] for a in actions}

        # Count stale entries correctly flagged
        correctly_flagged = sum(1 for n in stale_names if n in flagged_names)
        accuracy = correctly_flagged / len(stale_names) if stale_names else 0.0

        assert accuracy > 0.9, (
            f"Stale removal accuracy = {accuracy:.2%}, expected > 90%. "
            f"Flagged {correctly_flagged}/{len(stale_names)} stale entries."
        )

    def test_active_false_positive_rate(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """GC should never flag active (high-quality, recent) entries."""
        monkeypatch.setenv("VT_MEMORY_GC", "1")
        monkeypatch.setenv("VT_MEMORY_DECAY", "1")

        active_names, _ = _generate_gc_entries(tmp_path, active_count=15, stale_count=15)
        mem = PersistentMemory(memory_dir=tmp_path)
        lifecycle = MemoryLifecycle(mem)
        # Lower capacity threshold so GC evaluates in dry_run mode
        monkeypatch.setattr(lifecycle, "MAX_MEMORY_COUNT", 10)

        actions = lifecycle.run_gc(dry_run=True)
        flagged_names = {a["name"] for a in actions}

        # No active entry should be flagged
        false_positives = [n for n in active_names if n in flagged_names]
        assert len(false_positives) == 0, (
            f"Active entries incorrectly flagged: {false_positives}"
        )


# ---------------------------------------------------------------------------
# Test: No Regression
# ---------------------------------------------------------------------------


class TestNoRegression:
    """Ensure importance weighting does not significantly increase latency."""

    @pytest.mark.benchmark
    def test_latency_overhead_within_10_percent(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Importance-weighted search latency overhead should be < 10%."""
        _generate_entries(tmp_path, count=200, seed=789)
        rng = random.Random(99)
        queries = [rng.choice(_QUERY_CORPUS) for _ in range(30)]

        # Baseline: decay disabled (pure quality_score as importance)
        monkeypatch.setenv("VT_MEMORY_DECAY", "0")
        mem_baseline = PersistentMemory(memory_dir=tmp_path)
        baseline_times: list[float] = []
        for q in queries:
            start = time.perf_counter()
            mem_baseline.find_relevant(q)
            baseline_times.append(time.perf_counter() - start)
        baseline_median = sorted(baseline_times)[len(baseline_times) // 2]

        # With decay enabled
        monkeypatch.setenv("VT_MEMORY_DECAY", "1")
        mem_decay = PersistentMemory(memory_dir=tmp_path)
        decay_times: list[float] = []
        for q in queries:
            start = time.perf_counter()
            mem_decay.find_relevant(q)
            decay_times.append(time.perf_counter() - start)
        decay_median = sorted(decay_times)[len(decay_times) // 2]

        # Overhead check: decay should not add > 10% latency
        if baseline_median > 0:
            overhead = (decay_median - baseline_median) / baseline_median
        else:
            overhead = 0.0

        assert overhead < 0.10, (
            f"Latency overhead = {overhead:.1%}, expected < 10%. "
            f"Baseline p50={baseline_median*1000:.1f}ms, "
            f"Decay p50={decay_median*1000:.1f}ms"
        )

    def test_result_quality_no_degradation(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Decay-enabled search should return at least as many results."""
        _generate_entries(tmp_path, count=100, seed=321)
        query = "momentum breakout volume signal strategy"

        monkeypatch.setenv("VT_MEMORY_DECAY", "0")
        mem_baseline = PersistentMemory(memory_dir=tmp_path)
        baseline_count = len(mem_baseline.find_relevant(query))

        monkeypatch.setenv("VT_MEMORY_DECAY", "1")
        mem_decay = PersistentMemory(memory_dir=tmp_path)
        decay_count = len(mem_decay.find_relevant(query))

        # Decay model should not reduce number of results
        assert decay_count >= baseline_count, (
            f"Decay returned {decay_count} results vs baseline {baseline_count}"
        )
