"""M12.1 PaperOperator bounds, kill, and A2/B1 safety tests (no network)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from config.settings import Settings
from core.exceptions import ConfigurationError
from core.types import TradingMode
from runtime.context import RuntimeContext
from runtime.kill_switch import FileEnvKillSwitch
from runtime.models import PipelineResult
from runtime.paper_operator import (
    MAX_OPERATOR_CYCLES,
    PaperOperator,
    PaperOperatorConfig,
)
from runtime.session import SessionConfig, SessionRunner


class _FakePortfolio:
    def __init__(self, total_value: Decimal) -> None:
        self.total_value = total_value


class _RecordingRuntime:
    def __init__(
        self,
        portfolio: _FakePortfolio,
        outcomes: list[PipelineResult] | None = None,
    ) -> None:
        self.portfolio = portfolio
        self.contexts: list[RuntimeContext] = []
        self._outcomes = list(outcomes or [])
        self.run_once_calls = 0

    def run_once(self, context: RuntimeContext) -> PipelineResult:
        self.run_once_calls += 1
        self.contexts.append(context)
        if self._outcomes:
            return self._outcomes.pop(0)
        return PipelineResult(success=True, stage_reached="portfolio")


class _SequenceKillSwitch:
    """Return successive engagement flags; repeat the last value when exhausted."""

    def __init__(self, sequence: list[bool]) -> None:
        if not sequence:
            raise ValueError("sequence must be non-empty")
        self._sequence = list(sequence)
        self.checks = 0

    def is_engaged(self) -> bool:
        idx = min(self.checks, len(self._sequence) - 1)
        self.checks += 1
        return self._sequence[idx]


class _FakeClock:
    def __init__(self, start: datetime) -> None:
        self._current = start

    def __call__(self) -> datetime:
        return self._current

    def advance(self, seconds: float) -> None:
        self._current = self._current + timedelta(seconds=seconds)


def _ok(stage: str = "portfolio") -> PipelineResult:
    return PipelineResult(success=True, stage_reached=stage)


def _abort(reason: str, stage: str) -> PipelineResult:
    return PipelineResult(
        success=False,
        stage_reached=stage,
        aborted_reason=reason,
    )


def _settings(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "trading_mode": "paper",
        "market_data_provider": "mock",
        "market_hours_enabled": True,
        "market_hours_policy": "reject",
        "alerts_enabled": False,
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


def _config(**overrides: object) -> PaperOperatorConfig:
    base: dict[str, object] = {
        "symbol": "AAPL",
        "max_cycles": 3,
        "max_wall_time_seconds": 3600.0,
        "interval_seconds": 1.0,
        "execution": "dry_run",
    }
    base.update(overrides)
    return PaperOperatorConfig(**base)  # type: ignore[arg-type]


def _operator(
    runtime: _RecordingRuntime,
    *,
    settings: Settings | None = None,
    kill_switch: object | None = None,
    clock: object | None = None,
    tmp_path: Path | None = None,
) -> PaperOperator:
    if kill_switch is None:
        assert tmp_path is not None
        kill_switch = FileEnvKillSwitch(tmp_path / "KILL")
    return PaperOperator(
        runtime,  # type: ignore[arg-type]
        settings=settings or _settings(),
        kill_switch=kill_switch,  # type: ignore[arg-type]
        clock=clock,  # type: ignore[arg-type]
    )


# --- config / bounds validation -------------------------------------------------


@pytest.mark.parametrize(
    "kwargs, match",
    [
        ({"max_cycles": 0}, "max_cycles"),
        ({"max_cycles": MAX_OPERATOR_CYCLES + 1}, "max_cycles"),
        ({"max_cycles": True}, "max_cycles"),
        ({"max_wall_time_seconds": 0}, "max_wall_time_seconds"),
        ({"max_wall_time_seconds": -1}, "max_wall_time_seconds"),
        ({"max_wall_time_seconds": float("nan")}, "max_wall_time_seconds"),
        ({"interval_seconds": 0}, "interval_seconds"),
        ({"interval_seconds": -5}, "interval_seconds"),
        ({"interval_seconds": float("inf")}, "interval_seconds"),
        ({"symbol": ""}, "symbol"),
        ({"bar_limit": 0}, "bar_limit"),
        ({"execution": "live"}, "execution"),
    ],
)
def test_invalid_bounds_refuse_start(
    tmp_path: Path, kwargs: dict, match: str, monkeypatch
) -> None:
    monkeypatch.delenv("PAPER_OPERATOR_KILL", raising=False)
    op = _operator(_RecordingRuntime(_FakePortfolio(Decimal("100000"))), tmp_path=tmp_path)
    with pytest.raises(ConfigurationError, match=match):
        op.run(_config(**kwargs))


def test_missing_required_bounds_have_no_unbounded_defaults() -> None:
    with pytest.raises(TypeError):
        PaperOperatorConfig(symbol="AAPL")  # type: ignore[call-arg]


# --- B1 hours profile -----------------------------------------------------------


def test_hours_disabled_refuses_start(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("PAPER_OPERATOR_KILL", raising=False)
    op = _operator(
        _RecordingRuntime(_FakePortfolio(Decimal("100000"))),
        settings=_settings(market_hours_enabled=False),
        tmp_path=tmp_path,
    )
    with pytest.raises(ConfigurationError, match="MARKET_HOURS_ENABLED"):
        op.run(_config())


def test_hours_policy_allow_refuses_start(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("PAPER_OPERATOR_KILL", raising=False)
    op = _operator(
        _RecordingRuntime(_FakePortfolio(Decimal("100000"))),
        settings=_settings(market_hours_policy="allow"),
        tmp_path=tmp_path,
    )
    with pytest.raises(ConfigurationError, match="MARKET_HOURS_POLICY"):
        op.run(_config())


def test_live_trading_mode_refuses_start(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("PAPER_OPERATOR_KILL", raising=False)
    op = _operator(
        _RecordingRuntime(_FakePortfolio(Decimal("100000"))),
        settings=_settings(trading_mode="live"),
        tmp_path=tmp_path,
    )
    with pytest.raises(ConfigurationError, match="trading_mode='paper'"):
        op.run(_config())


# --- kill switch ----------------------------------------------------------------


def test_kill_engaged_before_start_refuses(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.delenv("PAPER_OPERATOR_KILL", raising=False)
    kill_path = tmp_path / "KILL"
    kill_path.write_text("stop", encoding="utf-8")
    runtime = _RecordingRuntime(_FakePortfolio(Decimal("100000")))
    op = _operator(runtime, kill_switch=FileEnvKillSwitch(kill_path))
    with pytest.raises(ConfigurationError, match="kill switch"):
        op.run(_config())
    assert runtime.run_once_calls == 0


def test_kill_engaged_between_cycles_stops_before_next_run_once(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.delenv("PAPER_OPERATOR_KILL", raising=False)
    # Check order: pre-start, before cycle 1, before cycle 2.
    kill = _SequenceKillSwitch([False, False, True])
    runtime = _RecordingRuntime(
        _FakePortfolio(Decimal("100000")),
        outcomes=[_ok()],
    )
    op = _operator(runtime, kill_switch=kill)
    result = op.run(_config(max_cycles=5))

    assert runtime.run_once_calls == 1
    assert result.cycles_executed == 1
    assert result.stopped_early is True
    assert result.kill_engaged is True
    assert result.stop_reason == "kill_switch_engaged"
    assert result.bound_reached is None


# --- hard bounds ----------------------------------------------------------------


def test_max_cycles_stops(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("PAPER_OPERATOR_KILL", raising=False)
    runtime = _RecordingRuntime(_FakePortfolio(Decimal("100000")))
    result = _operator(runtime, tmp_path=tmp_path).run(_config(max_cycles=3))

    assert runtime.run_once_calls == 3
    assert result.cycles_executed == 3
    assert result.stopped_early is False
    assert result.completed_all_cycles is True
    assert result.bound_reached == "max_cycles"
    assert result.kill_engaged is False


def test_max_wall_time_stops_before_run_once(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.delenv("PAPER_OPERATOR_KILL", raising=False)
    start = datetime(2026, 7, 15, 14, 0, tzinfo=timezone.utc)
    clock = _FakeClock(start)

    class _AdvancingKill:
        def is_engaged(self) -> bool:
            return False

    runtime = _RecordingRuntime(_FakePortfolio(Decimal("100000")))

    # Advance clock past wall budget before/at first bound check by wrapping run:
    # started_at is captured at run start; advance before first loop iteration via
    # a clock that jumps after start capture. Use a two-phase clock.
    class _JumpClock:
        def __init__(self) -> None:
            self.calls = 0
            self._start = start

        def __call__(self) -> datetime:
            self.calls += 1
            # First call: operator start timestamp.
            if self.calls == 1:
                return self._start
            # Subsequent calls (wall checks): already over budget.
            return self._start + timedelta(seconds=100)

    result = _operator(
        runtime,
        kill_switch=_AdvancingKill(),
        clock=_JumpClock(),
    ).run(_config(max_cycles=5, max_wall_time_seconds=10.0))

    assert runtime.run_once_calls == 0
    assert result.cycles_executed == 0
    assert result.stopped_early is True
    assert result.bound_reached == "max_wall_time"
    assert "max_wall_time" in (result.stop_reason or "")


def test_max_wall_time_stops_between_cycles(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.delenv("PAPER_OPERATOR_KILL", raising=False)
    start = datetime(2026, 7, 15, 14, 0, tzinfo=timezone.utc)

    class _Clock:
        def __init__(self) -> None:
            self.calls = 0

        def __call__(self) -> datetime:
            self.calls += 1
            # call 1: start; call 2: first wall check (ok); call 3: second wall check (over)
            if self.calls <= 2:
                return start
            return start + timedelta(seconds=100)

    runtime = _RecordingRuntime(
        _FakePortfolio(Decimal("100000")),
        outcomes=[_ok()],
    )
    result = _operator(
        runtime,
        kill_switch=FileEnvKillSwitch(tmp_path / "missing"),
        clock=_Clock(),
    ).run(_config(max_cycles=5, max_wall_time_seconds=50.0))

    assert runtime.run_once_calls == 1
    assert result.cycles_executed == 1
    assert result.stopped_early is True
    assert result.bound_reached == "max_wall_time"


# --- A2 continue-on / hard-stop -------------------------------------------------


def test_market_hours_abort_continues_until_max_cycles(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.delenv("PAPER_OPERATOR_KILL", raising=False)
    runtime = _RecordingRuntime(
        _FakePortfolio(Decimal("100000")),
        outcomes=[
            _abort("market closed", "market_hours"),
            _abort("market closed", "market_hours"),
            _ok(),
        ],
    )
    result = _operator(runtime, tmp_path=tmp_path).run(_config(max_cycles=3))

    assert runtime.run_once_calls == 3
    assert result.cycles_executed == 3
    assert result.stopped_early is False
    assert result.results[0].stage_reached == "market_hours"
    assert result.results[1].stage_reached == "market_hours"
    assert result.results[2].success is True


def test_freshness_abort_hard_stops(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("PAPER_OPERATOR_KILL", raising=False)
    runtime = _RecordingRuntime(
        _FakePortfolio(Decimal("100000")),
        outcomes=[
            _abort("stale market data", "market_data"),
            _ok(),
        ],
    )
    result = _operator(runtime, tmp_path=tmp_path).run(_config(max_cycles=5))

    assert runtime.run_once_calls == 1
    assert result.cycles_executed == 1
    assert result.stopped_early is True
    assert "stale" in (result.stop_reason or "")
    assert result.bound_reached is None


def test_mode_abort_hard_stops(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("PAPER_OPERATOR_KILL", raising=False)
    runtime = _RecordingRuntime(
        _FakePortfolio(Decimal("100000")),
        outcomes=[_abort("live not allowed", "mode"), _ok()],
    )
    result = _operator(runtime, tmp_path=tmp_path).run(_config(max_cycles=3))

    assert runtime.run_once_calls == 1
    assert result.stopped_early is True
    assert result.results[0].stage_reached == "mode"


def test_risk_abort_hard_stops(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("PAPER_OPERATOR_KILL", raising=False)
    runtime = _RecordingRuntime(
        _FakePortfolio(Decimal("100000")),
        outcomes=[_abort("daily loss limit", "risk"), _ok()],
    )
    result = _operator(runtime, tmp_path=tmp_path).run(_config(max_cycles=3))

    assert runtime.run_once_calls == 1
    assert result.stopped_early is True
    assert result.results[0].stage_reached == "risk"


def test_operator_maps_pnl_from_operator_start_equity(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.delenv("PAPER_OPERATOR_KILL", raising=False)
    portfolio = _FakePortfolio(Decimal("100000"))
    runtime = _RecordingRuntime(portfolio, outcomes=[_ok(), _ok()])

    def _run_once(context: RuntimeContext) -> PipelineResult:
        runtime.run_once_calls += 1
        runtime.contexts.append(context)
        if runtime.run_once_calls == 1:
            portfolio.total_value = Decimal("90000")
        return _ok()

    runtime.run_once = _run_once  # type: ignore[method-assign]
    _operator(runtime, tmp_path=tmp_path).run(_config(max_cycles=2))

    assert runtime.contexts[0].daily_pnl_pct == Decimal("0")
    assert runtime.contexts[0].mode is TradingMode.PAPER
    assert runtime.contexts[1].daily_pnl_pct == Decimal("-0.1")


# --- SessionRunner regression (unchanged fail-closed) ---------------------------


def test_session_runner_still_stops_on_market_hours_abort() -> None:
    """M9 SessionRunner must NOT adopt operator continue-on semantics."""
    runtime = _RecordingRuntime(
        _FakePortfolio(Decimal("100000")),
        outcomes=[
            _abort("market closed", "market_hours"),
            _ok(),
        ],
    )
    result = SessionRunner(runtime).run(  # type: ignore[arg-type]
        SessionConfig(cycles=3, symbol="AAPL")
    )
    assert runtime.run_once_calls == 1
    assert result.stopped_early is True
    assert result.cycles_executed == 1


def test_m12_1_does_not_sleep_between_cycles(
    tmp_path: Path, monkeypatch
) -> None:
    """Interval is validated but M12.1 must not sleep (M12.3 owns sleeping)."""
    monkeypatch.delenv("PAPER_OPERATOR_KILL", raising=False)
    sleep = MagicMock()
    monkeypatch.setattr("time.sleep", sleep)
    runtime = _RecordingRuntime(_FakePortfolio(Decimal("100000")))
    _operator(runtime, tmp_path=tmp_path).run(
        _config(max_cycles=3, interval_seconds=30.0)
    )
    sleep.assert_not_called()
