"""NSE sectoral indices (Nifty Bank, Nifty IT, ...) are benchable Alpha Zoo universes.

Mirrors ``test_alpha_bench_universe_metadata.py``'s style: monkeypatch the
network-touching internals (constituent CSV fetch, per-symbol OHLCV fetch) and
assert on the panel shape / ``_meta`` disclosure, rather than hitting the network.
"""

from __future__ import annotations

import pandas as pd
import pytest

from src.tools import alpha_bench_tool as tool
from trade_integrations.dataflows.index_research import constituents as constituents_mod
from trade_integrations.dataflows.index_research.alpha_bridge import india_ohlcv as india_ohlcv_mod
from trade_integrations.dataflows.index_research.models import ConstituentRow


def _ohlcv_frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "date": ["2024-01-02", "2024-01-03"],
            "open": [100.0, 101.0],
            "high": [102.0, 103.0],
            "low": [99.0, 100.0],
            "close": [101.0, 102.0],
            "volume": [1000.0, 1100.0],
        }
    )


def test_all_sector_indices_registered_with_equity_in_market() -> None:
    """Every NSE sectoral index resolves in UNIVERSE_REGISTRY as equity_in."""
    for index_id in tool._NSE_SECTOR_INDICES:
        assert index_id in tool.UNIVERSE_REGISTRY
        spec = tool.UNIVERSE_REGISTRY[index_id]
        assert spec.market == "equity_in"
        assert spec.id == index_id
        assert spec.survivorship_bias is True


@pytest.mark.parametrize("index_id", sorted(tool._NSE_SECTOR_INDICES))
def test_sector_panel_loader_returns_nonempty_panel(
    monkeypatch: pytest.MonkeyPatch, index_id: str
) -> None:
    """Each registered sector universe's panel_loader is callable and returns data."""
    monkeypatch.setattr(
        constituents_mod,
        "load_nse_sector_index_constituents",
        lambda _method_name: [
            ConstituentRow(symbol="FAKESYM1", name="FAKESYM1", weight=0.5, sector="", market="IN"),
            ConstituentRow(symbol="FAKESYM2", name="FAKESYM2", weight=0.5, sector="", market="IN"),
        ],
    )
    monkeypatch.setattr(
        india_ohlcv_mod,
        "load_symbol_ohlcv",
        lambda _symbol, **_kwargs: _ohlcv_frame(),
    )

    panel_loader = tool.UNIVERSE_REGISTRY[index_id].panel_loader
    panel = panel_loader("2024-01-01", "2024-01-31")

    assert "close" in panel
    assert not panel["close"].empty
    assert set(panel["close"].columns) == {"FAKESYM1", "FAKESYM2"}
    assert panel["_meta"] == {
        "universe": index_id,
        "survivorship_bias": True,
        "constituent_source": "niftyindices_constituents",
        "constituent_count": 2,
    }


def test_sector_panel_raises_when_every_constituent_source_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The constituent chain answers [] when every vendor failed: the loader names that cause."""
    monkeypatch.setattr(constituents_mod, "load_nse_sector_index_constituents", lambda _method_name: [])

    with pytest.raises(RuntimeError, match="no constituents for niftybank_equity_list"):
        tool._load_nse_sector_panel("niftybank", "niftybank_equity_list", "2024-01-01", "2024-01-31")


def test_sector_panel_propagates_internal_constituent_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """A bug in the constituent load (here a TypeError) is not turned into an empty roster."""

    def broken(_method_name):
        raise TypeError("ConstituentRow.__init__() missing 1 required argument: 'market'")

    monkeypatch.setattr(constituents_mod, "load_nse_sector_index_constituents", broken)

    with pytest.raises(TypeError):
        tool._load_nse_sector_panel("niftybank", "niftybank_equity_list", "2024-01-01", "2024-01-31")


def test_sector_panel_drops_vendor_failed_symbol_but_propagates_bug(monkeypatch: pytest.MonkeyPatch) -> None:
    """A vendor error drops that one symbol (WARNING); a programming error propagates."""
    monkeypatch.setattr(
        constituents_mod,
        "load_nse_sector_index_constituents",
        lambda _method_name: [
            ConstituentRow(symbol="FAKESYM1", name="FAKESYM1", weight=0.5, sector="", market="IN"),
            ConstituentRow(symbol="FAKESYM2", name="FAKESYM2", weight=0.5, sector="", market="IN"),
        ],
    )

    def vendor_down(symbol, **_kwargs):
        if symbol == "FAKESYM2":
            raise RuntimeError("yfinance unreachable")
        return _ohlcv_frame()

    monkeypatch.setattr(india_ohlcv_mod, "load_symbol_ohlcv", vendor_down)
    panel = tool._load_nse_sector_panel("niftybank", "niftybank_equity_list", "2024-01-01", "2024-01-31")
    assert set(panel["close"].columns) == {"FAKESYM1"}

    def bug(_symbol, **_kwargs):
        raise AttributeError("'NoneType' object has no attribute 'empty'")

    monkeypatch.setattr(india_ohlcv_mod, "load_symbol_ohlcv", bug)
    with pytest.raises(AttributeError):
        tool._load_nse_sector_panel("niftybank", "niftybank_equity_list", "2024-01-01", "2024-01-31")
