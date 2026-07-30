"""M10.2 BacktestRunner + BacktestResult metrics tests (deterministic, no network)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from backtesting.commission import CommissionDryRunExecutor
from backtesting.engine import BacktestResult
from backtesting.runner import MAX_BACKTEST_CYCLES, BacktestConfig, BacktestRunner
from broker_interface.broker import PaperBroker
from config.settings import Settings
from core.exceptions import ConfigurationError
from core.types import MarketBar, SignalAction, TimeFrame
from market_data.historical_provider import (
    HistoricalMarketDataProvider,
    HistoricalRuntimeMarketData,
)
from portfolio_manager.portfolio import Portfolio
from risk_manager.basic import BasicRiskManager
from runtime.broker_executor import BrokerOrderExecutor
from runtime.dry_run import DryRunExecutor
from runtime.trading_runtime import BasicTradingRuntime
from strategy_engine.signal import StrategySignal


def _bar(
    index: int,
    *,
    symbol: str = "AAPL",
    price: Decimal | None = None,
) -> MarketBar:
    close = price if price is not None else Decimal("100") + Decimal(index)
    stamp = datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(hours=index)
    return MarketBar(
        timestamp=stamp,
        open=close,
        high=close + Decimal("1"),
        low=close - Decimal("1"),
        close=close,
        volume=Decimal("1000"),
        symbol=symbol,
        timeframe=TimeFrame.H1.value,
    )


def _series(count: int, prices: list[Decimal] | None = None) -> list[MarketBar]:
    if prices is not None:
        return [_bar(i, price=prices[i]) for i in range(len(prices))]
    return [_bar(i) for i in range(count)]


class _ScriptedStrategy:
    """Deterministic signal sequence; price always taken from the last bar."""

    def __init__(self, actions: list[SignalAction], *, name: str = "scripted") -> None:
        self._actions = list(actions)
        self._index = 0
        self._name = name

    def evaluate(
        self,
        bars: list[MarketBar],
        *,
        symbol: str | None = None,
        strategy_name: str | None = None,
    ) -> StrategySignal:
        action = self._actions[min(self._index, len(self._actions) - 1)]
        self._index += 1
        price = bars[-1].close if bars else Decimal("100")
        return StrategySignal(
            symbol=symbol or "AAPL",
            action=action,
            confidence=0.9,
            strategy_name=strategy_name or self._name,
            price=price,
        )


def _build_runtime(
    *,
    bars: list[MarketBar],
    actions: list[SignalAction],
    commission_pct: Decimal | None = None,
    initial_capital: Decimal | None = None,
    executor: object | None = None,
) -> tuple[BasicTradingRuntime, HistoricalMarketDataProvider, HistoricalRuntimeMarketData]:
    settings = Settings(
        trading_mode="paper",
        max_position_size_pct=Decimal("0.10"),
        backtest_initial_capital=initial_capital
        if initial_capital is not None
        else Decimal("100000"),
        backtest_commission_pct=commission_pct
        if commission_pct is not None
        else Decimal("0.001"),
    )
    provider = HistoricalMarketDataProvider(bars, initial_end_exclusive=0)
    market_data = HistoricalRuntimeMarketData(provider)
    portfolio = Portfolio(cash=settings.backtest_initial_capital)
    if executor is None:
        if commission_pct is not None and commission_pct != 0:
            executor = CommissionDryRunExecutor(settings.backtest_commission_pct)
        elif commission_pct == Decimal("0"):
            executor = CommissionDryRunExecutor(Decimal("0"))
        else:
            executor = CommissionDryRunExecutor(settings.backtest_commission_pct)

    runtime = BasicTradingRuntime(
        settings=settings,
        market_data=market_data,
        strategy_engine=_ScriptedStrategy(actions),
        risk_manager=BasicRiskManager(settings),
        portfolio=portfolio,
        executor=executor,  # type: ignore[arg-type]
    )
    return runtime, provider, market_data


def test_zero_trades_hold_only() -> None:
    bars = _series(8)
    runtime, provider, market_data = _build_runtime(
        bars=bars,
        actions=[SignalAction.HOLD],
        commission_pct=Decimal("0"),
    )
    runner = BacktestRunner(runtime, historical=market_data)
    result = runner.run(
        BacktestConfig(symbol="AAPL", warmup_bars=3, max_cycles=5, bar_limit=50)
    )

    assert result.total_trades == 0
    assert result.wins == 0
    assert result.losses == 0
    assert result.win_rate == Decimal("0")
    assert result.realized_pnl == Decimal("0")
    assert result.commissions_paid == Decimal("0")
    assert result.initial_capital == Decimal("100000")
    assert result.final_capital == result.initial_capital
    assert result.ending_equity == result.final_capital
    assert result.total_return_pct == Decimal("0")
    assert result.return_pct == Decimal("0")
    assert result.cycles_executed == 5
    assert provider.end_exclusive == 7  # warmup 3 + 4 advances (no advance after last cycle)


def test_winning_round_trip() -> None:
    # BUY at 100, CLOSE at 110 → positive realized PnL
    prices = [
        Decimal("100"),
        Decimal("100"),
        Decimal("100"),
        Decimal("100"),  # warmup end / BUY
        Decimal("110"),  # CLOSE
        Decimal("110"),
    ]
    runtime, _, market_data = _build_runtime(
        bars=_series(0, prices=prices),
        actions=[SignalAction.BUY, SignalAction.CLOSE, SignalAction.HOLD],
        commission_pct=Decimal("0"),
        initial_capital=Decimal("100000"),
    )
    result = BacktestRunner(runtime, historical=market_data).run(
        BacktestConfig(symbol="AAPL", warmup_bars=4, max_cycles=3, bar_limit=50)
    )

    assert result.total_trades == 2
    assert result.wins == 1
    assert result.losses == 0
    assert result.win_rate == Decimal("1.0000")
    assert result.realized_pnl > 0
    assert result.final_capital > result.initial_capital
    assert result.total_return_pct > 0
    assert result.commissions_paid == Decimal("0")


def test_losing_round_trip() -> None:
    prices = [
        Decimal("100"),
        Decimal("100"),
        Decimal("100"),
        Decimal("100"),  # BUY
        Decimal("90"),  # CLOSE loss
        Decimal("90"),
    ]
    runtime, _, market_data = _build_runtime(
        bars=_series(0, prices=prices),
        actions=[SignalAction.BUY, SignalAction.CLOSE, SignalAction.HOLD],
        commission_pct=Decimal("0"),
    )
    result = BacktestRunner(runtime, historical=market_data).run(
        BacktestConfig(symbol="AAPL", warmup_bars=4, max_cycles=3, bar_limit=50)
    )

    assert result.total_trades == 2
    assert result.wins == 0
    assert result.losses == 1
    assert result.win_rate == Decimal("0.0000")
    assert result.realized_pnl < 0
    assert result.final_capital < result.initial_capital


def test_commission_affects_equity_exactly_once() -> None:
    prices = [
        Decimal("100"),
        Decimal("100"),
        Decimal("100"),
        Decimal("100"),
        Decimal("100"),
        Decimal("100"),
    ]
    actions = [SignalAction.BUY, SignalAction.CLOSE, SignalAction.HOLD]

    runtime_zero, _, md_zero = _build_runtime(
        bars=_series(0, prices=prices),
        actions=actions,
        commission_pct=Decimal("0"),
    )
    zero = BacktestRunner(runtime_zero, historical=md_zero).run(
        BacktestConfig(symbol="AAPL", warmup_bars=4, max_cycles=3, bar_limit=50)
    )

    runtime_fee, _, md_fee = _build_runtime(
        bars=_series(0, prices=list(prices)),
        actions=list(actions),
        commission_pct=Decimal("0.001"),
    )
    fee = BacktestRunner(runtime_fee, historical=md_fee).run(
        BacktestConfig(symbol="AAPL", warmup_bars=4, max_cycles=3, bar_limit=50)
    )

    assert fee.commissions_paid > 0
    assert zero.commissions_paid == Decimal("0")
    # Same flat prices → zero-commission equity unchanged; fee run loses exactly commissions.
    assert zero.final_capital == zero.initial_capital
    assert fee.final_capital == (fee.initial_capital - fee.commissions_paid).quantize(
        Decimal("0.01")
    )
    assert fee.realized_pnl == (-fee.commissions_paid).quantize(Decimal("0.01"))


def test_bounded_execution_hard_cap() -> None:
    bars = _series(20)
    runtime, provider, market_data = _build_runtime(
        bars=bars,
        actions=[SignalAction.HOLD],
        commission_pct=Decimal("0"),
    )
    result = BacktestRunner(runtime, historical=market_data).run(
        BacktestConfig(symbol="AAPL", warmup_bars=2, max_cycles=3, bar_limit=50)
    )

    assert result.cycles_executed == 3
    assert provider.end_exclusive == 4  # 2 + 2 advances (after cycles 1 and 2)
    assert provider.can_advance() is True  # more bars remain; stopped by max_cycles

    with pytest.raises(ConfigurationError, match="max_cycles"):
        BacktestRunner(runtime, historical=market_data).run(
            BacktestConfig(
                symbol="AAPL",
                warmup_bars=2,
                max_cycles=MAX_BACKTEST_CYCLES + 1,
            )
        )


def test_deterministic_repeatability() -> None:
    prices = [
        Decimal("100"),
        Decimal("101"),
        Decimal("102"),
        Decimal("103"),
        Decimal("110"),
        Decimal("111"),
    ]
    actions = [SignalAction.BUY, SignalAction.CLOSE, SignalAction.HOLD]

    def _run() -> BacktestResult:
        runtime, _, market_data = _build_runtime(
            bars=_series(0, prices=list(prices)),
            actions=list(actions),
            commission_pct=Decimal("0.001"),
        )
        return BacktestRunner(runtime, historical=market_data).run(
            BacktestConfig(symbol="AAPL", warmup_bars=4, max_cycles=3, bar_limit=50)
        )

    first = _run()
    second = _run()
    assert first == second
    assert first.to_dict() == second.to_dict()


def test_rejects_broker_executor_path() -> None:
    bars = _series(5)
    settings = Settings(trading_mode="paper", backtest_initial_capital=Decimal("100000"))
    provider = HistoricalMarketDataProvider(bars, initial_end_exclusive=0)
    market_data = HistoricalRuntimeMarketData(provider)
    broker = PaperBroker(buying_power=settings.backtest_initial_capital)
    runtime = BasicTradingRuntime(
        settings=settings,
        market_data=market_data,
        strategy_engine=_ScriptedStrategy([SignalAction.HOLD]),
        risk_manager=BasicRiskManager(settings),
        portfolio=Portfolio(cash=settings.backtest_initial_capital),
        executor=BrokerOrderExecutor(broker),
    )

    with pytest.raises(ConfigurationError, match="BrokerOrderExecutor"):
        BacktestRunner(runtime, historical=market_data)


def test_commission_wrapper_rejects_non_dry_run_inner() -> None:
    broker = PaperBroker(buying_power=Decimal("100000"))
    with pytest.raises(ConfigurationError, match="DryRunExecutor"):
        CommissionDryRunExecutor(
            Decimal("0.001"),
            inner=BrokerOrderExecutor(broker),  # type: ignore[arg-type]
        )


def test_plain_dry_run_executor_allowed_zero_commission_metric() -> None:
    bars = _series(6)
    runtime, _, market_data = _build_runtime(
        bars=bars,
        actions=[SignalAction.HOLD],
        executor=DryRunExecutor(),
    )
    result = BacktestRunner(runtime, historical=market_data).run(
        BacktestConfig(symbol="AAPL", warmup_bars=2, max_cycles=2, bar_limit=50)
    )
    assert result.commissions_paid == Decimal("0")
    assert result.cycles_executed == 2


def test_backtest_result_aliases_and_to_dict() -> None:
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    result = BacktestResult(
        strategy_name="x",
        start_date=now,
        end_date=now,
        initial_capital=Decimal("100"),
        final_capital=Decimal("110"),
        total_return_pct=Decimal("0.1000"),
        total_trades=2,
        win_rate=Decimal("1.0000"),
        wins=1,
        losses=0,
        realized_pnl=Decimal("10"),
        commissions_paid=Decimal("0.5"),
        cycles_executed=4,
    )
    assert result.ending_equity == Decimal("110")
    assert result.return_pct == Decimal("0.1000")
    payload = result.to_dict()
    assert payload["ending_equity"] == 110.0
    assert payload["return_pct"] == 0.1
    assert payload["wins"] == 1
    assert payload["commissions_paid"] == 0.5


def test_run_once_is_invoked_each_cycle(monkeypatch: pytest.MonkeyPatch) -> None:
    bars = _series(6)
    runtime, _, market_data = _build_runtime(
        bars=bars,
        actions=[SignalAction.HOLD],
        commission_pct=Decimal("0"),
    )
    calls: list[str] = []
    original = runtime.run_once

    def _wrap(context):  # type: ignore[no-untyped-def]
        calls.append(context.symbol)
        return original(context)

    monkeypatch.setattr(runtime, "run_once", _wrap)
    result = BacktestRunner(runtime, historical=market_data).run(
        BacktestConfig(symbol="AAPL", warmup_bars=2, max_cycles=4, bar_limit=50)
    )
    assert result.cycles_executed == 4
    assert calls == ["AAPL", "AAPL", "AAPL", "AAPL"]
