"""M12.4 CI soak harness — mock MD, injectable clock/sleeper, no network."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

import pytest

from config.settings import Settings
from core.types import MarketBar, SignalAction
from market_data.calendars.us_equity_xnys import UsEquityXnysCalendar
from order_manager.manager import OrderManager
from portfolio_manager.portfolio import Portfolio
from risk_manager.basic import BasicRiskManager
from runtime.dry_run import DryRunExecutor
from runtime.kill_switch import FileEnvKillSwitch
from runtime.paper_operator import PaperOperator, PaperOperatorConfig
from runtime.paper_state_store import JsonPaperStateStore
from runtime.trading_runtime import BasicTradingRuntime
from strategy_engine.signal import StrategySignal

_NY = ZoneInfo("America/New_York")


@pytest.fixture(autouse=True)
def _forbid_real_operator_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("real time.sleep invoked in soak tests")

    monkeypatch.setattr("runtime.paper_operator.time.sleep", _boom)


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


class _FakeClock:
    def __init__(self, start: datetime) -> None:
        self._current = start

    def __call__(self) -> datetime:
        return self._current

    def advance(self, seconds: float) -> None:
        self._current = self._current + timedelta(seconds=seconds)


class _SequenceKill:
    def __init__(self, sequence: list[bool]) -> None:
        self._sequence = list(sequence)
        self.checks = 0

    def is_engaged(self) -> bool:
        idx = min(self.checks, len(self._sequence) - 1)
        self.checks += 1
        return self._sequence[idx]


def _runtime(*, now: datetime, bars: list[MarketBar], settings: Settings) -> BasicTradingRuntime:
    md = MagicMock()
    md.get_bars.return_value = bars
    strategy = MagicMock()
    strategy.evaluate.return_value = StrategySignal(
        symbol="AAPL",
        action=SignalAction.HOLD,
        confidence=0.5,
        strategy_name="ema_crossover",
        price=Decimal("0"),
    )
    return BasicTradingRuntime(
        settings=settings,
        market_data=md,
        strategy_engine=strategy,
        risk_manager=BasicRiskManager(settings),
        portfolio=Portfolio(cash=Decimal("100000")),
        executor=DryRunExecutor(),
        order_manager=OrderManager(),
        clock=lambda: now,
        session_calendar=UsEquityXnysCalendar(),
    )


def test_m12_soak_completes_max_cycles_mock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("PAPER_OPERATOR_KILL", raising=False)
    now = _utc(2026, 7, 15, 11, 0)
    bar = _bar(timestamp=_utc(2026, 7, 15, 10, 0))
    settings = _settings()
    runtime = _runtime(now=now, bars=[bar], settings=settings)
    state_path = tmp_path / "soak" / "state.json"
    sleeper = MagicMock()

    result = PaperOperator(
        runtime,
        settings=settings,
        kill_switch=FileEnvKillSwitch(tmp_path / "KILL"),
        clock=lambda: now,
        sleeper=sleeper,
    ).run(
        PaperOperatorConfig(
            symbol="AAPL",
            max_cycles=12,
            max_wall_time_seconds=3600.0,
            interval_seconds=0.25,
            state_path=state_path,
            execution="dry_run",
        )
    )

    assert result.cycles_executed == 12
    assert result.bound_reached == "max_cycles"
    assert result.kill_engaged is False
    assert result.stopped_early is False
    assert sleeper.call_count == 11
    sleeper.assert_called_with(0.25)
    assert state_path.is_file()
    loaded = JsonPaperStateStore(state_path).load()
    assert loaded.cycles_completed_total == 12
    assert loaded.operator_start_equity == Decimal("100000")
    assert all(r.success for r in result.results)


def test_m12_soak_kill_mid_run_stops_cleanly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("PAPER_OPERATOR_KILL", raising=False)
    now = _utc(2026, 7, 15, 11, 0)
    bar = _bar(timestamp=_utc(2026, 7, 15, 10, 0))
    settings = _settings()
    runtime = _runtime(now=now, bars=[bar], settings=settings)
    state_path = tmp_path / "soak_kill" / "state.json"
    sleeper = MagicMock()
    # pre-start False, then allow 3 cycles, kill before 4th
    kill = _SequenceKill([False] + [False] * 3 + [True])

    result = PaperOperator(
        runtime,
        settings=settings,
        kill_switch=kill,  # type: ignore[arg-type]
        clock=lambda: now,
        sleeper=sleeper,
    ).run(
        PaperOperatorConfig(
            symbol="AAPL",
            max_cycles=20,
            max_wall_time_seconds=3600.0,
            interval_seconds=1.0,
            state_path=state_path,
        )
    )

    assert result.cycles_executed == 3
    assert result.kill_engaged is True
    assert result.stop_reason == "kill_switch_engaged"
    assert result.bound_reached is None
    # Sleeps only between the three successful cycles (before kill check on 4th).
    assert sleeper.call_count == 3
    loaded = JsonPaperStateStore(state_path).load()
    assert loaded.cycles_completed_total == 3
    assert loaded.operator_start_equity == Decimal("100000")


def test_m12_soak_wall_time_bound(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("PAPER_OPERATOR_KILL", raising=False)
    start = _utc(2026, 7, 15, 11, 0)
    clock = _FakeClock(start)
    bar = _bar(timestamp=_utc(2026, 7, 15, 10, 0))
    settings = _settings()
    runtime = _runtime(now=start, bars=[bar], settings=settings)
    # Keep runtime bar clock fixed; operator uses advancing clock for wall bound.
    runtime._clock = lambda: start  # type: ignore[method-assign]
    state_path = tmp_path / "soak_wall" / "state.json"
    sleeper = MagicMock()

    class _AdvancingSleeper:
        def __init__(self) -> None:
            self.calls = 0

        def __call__(self, seconds: float) -> None:
            self.calls += 1
            clock.advance(seconds)

    advancing = _AdvancingSleeper()
    # Also advance clock slightly each cycle via wrapper around run — wall check
    # uses operator clock. Advance after each sleeper and at start capture.
    original_run_once = runtime.run_once

    def _run_once(context):  # type: ignore[no-untyped-def]
        result = original_run_once(context)
        clock.advance(10.0)
        return result

    runtime.run_once = _run_once  # type: ignore[method-assign]

    result = PaperOperator(
        runtime,
        settings=settings,
        kill_switch=FileEnvKillSwitch(tmp_path / "KILL"),
        clock=clock,
        sleeper=advancing,
    ).run(
        PaperOperatorConfig(
            symbol="AAPL",
            max_cycles=50,
            max_wall_time_seconds=25.0,
            interval_seconds=5.0,
            state_path=state_path,
        )
    )

    assert result.bound_reached == "max_wall_time"
    assert result.stopped_early is True
    assert result.cycles_executed >= 1
    assert result.cycles_executed < 50
    assert result.kill_engaged is False
    assert state_path.is_file()


def test_m12_soak_market_hours_continue_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A2: closed hours continue; SessionRunner would hard-stop."""
    monkeypatch.delenv("PAPER_OPERATOR_KILL", raising=False)
    # Saturday — XNYS closed
    now = _utc(2026, 7, 18, 11, 0)
    bar = _bar(timestamp=_utc(2026, 7, 17, 15, 30))
    settings = _settings()
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
    runtime = BasicTradingRuntime(
        settings=settings,
        market_data=md,
        strategy_engine=strategy,
        risk_manager=BasicRiskManager(settings),
        portfolio=Portfolio(cash=Decimal("100000")),
        executor=DryRunExecutor(),
        order_manager=OrderManager(),
        clock=lambda: now,
        session_calendar=UsEquityXnysCalendar(),
    )
    sleeper = MagicMock()
    state_path = tmp_path / "soak_hours" / "state.json"

    result = PaperOperator(
        runtime,
        settings=settings,
        kill_switch=FileEnvKillSwitch(tmp_path / "KILL"),
        clock=lambda: now,
        sleeper=sleeper,
    ).run(
        PaperOperatorConfig(
            symbol="AAPL",
            max_cycles=5,
            max_wall_time_seconds=3600.0,
            interval_seconds=0.5,
            state_path=state_path,
        )
    )

    assert result.cycles_executed == 5
    assert result.stopped_early is False
    assert result.bound_reached == "max_cycles"
    assert all(r.stage_reached == "market_hours" for r in result.results)
    assert all(r.success is False for r in result.results)
    assert sleeper.call_count == 4
    strategy.evaluate.assert_not_called()
