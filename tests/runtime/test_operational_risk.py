"""M8.2 operational risk limits on the Runtime path."""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock

from config.settings import Settings
from core.types import PositionId, Side, SignalAction, Symbol
from portfolio_manager.portfolio import Portfolio, Position
from risk_manager.basic import (
    DEFAULT_RISK_REWARD_RATIO,
    DEFAULT_STOP_LOSS_PCT,
    BasicRiskManager,
)
from runtime.broker_executor import BrokerOrderExecutor
from runtime.context import RuntimeContext
from runtime.dry_run import DryRunExecutor
from runtime.trading_runtime import BasicTradingRuntime
from strategy_engine.signal import StrategySignal


def _signal(
    *,
    action: SignalAction = SignalAction.BUY,
    price: Decimal = Decimal("100"),
    symbol: str = "AAPL",
) -> StrategySignal:
    return StrategySignal(
        symbol=symbol,
        action=action,
        confidence=0.8,
        strategy_name="ema_crossover",
        price=price,
    )


def _long_position(
    *,
    symbol: str = "AAPL",
    quantity: Decimal = Decimal("10"),
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
    portfolio: Portfolio,
    settings: Settings,
    executor: DryRunExecutor | BrokerOrderExecutor | MagicMock | None = None,
) -> tuple[BasicTradingRuntime, MagicMock | DryRunExecutor | BrokerOrderExecutor | None]:
    if executor is None:
        executor = MagicMock()
        executor.execute = MagicMock(
            side_effect=AssertionError("executor must not be called"),
        )
    market_data = MagicMock()
    market_data.get_bars.return_value = [object()]
    strategy_engine = MagicMock()
    strategy_engine.evaluate.return_value = signal
    runtime = BasicTradingRuntime(
        settings=settings,
        market_data=market_data,
        strategy_engine=strategy_engine,
        risk_manager=BasicRiskManager(settings),
        portfolio=portfolio,
        executor=executor,
    )
    return runtime, executor


def test_buy_allowed_below_max_open_positions() -> None:
    settings = Settings(
        trading_mode="paper",
        max_position_size_pct=Decimal("0.05"),
        max_open_positions=2,
        max_daily_loss_pct=Decimal("0.02"),
        market_data_freshness_enabled=False,
    )
    portfolio = Portfolio(cash=Decimal("100000"))
    portfolio.positions["MSFT"] = _long_position(symbol="MSFT")
    executor = DryRunExecutor()
    runtime, _ = _runtime(
        signal=_signal(action=SignalAction.BUY),
        portfolio=portfolio,
        settings=settings,
        executor=executor,
    )

    result = runtime.run_once(
        RuntimeContext(symbol="AAPL", daily_pnl_pct=Decimal("0")),
    )

    assert result.success is True
    assert result.stage_reached == "portfolio"
    assert result.execution is not None
    assert portfolio.position_count == 2


def test_buy_rejected_when_max_open_positions_reached() -> None:
    settings = Settings(
        trading_mode="paper",
        max_position_size_pct=Decimal("0.05"),
        max_open_positions=1,
        max_daily_loss_pct=Decimal("0.02"),
        market_data_freshness_enabled=False,
    )
    portfolio = Portfolio(cash=Decimal("100000"))
    portfolio.positions["MSFT"] = _long_position(symbol="MSFT")
    before = portfolio.summary()
    executor = MagicMock()
    runtime, executor = _runtime(
        signal=_signal(action=SignalAction.BUY, symbol="AAPL"),
        portfolio=portfolio,
        settings=settings,
        executor=executor,
    )

    result = runtime.run_once(
        RuntimeContext(symbol="AAPL", daily_pnl_pct=Decimal("0")),
    )

    assert result.success is False
    assert result.stage_reached == "risk"
    assert result.aborted_reason is not None
    assert "max_open_positions reached" in result.aborted_reason
    assert result.execution is None
    assert result.intent is None
    executor.execute.assert_not_called()
    assert portfolio.summary() == before


def test_sell_not_blocked_only_by_max_open_positions() -> None:
    settings = Settings(
        trading_mode="paper",
        max_position_size_pct=Decimal("0.05"),
        max_open_positions=1,
        max_daily_loss_pct=Decimal("0.02"),
        market_data_freshness_enabled=False,
    )
    portfolio = Portfolio(cash=Decimal("100000"))
    portfolio.positions["AAPL"] = _long_position(quantity=Decimal("100"))
    cash_before = portfolio.cash
    qty_before = portfolio.positions["AAPL"].quantity
    runtime, _ = _runtime(
        signal=_signal(action=SignalAction.SELL),
        portfolio=portfolio,
        settings=settings,
        executor=DryRunExecutor(),
    )

    result = runtime.run_once(
        RuntimeContext(symbol="AAPL", daily_pnl_pct=Decimal("0")),
    )

    assert result.success is True
    assert result.stage_reached == "portfolio"
    assert result.execution is not None
    assert portfolio.cash > cash_before
    assert portfolio.positions["AAPL"].quantity < qty_before


def test_buy_allowed_when_daily_pnl_below_loss_limit() -> None:
    settings = Settings(
        trading_mode="paper",
        max_position_size_pct=Decimal("0.05"),
        max_open_positions=10,
        max_daily_loss_pct=Decimal("0.02"),
        market_data_freshness_enabled=False,
    )
    portfolio = Portfolio(cash=Decimal("100000"))
    runtime, _ = _runtime(
        signal=_signal(action=SignalAction.BUY),
        portfolio=portfolio,
        settings=settings,
        executor=DryRunExecutor(),
    )

    result = runtime.run_once(
        RuntimeContext(symbol="AAPL", daily_pnl_pct=Decimal("-0.019")),
    )

    assert result.success is True
    assert result.stage_reached == "portfolio"
    assert result.execution is not None


def test_buy_allowed_when_daily_pnl_is_positive_gain() -> None:
    """Positive daily PnL must not trip max_daily_loss_pct."""
    settings = Settings(
        trading_mode="paper",
        max_position_size_pct=Decimal("0.05"),
        max_open_positions=10,
        max_daily_loss_pct=Decimal("0.02"),
        market_data_freshness_enabled=False,
    )
    portfolio = Portfolio(cash=Decimal("100000"))
    cash_before = portfolio.cash
    runtime, _ = _runtime(
        signal=_signal(action=SignalAction.BUY),
        portfolio=portfolio,
        settings=settings,
        executor=DryRunExecutor(),
    )

    result = runtime.run_once(
        RuntimeContext(symbol="AAPL", daily_pnl_pct=Decimal("0.05")),
    )

    assert result.success is True
    assert result.stage_reached == "portfolio"
    assert result.execution is not None
    assert result.aborted_reason is None
    assert "max_daily_loss_pct" not in (result.aborted_reason or "")
    assert portfolio.cash < cash_before


def test_buy_rejected_when_daily_loss_equals_limit() -> None:
    settings = Settings(
        trading_mode="paper",
        max_position_size_pct=Decimal("0.05"),
        max_open_positions=10,
        max_daily_loss_pct=Decimal("0.02"),
        market_data_freshness_enabled=False,
    )
    portfolio = Portfolio(cash=Decimal("100000"))
    before = portfolio.summary()
    executor = MagicMock()
    runtime, executor = _runtime(
        signal=_signal(action=SignalAction.BUY),
        portfolio=portfolio,
        settings=settings,
        executor=executor,
    )

    result = runtime.run_once(
        RuntimeContext(symbol="AAPL", daily_pnl_pct=Decimal("-0.02")),
    )

    assert result.success is False
    assert result.stage_reached == "risk"
    assert result.aborted_reason is not None
    assert "max_daily_loss_pct exceeded" in result.aborted_reason
    executor.execute.assert_not_called()
    assert portfolio.summary() == before


def test_buy_rejected_when_daily_loss_exceeds_limit() -> None:
    settings = Settings(
        trading_mode="paper",
        max_position_size_pct=Decimal("0.05"),
        max_open_positions=10,
        max_daily_loss_pct=Decimal("0.02"),
        market_data_freshness_enabled=False,
    )
    portfolio = Portfolio(cash=Decimal("100000"))
    before = portfolio.summary()
    executor = MagicMock()
    runtime, executor = _runtime(
        signal=_signal(action=SignalAction.BUY),
        portfolio=portfolio,
        settings=settings,
        executor=executor,
    )

    result = runtime.run_once(
        RuntimeContext(symbol="AAPL", daily_pnl_pct=Decimal("-0.05")),
    )

    assert result.success is False
    assert result.stage_reached == "risk"
    assert result.aborted_reason is not None
    assert "max_daily_loss_pct exceeded" in result.aborted_reason
    executor.execute.assert_not_called()
    assert portfolio.summary() == before


def test_actionable_trade_fail_closed_when_daily_pnl_pct_missing() -> None:
    settings = Settings(
        trading_mode="paper",
        max_position_size_pct=Decimal("0.05"),
        max_open_positions=10,
        max_daily_loss_pct=Decimal("0.02"),
        market_data_freshness_enabled=False,
    )
    portfolio = Portfolio(cash=Decimal("100000"))
    before = portfolio.summary()
    executor = MagicMock()
    runtime, executor = _runtime(
        signal=_signal(action=SignalAction.BUY),
        portfolio=portfolio,
        settings=settings,
        executor=executor,
    )

    result = runtime.run_once(RuntimeContext(symbol="AAPL"))

    assert result.success is False
    assert result.stage_reached == "risk"
    assert result.aborted_reason is not None
    assert "daily_pnl_pct is required" in result.aborted_reason
    assert "fail-closed" in result.aborted_reason
    executor.execute.assert_not_called()
    assert portfolio.summary() == before


def test_hold_not_blocked_by_missing_daily_pnl_pct() -> None:
    settings = Settings(
        trading_mode="paper",
        max_position_size_pct=Decimal("0.05"),
        max_daily_loss_pct=Decimal("0.02"),
        market_data_freshness_enabled=False,
    )
    portfolio = Portfolio(cash=Decimal("100000"))
    before = portfolio.summary()
    executor = MagicMock()
    runtime, executor = _runtime(
        signal=_signal(action=SignalAction.HOLD, price=Decimal("0")),
        portfolio=portfolio,
        settings=settings,
        executor=executor,
    )

    result = runtime.run_once(RuntimeContext(symbol="AAPL"))

    assert result.success is True
    assert result.stage_reached == "risk"
    assert result.execution is None
    executor.execute.assert_not_called()
    assert portfolio.summary() == before


def test_m7_sizing_and_sl_tp_unchanged_when_operational_checks_pass() -> None:
    settings = Settings(
        trading_mode="paper",
        max_position_size_pct=Decimal("0.05"),
        max_open_positions=10,
        max_daily_loss_pct=Decimal("0.02"),
        market_data_freshness_enabled=False,
    )
    manager = BasicRiskManager(settings)
    entry = Decimal("100")

    result = manager.evaluate(
        symbol="AAPL",
        entry_price=entry,
        portfolio_value=Decimal("100000"),
        open_positions=0,
        daily_pnl_pct=Decimal("0"),
        opens_new_exposure=True,
    )

    assert result.approved is True
    assert result.position_size == Decimal("5000.00")
    expected_stop = (entry * (Decimal("1") - DEFAULT_STOP_LOSS_PCT)).quantize(
        Decimal("0.0001")
    )
    risk_distance = entry - expected_stop
    expected_tp = (entry + risk_distance * DEFAULT_RISK_REWARD_RATIO).quantize(
        Decimal("0.0001")
    )
    assert result.stop_loss == expected_stop
    assert result.take_profit == expected_tp


def test_buy_into_existing_symbol_not_blocked_by_max_open_positions() -> None:
    """Adding to an existing position does not consume a new open-position slot."""
    settings = Settings(
        trading_mode="paper",
        max_position_size_pct=Decimal("0.05"),
        max_open_positions=1,
        max_daily_loss_pct=Decimal("0.02"),
        market_data_freshness_enabled=False,
    )
    portfolio = Portfolio(cash=Decimal("100000"))
    portfolio.positions["AAPL"] = _long_position(quantity=Decimal("5"))
    qty_before = portfolio.positions["AAPL"].quantity
    runtime, _ = _runtime(
        signal=_signal(action=SignalAction.BUY),
        portfolio=portfolio,
        settings=settings,
        executor=DryRunExecutor(),
    )

    result = runtime.run_once(
        RuntimeContext(symbol="AAPL", daily_pnl_pct=Decimal("0")),
    )

    assert result.success is True
    assert result.stage_reached == "portfolio"
    assert portfolio.position_count == 1
    assert portfolio.positions["AAPL"].quantity > qty_before
