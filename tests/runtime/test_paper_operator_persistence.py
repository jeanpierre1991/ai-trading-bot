"""M12.2 PaperOperator persistence, resume, and E1 idempotency tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

import pytest

from config.settings import Settings
from core.exceptions import ConfigurationError
from core.types import MarketBar, SignalAction, TradingMode
from market_data.calendars.us_equity_xnys import UsEquityXnysCalendar
from order_manager.manager import OrderManager
from portfolio_manager.portfolio import Portfolio
from risk_manager.basic import BasicRiskManager
from runtime.context import RuntimeContext
from runtime.dry_run import DryRunExecutor
from runtime.factory import create_trading_runtime
from runtime.kill_switch import FileEnvKillSwitch
from runtime.models import PipelineResult
from runtime.paper_operator import PaperOperator, PaperOperatorConfig
from runtime.paper_state_store import JsonPaperStateStore
from runtime.session import SessionConfig, SessionRunner
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


def _config(state_path: Path, **overrides: object) -> PaperOperatorConfig:
    base: dict[str, object] = {
        "symbol": "AAPL",
        "max_cycles": 2,
        "max_wall_time_seconds": 3600.0,
        "interval_seconds": 1.0,
        "execution": "dry_run",
        "state_path": state_path,
    }
    base.update(overrides)
    return PaperOperatorConfig(**base)  # type: ignore[arg-type]


class _SequenceKillSwitch:
    def __init__(self, sequence: list[bool]) -> None:
        self._sequence = list(sequence)
        self.checks = 0

    def is_engaged(self) -> bool:
        idx = min(self.checks, len(self._sequence) - 1)
        self.checks += 1
        return self._sequence[idx]


def _paper_operator(
    runtime: object,
    *,
    settings: Settings | None = None,
    kill_switch: object | None = None,
    clock: object | None = None,
    sleeper: object | None = None,
) -> PaperOperator:
    """Build PaperOperator with an injected sleeper (never production time.sleep)."""
    return PaperOperator(
        runtime,  # type: ignore[arg-type]
        settings=settings or _settings(),
        kill_switch=kill_switch,  # type: ignore[arg-type]
        clock=clock,  # type: ignore[arg-type]
        sleeper=MagicMock() if sleeper is None else sleeper,  # type: ignore[arg-type]
    )


@pytest.fixture(autouse=True)
def _forbid_real_operator_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail closed if any test accidentally uses production time.sleep."""

    def _boom(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("real time.sleep invoked in operator tests")

    monkeypatch.setattr("runtime.paper_operator.time.sleep", _boom)


def _runtime(
    *,
    bars: list[MarketBar],
    now: datetime,
    action: SignalAction = SignalAction.HOLD,
    portfolio: Portfolio | None = None,
    order_manager: OrderManager | None = None,
    settings: Settings | None = None,
) -> BasicTradingRuntime:
    settings = settings or _settings()
    md = MagicMock()
    md.get_bars.return_value = bars
    strategy = MagicMock()
    close = bars[-1].close if bars else Decimal("100")
    strategy.evaluate.return_value = StrategySignal(
        symbol="AAPL",
        action=action,
        confidence=0.9,
        strategy_name="ema_crossover",
        price=close if action is not SignalAction.HOLD else Decimal("0"),
    )
    return BasicTradingRuntime(
        settings=settings,
        market_data=md,
        strategy_engine=strategy,
        risk_manager=BasicRiskManager(settings),
        portfolio=portfolio or Portfolio(cash=Decimal("100000")),
        executor=DryRunExecutor(),
        order_manager=order_manager if order_manager is not None else OrderManager(),
        clock=lambda: now,
        session_calendar=UsEquityXnysCalendar(),
    )


def test_missing_state_first_run_allowed(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("PAPER_OPERATOR_KILL", raising=False)
    now = _utc(2026, 7, 15, 11, 0)
    bar = _bar(timestamp=_utc(2026, 7, 15, 10, 0))
    state_path = tmp_path / "state.json"
    runtime = _runtime(bars=[bar], now=now)
    result = _paper_operator(
        runtime,
        settings=_settings(),
        kill_switch=FileEnvKillSwitch(tmp_path / "KILL"),
        clock=lambda: now,
    ).run(_config(state_path, max_cycles=1))

    assert result.resumed is False
    assert result.cycles_executed == 1
    assert state_path.is_file()
    loaded = JsonPaperStateStore(state_path).load()
    assert loaded.cycles_completed_total == 1
    assert loaded.operator_start_equity == Decimal("100000")


def test_operator_start_equity_and_cycles_survive_restart(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.delenv("PAPER_OPERATOR_KILL", raising=False)
    now = _utc(2026, 7, 15, 11, 0)
    bar = _bar(timestamp=_utc(2026, 7, 15, 10, 0))
    state_path = tmp_path / "state.json"

    portfolio_a = Portfolio(cash=Decimal("100000"))
    runtime_a = _runtime(bars=[bar], now=now, portfolio=portfolio_a)
    result_a = _paper_operator(
        runtime_a,
        settings=_settings(),
        kill_switch=FileEnvKillSwitch(tmp_path / "KILL"),
        clock=lambda: now,
    ).run(_config(state_path, max_cycles=2))
    assert result_a.cycles_completed_total == 2
    assert result_a.operator_start_equity == Decimal("100000")

    # Simulate process restart with a fresh portfolio object that would otherwise
    # reset the risk baseline if persistence were ignored.
    portfolio_b = Portfolio(cash=Decimal("50000"))
    runtime_b = _runtime(bars=[bar], now=now, portfolio=portfolio_b)
    result_b = _paper_operator(
        runtime_b,
        settings=_settings(),
        kill_switch=FileEnvKillSwitch(tmp_path / "KILL"),
        clock=lambda: now + timedelta(minutes=5),
    ).run(_config(state_path, max_cycles=1))

    assert result_b.resumed is True
    assert result_b.operator_start_equity == Decimal("100000")
    assert result_b.cycles_completed_total == 3
    assert portfolio_b.cash == Decimal("100000")  # restored, not 50000
    loaded = JsonPaperStateStore(state_path).load()
    assert loaded.cycles_completed_total == 3
    assert loaded.last_cycle_at is not None


def test_e1_no_duplicate_side_effects_after_resume_same_bar(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.delenv("PAPER_OPERATOR_KILL", raising=False)
    now = _utc(2026, 7, 15, 11, 0)
    bar_ts = _utc(2026, 7, 15, 10, 0)
    bar = _bar(timestamp=bar_ts)
    state_path = tmp_path / "state.json"

    runtime_a = _runtime(
        bars=[bar],
        now=now,
        action=SignalAction.BUY,
        portfolio=Portfolio(cash=Decimal("100000")),
    )
    cash_before = runtime_a.portfolio.cash
    result_a = _paper_operator(
        runtime_a,
        settings=_settings(),
        kill_switch=FileEnvKillSwitch(tmp_path / "KILL"),
        clock=lambda: now,
    ).run(_config(state_path, max_cycles=1))
    assert result_a.cycles_executed == 1
    assert result_a.results[0].order is not None
    assert result_a.results[0].market_bar_timestamp == bar_ts
    assert runtime_a.market_data.get_bars.call_count == 1  # no operator peek
    orders_after_first = runtime_a.order_manager.order_count
    cash_after_first = runtime_a.portfolio.cash
    assert orders_after_first >= 1
    assert cash_after_first < cash_before

    runtime_b = _runtime(
        bars=[bar],
        now=now,
        action=SignalAction.BUY,
        portfolio=Portfolio(cash=Decimal("100000")),
        order_manager=OrderManager(),
    )
    result_b = _paper_operator(
        runtime_b,
        settings=_settings(),
        kill_switch=FileEnvKillSwitch(tmp_path / "KILL"),
        clock=lambda: now + timedelta(minutes=1),
    ).run(_config(state_path, max_cycles=1))

    assert result_b.resumed is True
    assert result_b.results[0].success is True
    assert result_b.results[0].intent is None
    assert result_b.results[0].order is None
    assert result_b.results[0].execution is None
    assert result_b.results[0].market_bar_timestamp == bar_ts
    assert result_b.results[0].risk_evaluation is not None  # risk still ran
    assert runtime_b.market_data.get_bars.call_count == 1  # one fetch in run_once
    # Restored book from first run; no additional order / no further cash drain.
    assert runtime_b.order_manager.order_count == orders_after_first
    assert runtime_b.portfolio.cash == cash_after_first


def test_corrupt_state_refuses_start_without_empty_portfolio_fallback(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.delenv("PAPER_OPERATOR_KILL", raising=False)
    state_path = tmp_path / "state.json"
    state_path.write_text("{bad", encoding="utf-8")
    portfolio = Portfolio(cash=Decimal("100000"))
    runtime = _runtime(
        bars=[_bar(timestamp=_utc(2026, 7, 15, 10, 0))],
        now=_utc(2026, 7, 15, 11, 0),
        portfolio=portfolio,
    )
    with pytest.raises(ConfigurationError, match="corrupt"):
        _paper_operator(
            runtime,
            settings=_settings(),
            kill_switch=FileEnvKillSwitch(tmp_path / "KILL"),
        ).run(_config(state_path, max_cycles=1))
    assert portfolio.cash == Decimal("100000")
    assert portfolio.position_count == 0


def test_kill_with_persisted_state_stops_and_keeps_baseline(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.delenv("PAPER_OPERATOR_KILL", raising=False)
    now = _utc(2026, 7, 15, 11, 0)
    bar = _bar(timestamp=_utc(2026, 7, 15, 10, 0))
    state_path = tmp_path / "state.json"
    runtime = _runtime(bars=[bar], now=now, portfolio=Portfolio(cash=Decimal("100000")))
    # pre-start False, before cycle1 False, before cycle2 True
    kill = _SequenceKillSwitch([False, False, True])
    result = _paper_operator(
        runtime,
        settings=_settings(),
        kill_switch=kill,
        clock=lambda: now,
    ).run(_config(state_path, max_cycles=5))
    assert result.cycles_executed == 1
    assert result.kill_engaged is True
    loaded = JsonPaperStateStore(state_path).load()
    assert loaded.cycles_completed_total == 1
    assert loaded.operator_start_equity == Decimal("100000")


def test_max_wall_time_with_state_persists(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("PAPER_OPERATOR_KILL", raising=False)
    start = _utc(2026, 7, 15, 11, 0)
    bar = _bar(timestamp=_utc(2026, 7, 15, 10, 0))
    state_path = tmp_path / "state.json"

    class _Clock:
        def __init__(self) -> None:
            self.calls = 0

        def __call__(self) -> datetime:
            self.calls += 1
            if self.calls <= 2:
                return start
            return start + timedelta(seconds=100)

    runtime = _runtime(
        bars=[bar],
        now=start,
        portfolio=Portfolio(cash=Decimal("100000")),
    )
    # Inject same clock into runtime for freshness consistency on first cycle.
    runtime._clock = lambda: start  # noqa: SLF001
    result = _paper_operator(
        runtime,
        settings=_settings(),
        kill_switch=FileEnvKillSwitch(tmp_path / "KILL"),
        clock=_Clock(),
    ).run(_config(state_path, max_cycles=5, max_wall_time_seconds=50.0))
    assert result.bound_reached == "max_wall_time"
    assert result.cycles_executed == 1
    assert JsonPaperStateStore(state_path).load().cycles_completed_total == 1


def test_session_runner_unchanged_with_market_hours_abort() -> None:
    class _FakePortfolio:
        def __init__(self) -> None:
            self.total_value = Decimal("100000")

    class _Runtime:
        def __init__(self) -> None:
            self.portfolio = _FakePortfolio()
            self.run_once_calls = 0

        def run_once(self, context: RuntimeContext) -> PipelineResult:
            self.run_once_calls += 1
            return PipelineResult(
                success=False,
                stage_reached="market_hours",
                aborted_reason="closed",
            )

    runtime = _Runtime()
    result = SessionRunner(runtime).run(SessionConfig(cycles=3, symbol="AAPL"))  # type: ignore[arg-type]
    assert runtime.run_once_calls == 1
    assert result.stopped_early is True


def test_factory_live_still_rejected() -> None:
    with pytest.raises(ConfigurationError, match="live"):
        create_trading_runtime(
            _settings(trading_mode="live"),
            execution="dry_run",
            market_data=MagicMock(),
            strategy_engine=MagicMock(),
            portfolio=Portfolio(cash=Decimal("1")),
            with_alerts=False,
        )


def test_m10_backtest_isolation_untouched() -> None:
    from backtesting.runner import BacktestRunner

    assert BacktestRunner is not None


def test_e1_post_risk_skip_uses_authoritative_processed_bar() -> None:
    now = _utc(2026, 7, 15, 11, 0)
    bar_ts = _utc(2026, 7, 15, 10, 0)
    bar = _bar(timestamp=bar_ts)
    runtime = _runtime(
        bars=[bar],
        now=now,
        action=SignalAction.BUY,
        portfolio=Portfolio(cash=Decimal("100000")),
    )
    from runtime.paper_state import normalize_bar_timestamp

    marker = normalize_bar_timestamp(bar_ts)
    result = runtime.run_once(
        RuntimeContext(
            symbol="AAPL",
            mode=TradingMode.PAPER,
            daily_pnl_pct=Decimal("0"),
            already_actioned_bar_timestamp=marker,
        )
    )
    assert result.success is True
    assert result.market_bar_timestamp == bar_ts
    assert result.risk_evaluation is not None
    assert result.intent is None
    assert result.order is None
    assert result.execution is None
    assert runtime.order_manager.order_count == 0
    assert runtime.portfolio.cash == Decimal("100000")
    assert runtime.market_data.get_bars.call_count == 1


def test_e1_newer_bar_remains_actionable(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("PAPER_OPERATOR_KILL", raising=False)
    now = _utc(2026, 7, 15, 11, 0)
    old_bar = _bar(timestamp=_utc(2026, 7, 15, 9, 0))
    new_bar = _bar(timestamp=_utc(2026, 7, 15, 10, 0))
    state_path = tmp_path / "state.json"

    runtime_a = _runtime(
        bars=[old_bar],
        now=now,
        action=SignalAction.BUY,
        portfolio=Portfolio(cash=Decimal("100000")),
    )
    _paper_operator(
        runtime_a,
        settings=_settings(),
        kill_switch=FileEnvKillSwitch(tmp_path / "KILL"),
        clock=lambda: now,
    ).run(_config(state_path, max_cycles=1))

    runtime_b = _runtime(
        bars=[new_bar],
        now=now,
        action=SignalAction.BUY,
        portfolio=Portfolio(cash=Decimal("100000")),
        order_manager=OrderManager(),
    )
    result_b = _paper_operator(
        runtime_b,
        settings=_settings(),
        kill_switch=FileEnvKillSwitch(tmp_path / "KILL"),
        clock=lambda: now + timedelta(minutes=1),
    ).run(_config(state_path, max_cycles=1))

    assert result_b.resumed is True
    assert result_b.results[0].order is not None
    assert result_b.results[0].market_bar_timestamp == new_bar.timestamp


def test_e1_hold_does_not_consume_actionable_cursor(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.delenv("PAPER_OPERATOR_KILL", raising=False)
    now = _utc(2026, 7, 15, 11, 0)
    bar = _bar(timestamp=_utc(2026, 7, 15, 10, 0))
    state_path = tmp_path / "state.json"

    runtime_hold = _runtime(
        bars=[bar],
        now=now,
        action=SignalAction.HOLD,
        portfolio=Portfolio(cash=Decimal("100000")),
    )
    _paper_operator(
        runtime_hold,
        settings=_settings(),
        kill_switch=FileEnvKillSwitch(tmp_path / "KILL"),
        clock=lambda: now,
    ).run(_config(state_path, max_cycles=1))
    loaded = JsonPaperStateStore(state_path).load()
    assert loaded.last_actionable_bar_timestamps == {}

    runtime_buy = _runtime(
        bars=[bar],
        now=now,
        action=SignalAction.BUY,
        portfolio=Portfolio(cash=Decimal("100000")),
    )
    result_buy = _paper_operator(
        runtime_buy,
        settings=_settings(),
        kill_switch=FileEnvKillSwitch(tmp_path / "KILL"),
        clock=lambda: now + timedelta(minutes=1),
    ).run(_config(state_path, max_cycles=1))
    assert result_buy.results[0].order is not None


def test_e1_risk_still_fail_closes_on_duplicate_bar_marker() -> None:
    """Risk runs before E1 side-effect skip; loss limit still aborts."""
    now = _utc(2026, 7, 15, 11, 0)
    bar_ts = _utc(2026, 7, 15, 10, 0)
    bar = _bar(timestamp=bar_ts)
    settings = _settings(max_daily_loss_pct=Decimal("0.01"))
    runtime = _runtime(
        bars=[bar],
        now=now,
        action=SignalAction.BUY,
        portfolio=Portfolio(cash=Decimal("100000")),
        settings=settings,
    )
    from runtime.paper_state import normalize_bar_timestamp

    result = runtime.run_once(
        RuntimeContext(
            symbol="AAPL",
            mode=TradingMode.PAPER,
            daily_pnl_pct=Decimal("-0.05"),
            already_actioned_bar_timestamp=normalize_bar_timestamp(bar_ts),
        )
    )
    assert result.success is False
    assert result.stage_reached == "risk"
    assert result.intent is None
    assert result.order is None
    assert result.market_bar_timestamp == bar_ts


def test_e1_default_context_has_no_side_effect_skip() -> None:
    now = _utc(2026, 7, 15, 11, 0)
    bar = _bar(timestamp=_utc(2026, 7, 15, 10, 0))
    runtime = _runtime(
        bars=[bar],
        now=now,
        action=SignalAction.BUY,
        portfolio=Portfolio(cash=Decimal("100000")),
    )
    result = runtime.run_once(
        RuntimeContext(
            symbol="AAPL",
            mode=TradingMode.PAPER,
            daily_pnl_pct=Decimal("0"),
        )
    )
    assert result.success is True
    assert result.order is not None
    assert result.market_bar_timestamp == bar.timestamp
