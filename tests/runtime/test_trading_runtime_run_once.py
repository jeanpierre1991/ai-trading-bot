"""Tests for BasicTradingRuntime.run_once decision cycle."""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from config.settings import Settings
from core.types import OrderType, PositionId, Side, SignalAction, Symbol
from portfolio_manager.portfolio import Portfolio, Position
from risk_manager.basic import BasicRiskManager
from risk_manager.models import RiskEvaluation
from runtime.context import RuntimeContext
from runtime.models import PipelineResult, TradeIntent
from runtime.trading_runtime import BasicTradingRuntime
from strategy_engine.signal import StrategySignal


def _signal(
    *,
    action: SignalAction,
    price: Decimal = Decimal("100"),
    symbol: str = "AAPL",
    confidence: float = 0.8,
    strategy_name: str = "ema_crossover",
) -> StrategySignal:
    return StrategySignal(
        symbol=symbol,
        action=action,
        confidence=confidence,
        strategy_name=strategy_name,
        price=price,
    )


def _long_position(
    *,
    symbol: str = "AAPL",
    quantity: Decimal = Decimal("100"),
    price: Decimal = Decimal("100"),
) -> Position:
    return Position(
        position_id=PositionId(f"pos-{symbol}"),
        symbol=Symbol(symbol),
        side=Side.BUY,
        quantity=quantity,
        entry_price=price,
        current_price=price,
    )


def _runtime(
    *,
    signal: StrategySignal,
    portfolio: Portfolio | None = None,
    max_position_size_pct: Decimal = Decimal("0.05"),
    bars: list | None = None,
) -> tuple[BasicTradingRuntime, MagicMock, MagicMock, Portfolio]:
    settings = Settings(
        max_position_size_pct=max_position_size_pct,
        market_data_freshness_enabled=False,
    )
    portfolio = portfolio or Portfolio(cash=Decimal("100000"))
    market_data = MagicMock()
    market_data.get_bars.return_value = bars if bars is not None else [object()]
    strategy_engine = MagicMock()
    strategy_engine.evaluate.return_value = signal
    runtime = BasicTradingRuntime(
        settings=settings,
        market_data=market_data,
        strategy_engine=strategy_engine,
        risk_manager=BasicRiskManager(settings),
        portfolio=portfolio,
    )
    return runtime, market_data, strategy_engine, portfolio


def test_hold_signal_ends_cleanly_without_trade_intent() -> None:
    runtime, market_data, strategy_engine, portfolio = _runtime(
        signal=_signal(action=SignalAction.HOLD, price=Decimal("0")),
    )
    before = portfolio.summary()

    result = runtime.run_once(RuntimeContext(symbol="AAPL", daily_pnl_pct=Decimal("0")))

    assert isinstance(result, PipelineResult)
    assert result.success is True
    assert result.stage_reached == "risk"
    assert result.signal is not None
    assert result.signal.action is SignalAction.HOLD
    assert result.intent is None
    assert result.order is None
    assert result.execution is None
    assert result.aborted_reason is None
    assert isinstance(result.risk_evaluation, RiskEvaluation)
    assert result.portfolio_snapshot == before
    assert portfolio.summary() == before
    market_data.get_bars.assert_called_once_with(symbol="AAPL", limit=100)
    strategy_engine.evaluate.assert_called_once()


def test_buy_approved_creates_trade_intent() -> None:
    runtime, market_data, strategy_engine, portfolio = _runtime(
        signal=_signal(action=SignalAction.BUY, price=Decimal("100")),
    )
    before = portfolio.summary()

    result = runtime.run_once(RuntimeContext(symbol="AAPL", daily_pnl_pct=Decimal("0")))

    assert result.success is True
    assert result.stage_reached == "portfolio"
    assert result.aborted_reason is None
    assert result.signal is not None
    assert result.signal.action is SignalAction.BUY
    assert isinstance(result.intent, TradeIntent)
    assert result.intent.symbol == Symbol("AAPL")
    assert result.intent.side is Side.BUY
    assert result.intent.order_type is OrderType.MARKET
    assert result.intent.limit_price is None
    assert result.intent.quantity == Decimal("50.0000")  # 5000 / 100
    assert result.intent.max_position_value == Decimal("5000.00")
    assert result.intent.strategy_name == "ema_crossover"
    assert result.intent.signal_confidence == 0.8
    assert result.intent.reason == result.risk_evaluation.reason
    assert result.risk_evaluation is not None
    assert result.risk_evaluation.approved is True
    assert result.order is None
    assert result.execution is None
    assert portfolio.summary() == before
    assert result.portfolio_snapshot == before
    market_data.get_bars.assert_called_once()
    strategy_engine.evaluate.assert_called_once()


@pytest.mark.parametrize(
    ("action", "expected_side"),
    [
        (SignalAction.SELL, Side.SELL),
        (SignalAction.CLOSE, Side.SELL),
    ],
)
def test_sell_or_close_approved_creates_trade_intent(
    action: SignalAction,
    expected_side: Side,
) -> None:
    portfolio = Portfolio(cash=Decimal("50000"))
    portfolio.positions["MSFT"] = _long_position(
        symbol="MSFT",
        quantity=Decimal("100"),
        price=Decimal("200"),
    )
    # total_value = 50000 + 20000 = 70000; 5% = 3500 → SELL qty 17.5
    runtime, _, _, _ = _runtime(
        signal=_signal(action=action, price=Decimal("200"), symbol="MSFT"),
        portfolio=portfolio,
    )
    before = portfolio.summary()

    result = runtime.run_once(RuntimeContext(symbol="MSFT", daily_pnl_pct=Decimal("0")))

    assert result.success is True
    assert result.intent is not None
    assert result.intent.side is expected_side
    assert result.intent.symbol == Symbol("MSFT")
    if action is SignalAction.SELL:
        assert result.intent.quantity == Decimal("17.5000")
        assert result.intent.max_position_value == Decimal("3500.00")
    else:
        assert result.intent.quantity == Decimal("100")
        assert result.intent.max_position_value == Decimal("20000.00")
    assert result.order is None
    assert portfolio.summary() == before


def test_risk_rejection_preserves_reason_and_skips_intent() -> None:
    portfolio = Portfolio(cash=Decimal("0"))
    runtime, market_data, strategy_engine, _ = _runtime(
        signal=_signal(action=SignalAction.BUY, price=Decimal("100")),
        portfolio=portfolio,
    )
    before = portfolio.summary()

    result = runtime.run_once(RuntimeContext(symbol="AAPL", daily_pnl_pct=Decimal("0")))

    assert result.success is False
    assert result.stage_reached == "risk"
    assert result.aborted_reason == "Portfolio value must be positive"
    assert result.risk_evaluation is not None
    assert result.risk_evaluation.approved is False
    assert result.risk_evaluation.reason == result.aborted_reason
    assert result.intent is None
    assert result.signal is not None
    assert result.signal.action is SignalAction.BUY
    assert portfolio.summary() == before
    assert result.portfolio_snapshot == before
    market_data.get_bars.assert_called_once()
    strategy_engine.evaluate.assert_called_once()


def test_provider_and_strategy_called_once() -> None:
    runtime, market_data, strategy_engine, _ = _runtime(
        signal=_signal(action=SignalAction.BUY),
    )

    runtime.run_once(RuntimeContext(symbol="AAPL", daily_pnl_pct=Decimal("0")))

    market_data.get_bars.assert_called_once_with(symbol="AAPL", limit=100)
    strategy_engine.evaluate.assert_called_once()
    args, kwargs = strategy_engine.evaluate.call_args
    assert len(args) == 1
    assert kwargs["symbol"] == "AAPL"
    assert kwargs["strategy_name"] is None


def test_empty_bars_returns_controlled_pipeline_result() -> None:
    runtime, market_data, strategy_engine, portfolio = _runtime(
        signal=_signal(action=SignalAction.BUY),
        bars=[],
    )
    before = portfolio.summary()

    result = runtime.run_once(RuntimeContext(symbol="AAPL", daily_pnl_pct=Decimal("0")))

    assert result.success is False
    assert result.stage_reached == "market_data"
    assert result.aborted_reason == "No market bars available"
    assert result.signal is None
    assert result.intent is None
    assert result.portfolio_snapshot == before
    market_data.get_bars.assert_called_once()
    strategy_engine.evaluate.assert_not_called()


def test_strategy_value_error_returns_controlled_pipeline_result() -> None:
    runtime, market_data, strategy_engine, _ = _runtime(
        signal=_signal(action=SignalAction.BUY),
    )
    strategy_engine.evaluate.side_effect = ValueError("Unknown strategy: 'nope'")

    result = runtime.run_once(
        RuntimeContext(symbol="AAPL", strategy_name="nope", daily_pnl_pct=Decimal("0")),
    )

    assert result.success is False
    assert result.stage_reached == "strategy"
    assert result.aborted_reason == "Unknown strategy: 'nope'"
    assert result.intent is None
    market_data.get_bars.assert_called_once()
    strategy_engine.evaluate.assert_called_once()


def test_portfolio_not_modified_on_approved_path() -> None:
    portfolio = Portfolio(cash=Decimal("100000"))
    runtime, _, _, _ = _runtime(
        signal=_signal(action=SignalAction.BUY, price=Decimal("50")),
        portfolio=portfolio,
    )
    cash_before = portfolio.cash
    positions_before = dict(portfolio.positions)

    result = runtime.run_once(RuntimeContext(symbol="AAPL", daily_pnl_pct=Decimal("0")))

    assert result.success is True
    assert result.intent is not None
    assert portfolio.cash == cash_before
    assert portfolio.positions == positions_before
    assert portfolio.summary() == result.portfolio_snapshot


def test_pipeline_result_always_valid_shape() -> None:
    cases = [
        _signal(action=SignalAction.HOLD, price=Decimal("0")),
        _signal(action=SignalAction.BUY, price=Decimal("100")),
    ]
    for signal in cases:
        runtime, _, _, _ = _runtime(signal=signal)
        result = runtime.run_once(RuntimeContext(symbol="AAPL", daily_pnl_pct=Decimal("0")))
        assert isinstance(result, PipelineResult)
        assert isinstance(result.success, bool)
        assert isinstance(result.stage_reached, str)
        assert result.stage_reached
        assert result.portfolio_snapshot is not None
        assert "cash" in result.portfolio_snapshot
        assert "total_value" in result.portfolio_snapshot


def test_uses_portfolio_total_value_when_context_value_missing() -> None:
    portfolio = Portfolio(cash=Decimal("20000"))
    runtime, _, _, _ = _runtime(
        signal=_signal(action=SignalAction.BUY, price=Decimal("100")),
        portfolio=portfolio,
        max_position_size_pct=Decimal("0.10"),
    )

    result = runtime.run_once(RuntimeContext(symbol="AAPL", daily_pnl_pct=Decimal("0")))

    assert result.success is True
    assert result.intent is not None
    assert result.intent.max_position_value == Decimal("2000.00")
    assert result.intent.quantity == Decimal("20.0000")
