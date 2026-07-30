"""Bounded historical backtest runner (Milestone 10.2).

Advances ``HistoricalMarketDataProvider`` and calls existing
``TradingRuntime.run_once`` only — no parallel strategy/risk/booking pipeline.
Simulation uses ``DryRunExecutor`` / ``CommissionDryRunExecutor`` exclusively.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from backtesting.commission import CommissionDryRunExecutor
from backtesting.engine import BacktestResult
from broker_interface.execution import ExecutionStatus
from core.exceptions import ConfigurationError
from core.types import Side, Symbol, TradingMode
from market_data.historical_provider import (
    MAX_HISTORICAL_BARS,
    HistoricalMarketDataProvider,
    HistoricalRuntimeMarketData,
)
from runtime.base import TradingRuntime
from runtime.broker_executor import BrokerOrderExecutor
from runtime.context import RuntimeContext
from runtime.dry_run import DryRunExecutor
from runtime.models import PipelineResult

# Hard cap aligned with historical series bound.
MAX_BACKTEST_CYCLES = MAX_HISTORICAL_BARS

_MONEY = Decimal("0.01")
_PCT = Decimal("0.0001")


@dataclass(frozen=True)
class BacktestConfig:
    """Inputs for one bounded historical backtest."""

    symbol: str
    strategy_name: str | None = None
    bar_limit: int = 100
    warmup_bars: int | None = None
    max_cycles: int | None = None


class BacktestRunner:
    """Replay historical bars through ``runtime.run_once`` (paper context)."""

    def __init__(
        self,
        runtime: TradingRuntime,
        *,
        historical: HistoricalMarketDataProvider | HistoricalRuntimeMarketData,
    ) -> None:
        self._runtime = runtime
        self._provider = self._resolve_provider(historical)
        self._assert_dry_run_only(runtime)
        self._assert_shared_market_data(runtime, self._provider)

    @property
    def runtime(self) -> TradingRuntime:
        return self._runtime

    @property
    def provider(self) -> HistoricalMarketDataProvider:
        return self._provider

    def run(self, config: BacktestConfig) -> BacktestResult:
        self._validate_config(config)
        portfolio = self._portfolio()
        commission_executor = self._commission_executor()
        if commission_executor is not None:
            commission_executor.reset_commission_total()

        warmup = (
            config.warmup_bars
            if config.warmup_bars is not None
            else min(config.bar_limit, self._provider.bar_count)
        )
        if warmup < 1 or warmup > self._provider.bar_count:
            raise ConfigurationError(
                f"warmup_bars must be in 1..{self._provider.bar_count}; got {warmup}"
            )

        max_cycles = (
            config.max_cycles
            if config.max_cycles is not None
            else self._provider.bar_count
        )
        if max_cycles < 1 or max_cycles > MAX_BACKTEST_CYCLES:
            raise ConfigurationError(
                f"max_cycles must be in 1..{MAX_BACKTEST_CYCLES}; got {max_cycles}"
            )

        self._provider.reset(end_exclusive=warmup)
        initial_capital = self._equity(portfolio)
        if initial_capital <= 0:
            raise ConfigurationError(
                f"backtest initial equity must be positive; got {initial_capital}"
            )

        start_bars = self._provider.get_bars(
            Symbol(self._provider.symbol),
            self._provider.timeframe,
            limit=1,
        )
        start_date = start_bars[0].timestamp

        total_trades = 0
        wins = 0
        losses = 0
        realized_pnl = Decimal("0")
        open_entry_fees: dict[str, Decimal] = {}
        cycles_executed = 0
        last_result: PipelineResult | None = None

        while cycles_executed < max_cycles:
            entry_price, qty_before = self._position_snapshot(portfolio, config.symbol)
            context = RuntimeContext(
                symbol=config.symbol,
                mode=TradingMode.PAPER,
                strategy_name=config.strategy_name,
                bar_limit=config.bar_limit,
                daily_pnl_pct=self._session_pnl_pct(initial_capital, portfolio),
                # M11.2: historical replay must not wall-clock-reject past bars.
                enforce_market_data_freshness=False,
                # M11.3: historical replay must not consult live session hours.
                enforce_market_hours=False,
            )
            result = self._runtime.run_once(context)
            last_result = result
            cycles_executed += 1
            self._mark_to_market(portfolio, config.symbol)

            if (
                result.success
                and result.execution is not None
                and result.execution.status is ExecutionStatus.FILLED
            ):
                total_trades += 1
                execution = result.execution
                symbol_key = str(execution.symbol)
                if execution.side is Side.BUY:
                    prior = open_entry_fees.get(symbol_key, Decimal("0"))
                    open_entry_fees[symbol_key] = (prior + execution.fee).quantize(
                        _MONEY
                    )
                elif execution.side is Side.SELL and entry_price is not None:
                    gross = (
                        (execution.fill_price - entry_price) * execution.filled_quantity
                    ).quantize(_MONEY)
                    buy_fees = open_entry_fees.get(symbol_key, Decimal("0"))
                    if qty_before is not None and qty_before > 0:
                        if execution.filled_quantity >= qty_before:
                            allocated_buy_fee = buy_fees
                            open_entry_fees.pop(symbol_key, None)
                        else:
                            allocated_buy_fee = (
                                buy_fees
                                * execution.filled_quantity
                                / qty_before
                            ).quantize(_MONEY)
                            open_entry_fees[symbol_key] = (
                                buy_fees - allocated_buy_fee
                            ).quantize(_MONEY)
                    else:
                        allocated_buy_fee = Decimal("0")
                    net = (gross - execution.fee - allocated_buy_fee).quantize(_MONEY)
                    realized_pnl = (realized_pnl + net).quantize(_MONEY)
                    if net > 0:
                        wins += 1
                    elif net < 0:
                        losses += 1

            if not result.success:
                break
            # Advance only when another cycle will run (bounded loop).
            if cycles_executed >= max_cycles:
                break
            if not self._provider.can_advance():
                break
            self._provider.advance(1)

        end_bars = self._provider.get_bars(
            Symbol(self._provider.symbol),
            self._provider.timeframe,
            limit=1,
        )
        end_date = end_bars[-1].timestamp if end_bars else start_date
        final_capital = self._equity(portfolio)
        total_return_pct = (
            (final_capital - initial_capital) / initial_capital
        ).quantize(_PCT)
        closed = wins + losses
        win_rate = (
            (Decimal(wins) / Decimal(closed)).quantize(_PCT) if closed > 0 else Decimal("0")
        )
        commissions_paid = (
            commission_executor.commissions_paid
            if commission_executor is not None
            else Decimal("0")
        )

        strategy_name = config.strategy_name or "default"
        if last_result is not None and last_result.signal is not None:
            strategy_name = last_result.signal.strategy_name or strategy_name

        return BacktestResult(
            strategy_name=strategy_name,
            start_date=start_date,
            end_date=end_date,
            initial_capital=initial_capital,
            final_capital=final_capital,
            total_return_pct=total_return_pct,
            total_trades=total_trades,
            win_rate=win_rate,
            wins=wins,
            losses=losses,
            realized_pnl=realized_pnl,
            commissions_paid=commissions_paid,
            cycles_executed=cycles_executed,
        )

    def _validate_config(self, config: BacktestConfig) -> None:
        if not isinstance(config.symbol, str) or not config.symbol.strip():
            raise ConfigurationError("symbol must be a non-empty string")
        if config.bar_limit <= 0:
            raise ConfigurationError("bar_limit must be a positive integer")
        if config.symbol.strip() != self._provider.symbol:
            raise ConfigurationError(
                f"config symbol {config.symbol!r} does not match historical "
                f"series symbol {self._provider.symbol!r}"
            )

    def _session_pnl_pct(self, initial_capital: Decimal, portfolio: Any) -> Decimal:
        current = self._equity(portfolio)
        return ((current - initial_capital) / initial_capital).quantize(_PCT)

    def _mark_to_market(self, portfolio: Any, symbol: str) -> None:
        positions = getattr(portfolio, "positions", None)
        if not positions or symbol not in positions:
            return
        visible = self._provider.get_bars(
            Symbol(self._provider.symbol),
            self._provider.timeframe,
            limit=1,
        )
        if not visible:
            return
        positions[symbol].current_price = visible[-1].close

    @staticmethod
    def _position_snapshot(
        portfolio: Any,
        symbol: str,
    ) -> tuple[Decimal | None, Decimal | None]:
        positions = getattr(portfolio, "positions", None)
        if not positions or symbol not in positions:
            return None, None
        position = positions[symbol]
        return position.entry_price, position.quantity

    def _portfolio(self) -> Any:
        portfolio = getattr(self._runtime, "portfolio", None)
        if portfolio is None:
            raise ConfigurationError("runtime has no portfolio for backtest")
        return portfolio

    @staticmethod
    def _equity(portfolio: Any) -> Decimal:
        total = getattr(portfolio, "total_value", None)
        if total is None:
            raise ConfigurationError("portfolio has no total_value")
        return total if isinstance(total, Decimal) else Decimal(total)

    def _commission_executor(self) -> CommissionDryRunExecutor | None:
        executor = getattr(self._runtime, "executor", None)
        if isinstance(executor, CommissionDryRunExecutor):
            return executor
        return None

    @staticmethod
    def _resolve_provider(
        historical: HistoricalMarketDataProvider | HistoricalRuntimeMarketData,
    ) -> HistoricalMarketDataProvider:
        if isinstance(historical, HistoricalRuntimeMarketData):
            return historical.provider
        if isinstance(historical, HistoricalMarketDataProvider):
            return historical
        raise ConfigurationError(
            "historical must be HistoricalMarketDataProvider or "
            "HistoricalRuntimeMarketData"
        )

    @staticmethod
    def _assert_shared_market_data(
        runtime: TradingRuntime,
        provider: HistoricalMarketDataProvider,
    ) -> None:
        market_data = getattr(runtime, "market_data", None)
        if isinstance(market_data, HistoricalRuntimeMarketData):
            if market_data.provider is not provider:
                raise ConfigurationError(
                    "BacktestRunner historical provider must be the same object "
                    "wired into runtime.market_data"
                )
            return
        if isinstance(market_data, HistoricalMarketDataProvider):
            if market_data is not provider:
                raise ConfigurationError(
                    "BacktestRunner historical provider must be the same object "
                    "wired into runtime.market_data"
                )
            return
        raise ConfigurationError(
            "runtime.market_data must be HistoricalRuntimeMarketData "
            "(or HistoricalMarketDataProvider) for backtest"
        )

    @staticmethod
    def _assert_dry_run_only(runtime: TradingRuntime) -> None:
        executor = getattr(runtime, "executor", None)
        if executor is None:
            raise ConfigurationError(
                "backtest requires a DryRunExecutor (or CommissionDryRunExecutor)"
            )
        if isinstance(executor, BrokerOrderExecutor):
            raise ConfigurationError(
                "backtest cannot use BrokerOrderExecutor; DryRunExecutor only "
                "(no live/paper broker path)"
            )
        if isinstance(executor, CommissionDryRunExecutor):
            return
        if isinstance(executor, DryRunExecutor):
            return
        raise ConfigurationError(
            f"backtest executor must be DryRunExecutor-based; got "
            f"{type(executor).__name__}"
        )
