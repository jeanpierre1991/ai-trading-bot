"""Tests for PortfolioManagerModule fill API."""

from __future__ import annotations

from decimal import Decimal

import pytest

from config.settings import Settings
from core.base_module import ModuleStatus
from core.types import Side, Symbol
from portfolio_manager.module import PortfolioManagerModule
from portfolio_manager.portfolio import Fill


@pytest.fixture
def module() -> PortfolioManagerModule:
    manager = PortfolioManagerModule(Settings())
    manager.initialize()
    return manager


def test_apply_fill_updates_managed_portfolio(module: PortfolioManagerModule) -> None:
    initial_cash = module.portfolio.cash
    module.apply_fill(
        Fill(
            symbol=Symbol("AAPL"),
            side=Side.BUY,
            quantity=Decimal("10"),
            price=Decimal("100.00"),
        )
    )

    assert module.portfolio.cash == initial_cash - Decimal("1000.00")
    assert module.portfolio.position_count == 1
    assert module.portfolio.positions["AAPL"].quantity == Decimal("10")


def test_apply_fill_before_initialize_raises() -> None:
    manager = PortfolioManagerModule(Settings())

    with pytest.raises(RuntimeError, match="not initialized"):
        manager.apply_fill(
            Fill(
                symbol=Symbol("AAPL"),
                side=Side.BUY,
                quantity=Decimal("1"),
                price=Decimal("100"),
            )
        )


def test_apply_fill_sell_via_module(module: PortfolioManagerModule) -> None:
    initial_cash = module.portfolio.cash
    module.apply_fill(
        Fill(symbol=Symbol("AAPL"), side=Side.BUY, quantity=Decimal("5"), price=Decimal("100"))
    )
    module.apply_fill(
        Fill(symbol=Symbol("AAPL"), side=Side.SELL, quantity=Decimal("5"), price=Decimal("110"))
    )

    assert module.portfolio.position_count == 0
    assert module.portfolio.cash == initial_cash + Decimal("50.00")


def test_health_check_remains_healthy_after_fill(module: PortfolioManagerModule) -> None:
    module.apply_fill(
        Fill(symbol=Symbol("AAPL"), side=Side.BUY, quantity=Decimal("1"), price=Decimal("100"))
    )
    health = module.health_check()

    assert health.is_healthy is True
    assert health.status == ModuleStatus.READY
    assert health.details["position_count"] == 1
