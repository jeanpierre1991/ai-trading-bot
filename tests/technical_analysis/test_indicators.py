"""Tests for existing indicator implementations."""

from __future__ import annotations

from decimal import Decimal

import pytest

from technical_analysis.indicators import calculate_ema, calculate_rsi, calculate_sma


def test_calculate_sma_returns_none_prefix_until_period_met() -> None:
    prices = [Decimal("10"), Decimal("20"), Decimal("30")]

    result = calculate_sma(prices, 2)

    assert result == [None, Decimal("15.0000"), Decimal("25.0000")]


def test_calculate_sma_rejects_non_positive_period() -> None:
    with pytest.raises(ValueError, match="period must be positive"):
        calculate_sma([Decimal("10")], 0)


def test_calculate_sma_returns_all_none_when_insufficient_data() -> None:
    prices = [Decimal("10")]

    assert calculate_sma(prices, 5) == [None]


def test_calculate_ema_matches_seed_and_next_value() -> None:
    prices = [Decimal("10"), Decimal("20"), Decimal("30"), Decimal("40")]

    result = calculate_ema(prices, 2)

    assert result[1] == Decimal("15.0000")
    assert result[2] == Decimal("25.0000")
    assert result[3] == Decimal("35.0000")


def test_calculate_ema_rejects_non_positive_period() -> None:
    with pytest.raises(ValueError, match="period must be positive"):
        calculate_ema([Decimal("10")], -1)


def test_calculate_rsi_returns_none_until_enough_prices() -> None:
    prices = [Decimal("10"), Decimal("11")]

    assert calculate_rsi(prices, 14) == [None, None]


def test_calculate_rsi_returns_100_when_average_loss_is_zero() -> None:
    prices = [Decimal(str(100 + i)) for i in range(16)]

    result = calculate_rsi(prices, 14)

    assert result[-1] == Decimal("100")


def test_calculate_rsi_rejects_non_positive_period() -> None:
    with pytest.raises(ValueError, match="period must be positive"):
        calculate_rsi([Decimal("10"), Decimal("11")], 0)
