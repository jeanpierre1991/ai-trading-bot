"""Tests for Milestone 5 Trading Runtime structure."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from decimal import Decimal

import pytest

from config.settings import Settings
from core.types import TradingMode
from risk_manager.basic import BasicRiskManager
from risk_manager.models import RiskEvaluation
from runtime.base import TradingRuntime
from runtime.context import RuntimeContext
from runtime.models import PipelineResult
from runtime.trading_runtime import BasicTradingRuntime


def test_runtime_context_defaults() -> None:
    context = RuntimeContext(symbol="AAPL")

    assert context.symbol == "AAPL"
    assert context.mode is TradingMode.PAPER
    assert context.strategy_name is None
    assert context.bar_limit == 100
    assert context.portfolio_value is None
    assert context.daily_pnl_pct is None


def test_runtime_context_is_immutable() -> None:
    context = RuntimeContext(symbol="MSFT", mode=TradingMode.BACKTEST, daily_pnl_pct=Decimal("0"))

    with pytest.raises(FrozenInstanceError):
        context.symbol = "AAPL"  # type: ignore[misc]


def test_trading_runtime_cannot_be_instantiated() -> None:
    with pytest.raises(TypeError):
        TradingRuntime()  # type: ignore[misc]


def test_basic_trading_runtime_stores_dependencies() -> None:
    settings = Settings()
    risk_manager = BasicRiskManager(settings)
    market_data = object()
    strategy_engine = object()
    portfolio = object()

    runtime = BasicTradingRuntime(
        settings=settings,
        market_data=market_data,
        strategy_engine=strategy_engine,
        risk_manager=risk_manager,
        portfolio=portfolio,
    )

    assert runtime.settings is settings
    assert runtime.market_data is market_data
    assert runtime.strategy_engine is strategy_engine
    assert runtime.risk_manager is risk_manager
    assert runtime.portfolio is portfolio
    assert runtime.executor is None
    assert runtime.order_manager is None
    assert runtime.alert_notifier is None


def test_pipeline_result_supports_risk_evaluation() -> None:
    evaluation = RiskEvaluation(
        approved=True,
        reason="ok",
        position_size=Decimal("1000"),
        stop_loss=Decimal("95"),
        take_profit=Decimal("110"),
    )
    result = PipelineResult(
        success=True,
        stage_reached="risk",
        risk_evaluation=evaluation,
    )

    assert result.risk_evaluation is evaluation
    assert result.risk_assessment is None
