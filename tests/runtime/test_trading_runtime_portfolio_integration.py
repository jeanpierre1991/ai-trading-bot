"""Tests for read-only Portfolio integration in BasicTradingRuntime."""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock

from config.settings import Settings
from core.types import PositionId, Side, SignalAction, Symbol
from portfolio_manager.portfolio import Portfolio, Position
from risk_manager.basic import BasicRiskManager
from runtime.context import RuntimeContext
from runtime.trading_runtime import BasicTradingRuntime
from strategy_engine.signal import StrategySignal


def _signal(
    *,
    action: SignalAction,
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
    quantity: Decimal,
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


class _ContextValueOnlyPortfolio:
    """Stub without total_value so RuntimeContext.portfolio_value is used."""

    positions: dict = {}

    def summary(self) -> dict[str, float | int]:
        return {
            "cash": 0.0,
            "positions_value": 0.0,
            "total_value": 0.0,
            "position_count": 0,
        }


def _build_runtime(
    *,
    signal: StrategySignal,
    portfolio: object,
    max_position_size_pct: Decimal = Decimal("0.05"),
    risk_manager: BasicRiskManager | MagicMock | None = None,
) -> tuple[BasicTradingRuntime, MagicMock, MagicMock]:
    settings = Settings(
        max_position_size_pct=max_position_size_pct,
        market_data_freshness_enabled=False,
    )
    market_data = MagicMock()
    market_data.get_bars.return_value = [object()]
    strategy_engine = MagicMock()
    strategy_engine.evaluate.return_value = signal
    manager = risk_manager or BasicRiskManager(settings)
    runtime = BasicTradingRuntime(
        settings=settings,
        market_data=market_data,
        strategy_engine=strategy_engine,
        risk_manager=manager,
        portfolio=portfolio,
    )
    return runtime, market_data, strategy_engine


def test_buy_uses_real_portfolio_total_value() -> None:
    portfolio = Portfolio(cash=Decimal("40000"))
    risk_manager = MagicMock(wraps=BasicRiskManager(Settings(max_position_size_pct=Decimal("0.05"))))
    runtime, _, _ = _build_runtime(
        signal=_signal(action=SignalAction.BUY, price=Decimal("100")),
        portfolio=portfolio,
        risk_manager=risk_manager,
    )

    result = runtime.run_once(
        RuntimeContext(symbol="AAPL", portfolio_value=999_999.0, daily_pnl_pct=Decimal("0")),
    )

    assert result.success is True
    assert result.intent is not None
    assert result.intent.max_position_value == Decimal("2000.00")
    assert result.intent.quantity == Decimal("20.0000")
    risk_manager.evaluate.assert_called_once()
    assert risk_manager.evaluate.call_args.kwargs["portfolio_value"] == Decimal("40000.00")


def test_context_portfolio_value_fallback_when_portfolio_unusable() -> None:
    portfolio = _ContextValueOnlyPortfolio()
    risk_manager = MagicMock(wraps=BasicRiskManager(Settings(max_position_size_pct=Decimal("0.10"))))
    runtime, _, _ = _build_runtime(
        signal=_signal(action=SignalAction.BUY, price=Decimal("50")),
        portfolio=portfolio,
        max_position_size_pct=Decimal("0.10"),
        risk_manager=risk_manager,
    )

    result = runtime.run_once(
        RuntimeContext(symbol="AAPL", portfolio_value=10_000.0, daily_pnl_pct=Decimal("0")),
    )

    assert result.success is True
    assert result.intent is not None
    assert result.intent.max_position_value == Decimal("1000.00")
    assert result.intent.quantity == Decimal("20.0000")
    assert risk_manager.evaluate.call_args.kwargs["portfolio_value"] == Decimal("10000.0")


def test_sell_with_sufficient_position() -> None:
    # total_value = 100000 cash + 10000 position = 110000 → 5% = 5500 → qty 55
    portfolio = Portfolio(cash=Decimal("100000"))
    portfolio.positions["AAPL"] = _long_position(quantity=Decimal("100"))
    before = portfolio.summary()
    runtime, _, _ = _build_runtime(
        signal=_signal(action=SignalAction.SELL, price=Decimal("100")),
        portfolio=portfolio,
    )

    result = runtime.run_once(RuntimeContext(symbol="AAPL", daily_pnl_pct=Decimal("0")))

    assert result.success is True
    assert result.intent is not None
    assert result.intent.side is Side.SELL
    assert result.intent.quantity == Decimal("55.0000")
    assert result.intent.max_position_value == Decimal("5500.00")
    assert portfolio.summary() == before
    assert result.portfolio_snapshot == before


def test_sell_without_position_returns_controlled_result() -> None:
    portfolio = Portfolio(cash=Decimal("100000"))
    before = portfolio.summary()
    runtime, _, _ = _build_runtime(
        signal=_signal(action=SignalAction.SELL, price=Decimal("100")),
        portfolio=portfolio,
    )

    result = runtime.run_once(RuntimeContext(symbol="AAPL", daily_pnl_pct=Decimal("0")))

    assert result.success is False
    assert result.stage_reached == "intent"
    assert result.aborted_reason == "No open long position for AAPL"
    assert result.signal is not None
    assert result.risk_evaluation is not None
    assert result.risk_evaluation.approved is True
    assert result.intent is None
    assert result.portfolio_snapshot == before
    assert portfolio.summary() == before


def test_sell_quantity_exceeding_available_returns_controlled_result() -> None:
    # total_value = 101000 → 5% = 5050 → sized qty 50.5 > available 10
    portfolio = Portfolio(cash=Decimal("100000"))
    portfolio.positions["AAPL"] = _long_position(quantity=Decimal("10"))
    before = portfolio.summary()
    runtime, _, _ = _build_runtime(
        signal=_signal(action=SignalAction.SELL, price=Decimal("100")),
        portfolio=portfolio,
    )

    result = runtime.run_once(RuntimeContext(symbol="AAPL", daily_pnl_pct=Decimal("0")))

    assert result.success is False
    assert result.stage_reached == "intent"
    assert result.aborted_reason == (
        "Sell quantity 50.5000 exceeds available 10 for AAPL"
    )
    assert result.intent is None
    assert result.risk_evaluation is not None
    assert portfolio.summary() == before


def test_close_with_existing_position_uses_full_quantity() -> None:
    portfolio = Portfolio(cash=Decimal("80000"))
    portfolio.positions["AAPL"] = _long_position(
        quantity=Decimal("7.5"),
        price=Decimal("100"),
    )
    before = portfolio.summary()
    runtime, _, _ = _build_runtime(
        signal=_signal(action=SignalAction.CLOSE, price=Decimal("100")),
        portfolio=portfolio,
    )

    result = runtime.run_once(RuntimeContext(symbol="AAPL", daily_pnl_pct=Decimal("0")))

    assert result.success is True
    assert result.intent is not None
    assert result.intent.side is Side.SELL
    assert result.intent.quantity == Decimal("7.5")
    assert result.intent.max_position_value == Decimal("750.00")
    assert portfolio.summary() == before
    assert result.portfolio_snapshot == before


def test_portfolio_unchanged_and_snapshot_matches_after_run_once() -> None:
    portfolio = Portfolio(cash=Decimal("25000"))
    portfolio.positions["AAPL"] = _long_position(quantity=Decimal("3"))
    cash_before = portfolio.cash
    positions_before = {
        key: (pos.quantity, pos.entry_price, pos.current_price)
        for key, pos in portfolio.positions.items()
    }
    before = portfolio.summary()
    runtime, _, _ = _build_runtime(
        signal=_signal(action=SignalAction.BUY, price=Decimal("100")),
        portfolio=portfolio,
    )

    result = runtime.run_once(RuntimeContext(symbol="AAPL", daily_pnl_pct=Decimal("0")))

    assert result.success is True
    assert result.portfolio_snapshot == before
    assert portfolio.cash == cash_before
    assert {
        key: (pos.quantity, pos.entry_price, pos.current_price)
        for key, pos in portfolio.positions.items()
    } == positions_before


def test_risk_manager_receives_portfolio_total_value() -> None:
    portfolio = Portfolio(cash=Decimal("12345.67"))
    risk_manager = MagicMock(wraps=BasicRiskManager(Settings(max_position_size_pct=Decimal("0.05"))))
    runtime, _, _ = _build_runtime(
        signal=_signal(action=SignalAction.BUY, price=Decimal("100")),
        portfolio=portfolio,
        risk_manager=risk_manager,
    )

    runtime.run_once(RuntimeContext(symbol="AAPL", portfolio_value=1.0, daily_pnl_pct=Decimal("0")))

    assert risk_manager.evaluate.call_args.kwargs["portfolio_value"] == Decimal("12345.67")


def test_apply_fill_not_called() -> None:
    portfolio = Portfolio(cash=Decimal("100000"))
    portfolio.apply_fill = MagicMock(wraps=portfolio.apply_fill)  # type: ignore[method-assign]
    runtime, _, _ = _build_runtime(
        signal=_signal(action=SignalAction.BUY, price=Decimal("100")),
        portfolio=portfolio,
    )

    result = runtime.run_once(RuntimeContext(symbol="AAPL", daily_pnl_pct=Decimal("0")))

    assert result.success is True
    portfolio.apply_fill.assert_not_called()


def test_no_broker_interaction() -> None:
    portfolio = Portfolio(cash=Decimal("100000"))
    portfolio.positions["AAPL"] = _long_position(quantity=Decimal("100"))
    broker = MagicMock()
    runtime, _, _ = _build_runtime(
        signal=_signal(action=SignalAction.SELL, price=Decimal("100")),
        portfolio=portfolio,
    )
    # Runtime must not depend on or invoke a broker in this milestone.
    assert not hasattr(runtime, "broker")
    assert not hasattr(runtime, "_broker")

    result = runtime.run_once(RuntimeContext(symbol="AAPL", daily_pnl_pct=Decimal("0")))

    assert result.success is True
    assert result.order is None
    assert result.execution is None
    broker.assert_not_called()
    broker.place_order.assert_not_called()
