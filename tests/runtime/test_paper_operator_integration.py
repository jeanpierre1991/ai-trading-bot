"""M12.1 PaperOperator integration with BasicTradingRuntime (no network)."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

import pytest

from config.settings import Settings
from core.exceptions import ConfigurationError
from core.types import MarketBar, SignalAction
from market_data.calendars.us_equity_xnys import UsEquityXnysCalendar
from portfolio_manager.portfolio import Portfolio
from risk_manager.basic import BasicRiskManager
from runtime.dry_run import DryRunExecutor
from runtime.factory import create_trading_runtime
from runtime.kill_switch import FileEnvKillSwitch
from runtime.paper_operator import PaperOperator, PaperOperatorConfig
from runtime.trading_runtime import BasicTradingRuntime
from strategy_engine.signal import StrategySignal

_NY = ZoneInfo("America/New_York")


def _utc(year: int, month: int, day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=_NY).astimezone(timezone.utc)


def _bar(*, timestamp: datetime, close: Decimal = Decimal("100")) -> MarketBar:
    return MarketBar(
        timestamp=timestamp,
        open=close,
        high=close + Decimal("1"),
        low=close - Decimal("1"),
        close=close,
        volume=Decimal("1000"),
        symbol="AAPL",
        timeframe="1h",
    )


def _settings(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "trading_mode": "paper",
        "market_data_provider": "mock",
        "max_position_size_pct": Decimal("0.05"),
        "max_daily_loss_pct": Decimal("0.50"),
        "default_timeframe": "1h",
        "market_data_freshness_enabled": True,
        "market_data_freshness_bar_periods": 2,
        "market_data_freshness_slack_seconds": 120,
        "market_data_future_skew_seconds": 60,
        "market_hours_enabled": True,
        "market_hours_policy": "reject",
        "alerts_enabled": False,
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


def test_factory_still_rejects_live_for_operator_settings() -> None:
    settings = _settings(trading_mode="live")
    with pytest.raises(ConfigurationError, match="live"):
        create_trading_runtime(
            settings,
            execution="dry_run",
            market_data=MagicMock(),
            strategy_engine=MagicMock(),
            portfolio=Portfolio(cash=Decimal("100000")),
            with_alerts=False,
        )


def test_operator_dry_run_runtime_respects_max_cycles(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.delenv("PAPER_OPERATOR_KILL", raising=False)
    now = _utc(2026, 7, 15, 11, 0)
    bar = _bar(timestamp=_utc(2026, 7, 15, 10, 0))
    md = MagicMock()
    md.get_bars.return_value = [bar]
    strategy = MagicMock()
    strategy.evaluate.return_value = StrategySignal(
        symbol="AAPL",
        action=SignalAction.HOLD,
        confidence=0.5,
        strategy_name="ema_crossover",
        price=Decimal("0"),
    )
    settings = _settings()
    runtime = BasicTradingRuntime(
        settings=settings,
        market_data=md,
        strategy_engine=strategy,
        risk_manager=BasicRiskManager(settings),
        portfolio=Portfolio(cash=Decimal("100000")),
        executor=DryRunExecutor(),
        clock=lambda: now,
        session_calendar=UsEquityXnysCalendar(),
    )

    result = PaperOperator(
        runtime,
        settings=settings,
        kill_switch=FileEnvKillSwitch(tmp_path / "KILL"),
        clock=lambda: now,
    ).run(
        PaperOperatorConfig(
            symbol="AAPL",
            max_cycles=2,
            max_wall_time_seconds=3600.0,
            interval_seconds=1.0,
            execution="dry_run",
        )
    )

    assert result.cycles_executed == 2
    assert result.stopped_early is False
    assert result.bound_reached == "max_cycles"
    assert all(r.success for r in result.results)


def test_operator_continues_on_real_market_hours_reject(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.delenv("PAPER_OPERATOR_KILL", raising=False)
    # Saturday — XNYS closed; policy=reject → stage market_hours.
    now = _utc(2026, 7, 18, 11, 0)
    # Bar near last regular close so freshness uses session-aware T_ref.
    bar = _bar(timestamp=_utc(2026, 7, 17, 15, 30))
    md = MagicMock()
    md.get_bars.return_value = [bar]
    strategy = MagicMock()
    strategy.evaluate.return_value = StrategySignal(
        symbol="AAPL",
        action=SignalAction.BUY,
        confidence=0.9,
        strategy_name="ema_crossover",
        price=Decimal("100"),
    )
    settings = _settings(market_hours_policy="reject")
    runtime = BasicTradingRuntime(
        settings=settings,
        market_data=md,
        strategy_engine=strategy,
        risk_manager=BasicRiskManager(settings),
        portfolio=Portfolio(cash=Decimal("100000")),
        executor=DryRunExecutor(),
        clock=lambda: now,
        session_calendar=UsEquityXnysCalendar(),
    )

    result = PaperOperator(
        runtime,
        settings=settings,
        kill_switch=FileEnvKillSwitch(tmp_path / "KILL"),
        clock=lambda: now,
    ).run(
        PaperOperatorConfig(
            symbol="AAPL",
            max_cycles=2,
            max_wall_time_seconds=3600.0,
            interval_seconds=1.0,
        )
    )

    assert result.cycles_executed == 2
    assert result.stopped_early is False
    assert all(r.stage_reached == "market_hours" for r in result.results)
    assert all(r.success is False for r in result.results)
    strategy.evaluate.assert_not_called()


def test_operator_hard_stops_on_real_freshness_failure(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.delenv("PAPER_OPERATOR_KILL", raising=False)
    now = _utc(2026, 7, 15, 11, 0)  # RTH Wednesday
    # Very old bar → freshness abort during open hours (not continue-on).
    bar = _bar(timestamp=_utc(2026, 7, 15, 6, 0))
    md = MagicMock()
    md.get_bars.return_value = [bar]
    strategy = MagicMock()
    settings = _settings()
    runtime = BasicTradingRuntime(
        settings=settings,
        market_data=md,
        strategy_engine=strategy,
        risk_manager=BasicRiskManager(settings),
        portfolio=Portfolio(cash=Decimal("100000")),
        executor=DryRunExecutor(),
        clock=lambda: now,
        session_calendar=UsEquityXnysCalendar(),
    )

    result = PaperOperator(
        runtime,
        settings=settings,
        kill_switch=FileEnvKillSwitch(tmp_path / "KILL"),
        clock=lambda: now,
    ).run(
        PaperOperatorConfig(
            symbol="AAPL",
            max_cycles=3,
            max_wall_time_seconds=3600.0,
            interval_seconds=1.0,
        )
    )

    assert result.cycles_executed == 1
    assert result.stopped_early is True
    assert result.results[0].stage_reached == "market_data"
    strategy.evaluate.assert_not_called()
