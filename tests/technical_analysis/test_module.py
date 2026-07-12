"""Tests for TechnicalAnalysisModule integration."""

from __future__ import annotations

import inspect
from decimal import Decimal
from typing import get_type_hints

import pytest

from config.settings import Settings
from core.base_module import ModuleStatus
from technical_analysis.module import TechnicalAnalysisModule


@pytest.fixture
def module() -> TechnicalAnalysisModule:
    return TechnicalAnalysisModule(Settings())


def test_analyze_public_signature_unchanged() -> None:
    signature = inspect.signature(TechnicalAnalysisModule.analyze)
    hints = get_type_hints(TechnicalAnalysisModule.analyze)

    assert list(signature.parameters) == ["self", "closes"]
    assert hints["closes"] == list[Decimal]
    assert hints["return"] == dict[str, list[Decimal | None]]


def test_analyze_returns_expected_keys(module: TechnicalAnalysisModule) -> None:
    results = module.analyze([Decimal("100"), Decimal("101"), Decimal("102")])

    assert set(results) == {"sma_20", "ema_20", "rsi_14"}


def test_analyze_empty_closes(module: TechnicalAnalysisModule) -> None:
    results = module.analyze([])

    assert results == {"sma_20": [], "ema_20": [], "rsi_14": []}


@pytest.mark.parametrize(
    "closes",
    [
        [],
        [Decimal("100")],
        [Decimal("100"), Decimal("101")],
        [Decimal(str(100 + i * 0.5)) for i in range(30)],
        [Decimal(str(100 + i * 0.25)) for i in range(100)],
    ],
)
def test_analyze_matches_legacy_implementation(
    module: TechnicalAnalysisModule,
    closes: list[Decimal],
) -> None:
    legacy = module._analyze_legacy(closes)
    current = module.analyze(closes)

    assert current == legacy


def test_analyze_short_series_returns_none_prefix(module: TechnicalAnalysisModule) -> None:
    results = module.analyze([Decimal("10"), Decimal("11")])

    assert results["sma_20"] == [None, None]
    assert results["ema_20"] == [None, None]
    assert results["rsi_14"] == [None, None]


def test_health_check_remains_healthy(module: TechnicalAnalysisModule) -> None:
    module.initialize()
    health = module.health_check()

    assert health.is_healthy is True
    assert health.status == ModuleStatus.READY
    assert health.message == "Indicators computed successfully"
    assert health.details["indicators"] == ("sma", "ema", "rsi")
