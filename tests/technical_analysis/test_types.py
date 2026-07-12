"""Tests for technical analysis domain types."""

from __future__ import annotations

from decimal import Decimal

import pytest

from technical_analysis.types import IndicatorRequest, IndicatorSnapshot, IndicatorSource


def test_indicator_source_values() -> None:
    assert IndicatorSource.CLOSE.value == "close"
    assert IndicatorSource.HIGH.value == "high"
    assert IndicatorSource.LOW.value == "low"
    assert IndicatorSource.VOLUME.value == "volume"


def test_indicator_request_normalizes_name() -> None:
    request = IndicatorRequest(name=" SMA ", params={"period": 10})

    assert request.name == "sma"
    assert request.params == {"period": 10}
    assert request.source is IndicatorSource.CLOSE


def test_indicator_request_rejects_empty_name() -> None:
    with pytest.raises(ValueError, match="indicator name must not be empty"):
        IndicatorRequest(name="   ")


def test_indicator_request_rejects_non_dict_params() -> None:
    with pytest.raises(ValueError, match="params must be a dictionary"):
        IndicatorRequest(name="sma", params="invalid")  # type: ignore[arg-type]


def test_indicator_snapshot_stores_values_and_series() -> None:
    snapshot = IndicatorSnapshot(
        symbol="AAPL",
        timeframe="1h",
        timestamp=None,
        values={"sma_20": Decimal("100.00")},
        series={"sma_20": [None, Decimal("100.00")]},
    )

    assert snapshot.symbol == "AAPL"
    assert snapshot.timeframe == "1h"
    assert snapshot.values["sma_20"] == Decimal("100.00")
    assert snapshot.series["sma_20"] == [None, Decimal("100.00")]
