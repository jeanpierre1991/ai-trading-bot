"""M12.3 PaperOperator interval sleeper tests (no real sleeping)."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from config.settings import Settings
from runtime.context import RuntimeContext
from runtime.kill_switch import FileEnvKillSwitch
from runtime.models import PipelineResult
from runtime.paper_operator import PaperOperator, PaperOperatorConfig


@pytest.fixture(autouse=True)
def _forbid_real_operator_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("real time.sleep invoked in operator tests")

    monkeypatch.setattr("runtime.paper_operator.time.sleep", _boom)


class _FakePortfolio:
    def __init__(self, total_value: Decimal) -> None:
        self.total_value = total_value


class _RecordingRuntime:
    def __init__(self, portfolio: _FakePortfolio) -> None:
        self.portfolio = portfolio
        self.run_once_calls = 0
        self.contexts: list[RuntimeContext] = []

    def run_once(self, context: RuntimeContext) -> PipelineResult:
        self.run_once_calls += 1
        self.contexts.append(context)
        return PipelineResult(success=True, stage_reached="portfolio")


def _settings() -> Settings:
    return Settings(
        trading_mode="paper",
        market_data_provider="mock",
        market_hours_enabled=True,
        market_hours_policy="reject",
        alerts_enabled=False,
    )


def test_interval_sleep_between_successful_cycles_only(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.delenv("PAPER_OPERATOR_KILL", raising=False)
    sleeper = MagicMock()
    runtime = _RecordingRuntime(_FakePortfolio(Decimal("100000")))
    PaperOperator(
        runtime,  # type: ignore[arg-type]
        settings=_settings(),
        kill_switch=FileEnvKillSwitch(tmp_path / "KILL"),
        sleeper=sleeper,
    ).run(
        PaperOperatorConfig(
            symbol="AAPL",
            max_cycles=3,
            max_wall_time_seconds=3600.0,
            interval_seconds=12.5,
        )
    )
    assert runtime.run_once_calls == 3
    assert sleeper.call_count == 2
    sleeper.assert_called_with(12.5)


def test_no_interval_sleep_after_hard_stop(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("PAPER_OPERATOR_KILL", raising=False)
    sleeper = MagicMock()

    class _Runtime:
        def __init__(self) -> None:
            self.portfolio = _FakePortfolio(Decimal("100000"))
            self.calls = 0

        def run_once(self, context: RuntimeContext) -> PipelineResult:
            self.calls += 1
            return PipelineResult(
                success=False,
                stage_reached="risk",
                aborted_reason="daily loss",
            )

    runtime = _Runtime()
    PaperOperator(
        runtime,  # type: ignore[arg-type]
        settings=_settings(),
        kill_switch=FileEnvKillSwitch(tmp_path / "KILL"),
        sleeper=sleeper,
    ).run(
        PaperOperatorConfig(
            symbol="AAPL",
            max_cycles=5,
            max_wall_time_seconds=3600.0,
            interval_seconds=9.0,
        )
    )
    assert runtime.calls == 1
    sleeper.assert_not_called()


def test_market_hours_continue_on_still_sleeps_between_cycles(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.delenv("PAPER_OPERATOR_KILL", raising=False)
    sleeper = MagicMock()

    class _Runtime:
        def __init__(self) -> None:
            self.portfolio = _FakePortfolio(Decimal("100000"))
            self.calls = 0

        def run_once(self, context: RuntimeContext) -> PipelineResult:
            self.calls += 1
            return PipelineResult(
                success=False,
                stage_reached="market_hours",
                aborted_reason="closed",
            )

    runtime = _Runtime()
    PaperOperator(
        runtime,  # type: ignore[arg-type]
        settings=_settings(),
        kill_switch=FileEnvKillSwitch(tmp_path / "KILL"),
        sleeper=sleeper,
    ).run(
        PaperOperatorConfig(
            symbol="AAPL",
            max_cycles=3,
            max_wall_time_seconds=3600.0,
            interval_seconds=1.0,
        )
    )
    assert runtime.calls == 3
    assert sleeper.call_count == 2


def test_no_interval_sleep_after_mid_run_kill(
    tmp_path: Path, monkeypatch
) -> None:
    """M3: kill stop must not sleep after the terminating kill condition."""
    monkeypatch.delenv("PAPER_OPERATOR_KILL", raising=False)
    sleeper = MagicMock()

    class _SequenceKill:
        """pre-start False, cycle0 False, cycle1 True (after one success + sleep)."""

        def __init__(self) -> None:
            self.checks = 0

        def is_engaged(self) -> bool:
            # PaperOperator checks kill before start, then before each cycle.
            seq = [False, False, True]
            idx = min(self.checks, len(seq) - 1)
            self.checks += 1
            return seq[idx]

    class _Runtime:
        def __init__(self) -> None:
            self.portfolio = _FakePortfolio(Decimal("100000"))
            self.calls = 0

        def run_once(self, context: RuntimeContext) -> PipelineResult:
            self.calls += 1
            return PipelineResult(success=True, stage_reached="portfolio")

    runtime = _Runtime()
    result = PaperOperator(
        runtime,  # type: ignore[arg-type]
        settings=_settings(),
        kill_switch=_SequenceKill(),  # type: ignore[arg-type]
        sleeper=sleeper,
    ).run(
        PaperOperatorConfig(
            symbol="AAPL",
            max_cycles=5,
            max_wall_time_seconds=3600.0,
            interval_seconds=9.0,
        )
    )
    assert runtime.calls == 1
    assert result.kill_engaged is True
    assert result.stop_reason == "kill_switch_engaged"
    # One sleep may occur after the successful cycle before the next kill check;
    # no additional sleep may occur after kill terminates the loop.
    assert sleeper.call_count == 1
    sleeper.assert_called_with(9.0)
