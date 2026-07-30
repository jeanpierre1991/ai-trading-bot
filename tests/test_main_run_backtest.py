"""M10.4 CLI smoke tests for main.py run-backtest (deterministic, no network)."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

import main as main_module
from backtesting.commission import CommissionDryRunExecutor
from backtesting.engine import BacktestResult
from backtesting.runner import MAX_BACKTEST_CYCLES
from config.settings import Settings
from core.types import SignalAction
from market_data.historical_provider import MAX_HISTORICAL_BARS
from runtime.broker_executor import BrokerOrderExecutor
from runtime.dry_run import DryRunExecutor
from runtime.trading_runtime import BasicTradingRuntime
from strategy_engine.signal import StrategySignal


def _settings(**overrides: Any) -> Settings:
    base = {
        "trading_mode": "paper",
        "market_data_provider": "mock",
        "broker_name": "paper",
        "alerts_enabled": False,
        "default_symbol": "AAPL",
        "default_timeframe": "1h",
        "backtest_initial_capital": Decimal("100000"),
        "backtest_commission_pct": Decimal("0.001"),
    }
    base.update(overrides)
    return Settings(**base)


def _hold_strategy() -> MagicMock:
    module = MagicMock()
    module.evaluate.side_effect = lambda bars, symbol=None, strategy_name=None: (
        StrategySignal(
            symbol=symbol or "AAPL",
            action=SignalAction.HOLD,
            confidence=0.5,
            strategy_name=strategy_name or "scripted",
            price=bars[-1].close if bars else Decimal("100"),
        )
    )
    return module


def _patch_app(
    monkeypatch: pytest.MonkeyPatch,
    settings: Settings,
    *,
    strategy_module: MagicMock | None = None,
) -> MagicMock:
    monkeypatch.setattr(main_module, "load_settings", lambda _path: settings)
    monkeypatch.setattr(main_module, "setup_logging", lambda _s: None)
    app = MagicMock()
    app.settings = settings
    app.startup.return_value = SimpleNamespace(success=True, summary=lambda: "OK")
    strategy = strategy_module if strategy_module is not None else _hold_strategy()
    app.get_module.side_effect = lambda name: (
        strategy if name == "strategy_engine" else MagicMock(name=name)
    )
    monkeypatch.setattr(main_module, "TradingBotApplication", lambda _s: app)
    return app


def test_parse_args_run_backtest_requires_bars_source() -> None:
    with pytest.raises(SystemExit):
        main_module.parse_args(["run-backtest"])


def test_parse_args_run_backtest_synthetic_defaults() -> None:
    args = main_module.parse_args(
        ["run-backtest", "--synthetic-bars", "20", "--max-cycles", "5"]
    )
    assert args.command == "run-backtest"
    assert args.synthetic_bars == 20
    assert args.bars_file is None
    assert args.max_cycles == 5
    assert not hasattr(args, "paper") or "paper" not in vars(args)


def test_parse_args_run_backtest_rejects_paper_flag() -> None:
    with pytest.raises(SystemExit):
        main_module.parse_args(
            ["run-backtest", "--synthetic-bars", "5", "--paper"]
        )


def test_parse_args_run_backtest_rejects_dry_run_flag() -> None:
    with pytest.raises(SystemExit):
        main_module.parse_args(
            ["run-backtest", "--synthetic-bars", "5", "--dry-run"]
        )


def test_run_once_and_run_session_cli_still_parse() -> None:
    once = main_module.parse_args(["run-once", "--symbol", "AAPL"])
    assert once.command == "run-once"
    assert main_module._execution_from_args(once) == "dry_run"

    session = main_module.parse_args(["run-session", "--cycles", "2"])
    assert session.command == "run-session"
    assert session.cycles == 2


def test_run_backtest_success_prints_metrics(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    settings = _settings()
    app = _patch_app(monkeypatch, settings)

    code = main_module.main(
        [
            "run-backtest",
            "--synthetic-bars",
            "12",
            "--warmup-bars",
            "3",
            "--max-cycles",
            "4",
            "--commission-pct",
            "0",
        ]
    )
    assert code == 0
    out = capsys.readouterr().out
    assert "=== run-backtest result ===" in out
    assert "initial_capital=100000" in out
    assert "ending_equity=" in out
    assert "return_pct=" in out
    assert "total_trades=0" in out
    assert "wins=0" in out
    assert "losses=0" in out
    assert "win_rate=" in out
    assert "realized_pnl=" in out
    assert "commissions_paid=0" in out
    assert "cycles_executed=4" in out
    app.shutdown.assert_called_once()
    # Never uses the M8 factory composition root for backtest wiring.
    assert not hasattr(main_module, "_factory_called")


def test_run_backtest_commission_setting_behavior(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    settings = _settings(backtest_commission_pct=Decimal("0.01"))
    _patch_app(monkeypatch, settings)

    # Flat HOLD → no fills → commissions stay 0 even with high commission setting.
    assert main_module.main(
        ["run-backtest", "--synthetic-bars", "8", "--max-cycles", "2"]
    ) == 0
    out = capsys.readouterr().out
    assert "commissions_paid=0" in out

    # Explicit zero override still accepted.
    assert main_module.main(
        [
            "run-backtest",
            "--synthetic-bars",
            "8",
            "--max-cycles",
            "2",
            "--commission-pct",
            "0",
        ]
    ) == 0
    out = capsys.readouterr().out
    assert "commissions_paid=0" in out


def test_run_backtest_wires_dry_run_or_commission_never_broker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings()
    _patch_app(monkeypatch, settings)
    captured: dict[str, Any] = {}
    real_cls = BasicTradingRuntime

    def _capture(**kwargs: Any) -> BasicTradingRuntime:
        captured["executor"] = kwargs["executor"]
        captured["market_data"] = kwargs["market_data"]
        return real_cls(**kwargs)

    monkeypatch.setattr(main_module, "BasicTradingRuntime", _capture)

    assert main_module.main(
        [
            "run-backtest",
            "--synthetic-bars",
            "6",
            "--max-cycles",
            "2",
            "--commission-pct",
            "0.001",
        ]
    ) == 0
    assert isinstance(captured["executor"], CommissionDryRunExecutor)
    assert not isinstance(captured["executor"], BrokerOrderExecutor)

    assert main_module.main(
        [
            "run-backtest",
            "--synthetic-bars",
            "6",
            "--max-cycles",
            "2",
            "--commission-pct",
            "0",
        ]
    ) == 0
    assert isinstance(captured["executor"], DryRunExecutor)
    assert not isinstance(captured["executor"], BrokerOrderExecutor)


def test_run_backtest_invalid_max_cycles_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings()
    _patch_app(monkeypatch, settings)
    assert (
        main_module.main(
            [
                "run-backtest",
                "--synthetic-bars",
                "5",
                "--max-cycles",
                str(MAX_BACKTEST_CYCLES + 1),
            ]
        )
        == 1
    )


def test_run_backtest_rejects_non_paper_trading_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(trading_mode="live")
    _patch_app(monkeypatch, settings)
    assert (
        main_module.main(["run-backtest", "--synthetic-bars", "5", "--max-cycles", "1"])
        == 1
    )

    settings_bt = _settings(trading_mode="backtest")
    _patch_app(monkeypatch, settings_bt)
    assert (
        main_module.main(["run-backtest", "--synthetic-bars", "5", "--max-cycles", "1"])
        == 1
    )


def test_run_backtest_invalid_commission_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings()
    _patch_app(monkeypatch, settings)
    assert (
        main_module.main(
            [
                "run-backtest",
                "--synthetic-bars",
                "5",
                "--max-cycles",
                "1",
                "--commission-pct",
                "not-a-number",
            ]
        )
        == 1
    )


def test_run_backtest_bars_file_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    settings = _settings()
    _patch_app(monkeypatch, settings)
    csv_path = tmp_path / "bars.csv"
    csv_path.write_text(
        "timestamp,open,high,low,close,volume\n"
        "2026-01-01T00:00:00+00:00,100,101,99,100,1000\n"
        "2026-01-01T01:00:00+00:00,101,102,100,101,1000\n"
        "2026-01-01T02:00:00+00:00,102,103,101,102,1000\n"
        "2026-01-01T03:00:00+00:00,103,104,102,103,1000\n",
        encoding="utf-8",
    )

    code = main_module.main(
        [
            "run-backtest",
            "--bars-file",
            str(csv_path),
            "--warmup-bars",
            "2",
            "--max-cycles",
            "2",
            "--commission-pct",
            "0",
        ]
    )
    assert code == 0
    out = capsys.readouterr().out
    assert "cycles_executed=2" in out
    assert "initial_capital=100000" in out


def test_run_backtest_missing_bars_file_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    settings = _settings()
    _patch_app(monkeypatch, settings)
    missing = tmp_path / "missing.csv"
    assert (
        main_module.main(
            ["run-backtest", "--bars-file", str(missing), "--max-cycles", "1"]
        )
        == 1
    )


def test_run_backtest_oversized_synthetic_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings()
    _patch_app(monkeypatch, settings)
    assert (
        main_module.main(
            [
                "run-backtest",
                "--synthetic-bars",
                str(MAX_HISTORICAL_BARS + 1),
                "--max-cycles",
                "1",
            ]
        )
        == 1
    )


def test_run_once_cli_regression_still_uses_factory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings()
    monkeypatch.setattr(main_module, "load_settings", lambda _path: settings)
    monkeypatch.setattr(main_module, "setup_logging", lambda _s: None)
    app = MagicMock()
    app.startup.return_value = SimpleNamespace(success=True, summary=lambda: "OK")
    monkeypatch.setattr(main_module, "TradingBotApplication", lambda _s: app)

    captured: dict[str, Any] = {}

    def _fake_factory(application: Any, *, execution: str = "dry_run", **_kwargs: Any):
        captured["execution"] = execution
        runtime = MagicMock()
        runtime.run_once.return_value = MagicMock(
            success=True,
            stage_reached="portfolio",
            aborted_reason=None,
            alerts_sent=0,
            signal=None,
            intent=None,
            execution=None,
            order=None,
            portfolio_snapshot=None,
        )
        return runtime

    monkeypatch.setattr(main_module, "create_trading_runtime_from_app", _fake_factory)
    assert main_module.main(["run-once", "--symbol", "AAPL"]) == 0
    assert captured["execution"] == "dry_run"


def test_run_session_cli_regression_still_uses_factory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings()
    monkeypatch.setattr(main_module, "load_settings", lambda _path: settings)
    monkeypatch.setattr(main_module, "setup_logging", lambda _s: None)
    app = MagicMock()
    app.startup.return_value = SimpleNamespace(success=True, summary=lambda: "OK")
    monkeypatch.setattr(main_module, "TradingBotApplication", lambda _s: app)

    captured: dict[str, Any] = {}

    def _fake_factory(application: Any, *, execution: str = "dry_run", **_kwargs: Any):
        captured["execution"] = execution
        return MagicMock(name="runtime")

    monkeypatch.setattr(main_module, "create_trading_runtime_from_app", _fake_factory)

    class _FakeSessionRunner:
        def __init__(self, runtime: Any) -> None:
            captured["runtime"] = runtime

        def run(self, config: Any) -> Any:
            from runtime.session import SessionResult
            from runtime.models import PipelineResult

            return SessionResult(
                cycles_requested=config.cycles,
                cycles_executed=config.cycles,
                results=tuple(
                    PipelineResult(success=True, stage_reached="portfolio")
                    for _ in range(config.cycles)
                ),
                session_start_equity=Decimal("100000"),
                session_end_equity=Decimal("100000"),
                stopped_early=False,
                stop_reason=None,
            )

    monkeypatch.setattr(main_module, "SessionRunner", _FakeSessionRunner)
    assert main_module.main(["run-session", "--cycles", "2", "--symbol", "AAPL"]) == 0
    assert captured["execution"] == "dry_run"


def test_print_backtest_result_includes_required_metrics() -> None:
    from datetime import datetime, timezone

    result = BacktestResult(
        strategy_name="ema_crossover",
        start_date=datetime(2026, 1, 1, tzinfo=timezone.utc),
        end_date=datetime(2026, 1, 2, tzinfo=timezone.utc),
        initial_capital=Decimal("100000"),
        final_capital=Decimal("101000"),
        total_return_pct=Decimal("0.0100"),
        total_trades=2,
        win_rate=Decimal("1.0000"),
        wins=1,
        losses=0,
        realized_pnl=Decimal("1000"),
        commissions_paid=Decimal("5"),
        cycles_executed=10,
    )
    # Ensure helpers remain importable / callable without raising.
    main_module._print_backtest_result(result)
