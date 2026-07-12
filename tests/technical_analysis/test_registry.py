"""Tests for indicator registry."""

from __future__ import annotations

import pytest

from technical_analysis.indicators import calculate_ema, calculate_rsi, calculate_sma
from technical_analysis.registry import (
    INDICATOR_REGISTRY,
    build_result_key,
    get_indicator,
    list_indicators,
)


def test_list_indicators_returns_supported_names() -> None:
    assert list_indicators() == ("ema", "rsi", "sma")


def test_get_indicator_returns_sma_definition() -> None:
    definition = get_indicator("SMA")

    assert definition.name == "sma"
    assert definition.function is calculate_sma
    assert definition.default_params == {"period": 20}


def test_get_indicator_returns_ema_definition() -> None:
    definition = get_indicator("ema")

    assert definition.function is calculate_ema
    assert definition.default_params == {"period": 20}


def test_get_indicator_returns_rsi_definition() -> None:
    definition = get_indicator("rsi")

    assert definition.function is calculate_rsi
    assert definition.default_params == {"period": 14}


def test_get_indicator_raises_for_unknown_indicator() -> None:
    with pytest.raises(ValueError, match="Unknown indicator: 'macd'"):
        get_indicator("macd")


def test_build_result_key_includes_period() -> None:
    assert build_result_key("sma", {"period": 20}) == "sma_20"


def test_build_result_key_without_period() -> None:
    assert build_result_key("sma", {}) == "sma"


def test_registry_contains_only_expected_indicators() -> None:
    assert set(INDICATOR_REGISTRY) == {"sma", "ema", "rsi"}
