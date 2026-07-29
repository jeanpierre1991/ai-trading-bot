"""M9.1 SessionRunner unit tests (no network)."""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from core.exceptions import ConfigurationError
from core.types import TradingMode
from runtime.context import RuntimeContext
from runtime.models import PipelineResult
from runtime.session import (
    MAX_SESSION_CYCLES,
    SessionConfig,
    SessionResult,
    SessionRunner,
    compute_session_pnl_pct,
)


class _FakePortfolio:
    def __init__(self, total_value: Decimal) -> None:
        self.total_value = total_value


class _RecordingRuntime:
    """Minimal TradingRuntime stand-in — no network, no pipeline logic."""

    def __init__(
        self,
        portfolio: _FakePortfolio,
        outcomes: list[PipelineResult] | None = None,
    ) -> None:
        self.portfolio = portfolio
        self.order_manager = object()
        self.contexts: list[RuntimeContext] = []
        self._outcomes = list(outcomes or [])
        self.run_once_calls = 0

    def run_once(self, context: RuntimeContext) -> PipelineResult:
        self.run_once_calls += 1
        self.contexts.append(context)
        if self._outcomes:
            return self._outcomes.pop(0)
        return PipelineResult(success=True, stage_reached="portfolio")


def _ok(stage: str = "portfolio") -> PipelineResult:
    return PipelineResult(success=True, stage_reached=stage)


def _abort(reason: str, stage: str = "risk") -> PipelineResult:
    return PipelineResult(
        success=False,
        stage_reached=stage,
        aborted_reason=reason,
    )


def test_compute_session_pnl_pct_basic() -> None:
    assert compute_session_pnl_pct(Decimal("100"), Decimal("100")) == Decimal("0")
    assert compute_session_pnl_pct(Decimal("100"), Decimal("90")) == Decimal("-0.1")
    assert compute_session_pnl_pct(Decimal("100"), Decimal("110")) == Decimal("0.1")


def test_compute_session_pnl_pct_rejects_non_positive_start() -> None:
    with pytest.raises(ConfigurationError, match="session_start_equity"):
        compute_session_pnl_pct(Decimal("0"), Decimal("10"))


def test_cycles_one_parity_single_run_once() -> None:
    portfolio = _FakePortfolio(Decimal("100000"))
    runtime = _RecordingRuntime(portfolio)
    runner = SessionRunner(runtime)

    result = runner.run(SessionConfig(cycles=1, symbol="AAPL"))

    assert runtime.run_once_calls == 1
    assert result.cycles_requested == 1
    assert result.cycles_executed == 1
    assert result.stopped_early is False
    assert result.stop_reason is None
    assert result.completed_all_cycles is True
    assert len(result.results) == 1
    assert result.results[0].success is True

    context = runtime.contexts[0]
    assert context.symbol == "AAPL"
    assert context.mode is TradingMode.PAPER
    assert context.daily_pnl_pct == Decimal("0")
    assert isinstance(context.daily_pnl_pct, Decimal)


def test_n_cycles_share_exact_same_runtime_and_portfolio() -> None:
    portfolio = _FakePortfolio(Decimal("50000"))
    runtime = _RecordingRuntime(portfolio, outcomes=[_ok(), _ok(), _ok()])
    runner = SessionRunner(runtime)

    result = runner.run(SessionConfig(cycles=3, symbol="MSFT"))

    assert runtime.run_once_calls == 3
    assert result.cycles_executed == 3
    assert runner.runtime is runtime
    assert runtime.portfolio is portfolio
    # Same portfolio object observed across the session
    assert all(runtime.portfolio is portfolio for _ in range(3))


def test_daily_pnl_pct_is_decimal_and_starts_at_zero() -> None:
    portfolio = _FakePortfolio(Decimal("1000"))
    runtime = _RecordingRuntime(portfolio, outcomes=[_ok(), _ok()])
    SessionRunner(runtime).run(SessionConfig(cycles=2, symbol="AAPL"))

    first = runtime.contexts[0].daily_pnl_pct
    assert isinstance(first, Decimal)
    assert first == Decimal("0")


def test_session_pnl_updates_when_equity_changes() -> None:
    portfolio = _FakePortfolio(Decimal("1000"))
    runtime = _RecordingRuntime(portfolio)

    def _run_once(context: RuntimeContext) -> PipelineResult:
        runtime.run_once_calls += 1
        runtime.contexts.append(context)
        # After first successful cycle, equity drops 10%
        if runtime.run_once_calls == 1:
            portfolio.total_value = Decimal("900")
            return _ok()
        return _ok()

    runtime.run_once = _run_once  # type: ignore[method-assign]

    SessionRunner(runtime).run(SessionConfig(cycles=2, symbol="AAPL"))

    assert runtime.contexts[0].daily_pnl_pct == Decimal("0")
    assert runtime.contexts[1].daily_pnl_pct == Decimal("-0.1")


def test_success_false_stops_before_next_cycle_and_preserves_partials() -> None:
    portfolio = _FakePortfolio(Decimal("100000"))
    runtime = _RecordingRuntime(
        portfolio,
        outcomes=[
            _ok(),
            _abort("max_daily_loss_pct exceeded"),
            _ok(),  # must never be consumed
        ],
    )
    result = SessionRunner(runtime).run(SessionConfig(cycles=5, symbol="AAPL"))

    assert runtime.run_once_calls == 2
    assert result.cycles_requested == 5
    assert result.cycles_executed == 2
    assert result.stopped_early is True
    assert result.stop_reason == "max_daily_loss_pct exceeded"
    assert result.completed_all_cycles is False
    assert len(result.results) == 2
    assert result.results[0].success is True
    assert result.results[1].success is False


def test_invalid_cycles_execute_zero_cycles() -> None:
    portfolio = _FakePortfolio(Decimal("100000"))
    runtime = _RecordingRuntime(portfolio)
    runner = SessionRunner(runtime)

    with pytest.raises(ConfigurationError, match="cycles"):
        runner.run(SessionConfig(cycles=0, symbol="AAPL"))
    assert runtime.run_once_calls == 0

    with pytest.raises(ConfigurationError, match="cycles"):
        runner.run(SessionConfig(cycles=-1, symbol="AAPL"))
    assert runtime.run_once_calls == 0

    with pytest.raises(ConfigurationError, match="cycles"):
        runner.run(SessionConfig(cycles=MAX_SESSION_CYCLES + 1, symbol="AAPL"))
    assert runtime.run_once_calls == 0


def test_non_positive_starting_equity_executes_zero_cycles() -> None:
    portfolio = _FakePortfolio(Decimal("0"))
    runtime = _RecordingRuntime(portfolio)
    runner = SessionRunner(runtime)

    with pytest.raises(ConfigurationError, match="session_start_equity"):
        runner.run(SessionConfig(cycles=3, symbol="AAPL"))
    assert runtime.run_once_calls == 0

    portfolio.total_value = Decimal("-1")
    with pytest.raises(ConfigurationError, match="session_start_equity"):
        runner.run(SessionConfig(cycles=1, symbol="AAPL"))
    assert runtime.run_once_calls == 0


def test_session_result_records_equity_bounds() -> None:
    portfolio = _FakePortfolio(Decimal("200"))
    runtime = _RecordingRuntime(portfolio)

    def _run_once(context: RuntimeContext) -> PipelineResult:
        runtime.run_once_calls += 1
        runtime.contexts.append(context)
        portfolio.total_value = Decimal("250")
        return _ok()

    runtime.run_once = _run_once  # type: ignore[method-assign]
    result = SessionRunner(runtime).run(SessionConfig(cycles=1, symbol="AAPL"))

    assert result.session_start_equity == Decimal("200")
    assert result.session_end_equity == Decimal("250")
    assert isinstance(result, SessionResult)


def test_strategy_and_bar_limit_forwarded() -> None:
    portfolio = _FakePortfolio(Decimal("100"))
    runtime = _RecordingRuntime(portfolio)
    SessionRunner(runtime).run(
        SessionConfig(
            cycles=1,
            symbol="AAPL",
            strategy_name="ema_crossover",
            bar_limit=50,
        )
    )
    ctx = runtime.contexts[0]
    assert ctx.strategy_name == "ema_crossover"
    assert ctx.bar_limit == 50


def test_runtime_without_portfolio_rejected() -> None:
    runtime = MagicMock(spec=["run_once"])
    # no portfolio attribute
    with pytest.raises(ConfigurationError, match="portfolio"):
        SessionRunner(runtime).run(SessionConfig(cycles=1, symbol="AAPL"))
