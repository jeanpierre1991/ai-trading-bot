"""Tests for network-free backtest bar IO helpers (M10.4)."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from backtesting.bars_io import load_bars_from_csv, make_synthetic_bars
from core.exceptions import ConfigurationError
from core.types import TimeFrame
from market_data.historical_provider import MAX_HISTORICAL_BARS


def test_make_synthetic_bars_bounded_and_deterministic() -> None:
    first = make_synthetic_bars(5, symbol="AAPL", timeframe=TimeFrame.H1)
    second = make_synthetic_bars(5, symbol="AAPL", timeframe=TimeFrame.H1)
    assert len(first) == 5
    assert first == second
    assert first[0].close == Decimal("100")
    assert first[-1].close == Decimal("104")


def test_make_synthetic_bars_rejects_oversized() -> None:
    with pytest.raises(ConfigurationError, match="MAX_HISTORICAL_BARS"):
        make_synthetic_bars(
            MAX_HISTORICAL_BARS + 1,
            symbol="AAPL",
            timeframe="1h",
        )


def test_load_bars_from_csv_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "ok.csv"
    path.write_text(
        "timestamp,open,high,low,close,volume\n"
        "2026-01-01T00:00:00Z,10,11,9,10.5,100\n"
        "2026-01-01T01:00:00+00:00,10.5,12,10,11,200\n",
        encoding="utf-8",
    )
    bars = load_bars_from_csv(path, symbol="MSFT", timeframe=TimeFrame.H1)
    assert len(bars) == 2
    assert bars[0].close == Decimal("10.5")
    assert bars[1]["symbol"] == "MSFT"


def test_load_bars_from_csv_missing_file(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match="not found"):
        load_bars_from_csv(tmp_path / "nope.csv", symbol="AAPL", timeframe="1h")
