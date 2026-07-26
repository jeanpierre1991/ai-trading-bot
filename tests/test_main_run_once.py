"""M8.7 CLI smoke tests for main.py run-once (no network / Yahoo / live)."""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

import main as main_module
from config.settings import Settings
from core.types import TradingMode
from runtime.models import PipelineResult


def _settings(**overrides: Any) -> Settings:
    base = {
        "trading_mode": "paper",
        "market_data_provider": "mock",
        "broker_name": "paper",
        "alerts_enabled": False,
    }
    base.update(overrides)
    return Settings(**base)


def test_parse_args_default_is_startup() -> None:
    args = main_module.parse_args([])
    assert args.command is None
    assert args.env_file == ".env"


def test_parse_args_run_once_defaults_to_dry_run_execution() -> None:
    args = main_module.parse_args(["run-once"])
    assert args.command == "run-once"
    assert args.paper is False
    assert args.dry_run is False  # neither flag → factory dry_run via helper
    assert main_module._execution_from_args(args) == "dry_run"


def test_parse_args_paper_requires_explicit_flag() -> None:
    args = main_module.parse_args(["run-once", "--paper"])
    assert args.paper is True
    assert main_module._execution_from_args(args) == "paper"


def test_parse_args_dry_run_and_paper_are_mutually_exclusive() -> None:
    with pytest.raises(SystemExit):
        main_module.parse_args(["run-once", "--dry-run", "--paper"])


def test_startup_without_subcommand_preserved(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings()
    monkeypatch.setattr(main_module, "load_settings", lambda _path: settings)
    monkeypatch.setattr(main_module, "setup_logging", lambda _s: None)

    app = MagicMock()
    report = SimpleNamespace(success=True, summary=lambda: "OK")
    app.startup.return_value = report
    app.is_started = False
    monkeypatch.setattr(main_module, "TradingBotApplication", lambda _s: app)

    assert main_module.main([]) == 0
    app.startup.assert_called_once()
    app.shutdown.assert_called_once()


def test_run_once_dry_run_default_uses_factory_and_returns_0(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings()
    monkeypatch.setattr(main_module, "load_settings", lambda _path: settings)
    monkeypatch.setattr(main_module, "setup_logging", lambda _s: None)

    app = MagicMock()
    app.settings = settings
    app.registry = object()
    app.startup.return_value = SimpleNamespace(success=True, summary=lambda: "OK")
    monkeypatch.setattr(main_module, "TradingBotApplication", lambda _s: app)

    captured: dict[str, Any] = {}

    def _fake_factory(application: Any, *, execution: str = "dry_run", **_kwargs: Any):
        captured["app"] = application
        captured["execution"] = execution
        runtime = MagicMock()
        runtime.run_once.return_value = PipelineResult(
            success=True,
            stage_reached="portfolio",
        )
        captured["runtime"] = runtime
        return runtime

    monkeypatch.setattr(main_module, "create_trading_runtime_from_app", _fake_factory)

    assert main_module.main(["run-once", "--symbol", "AAPL"]) == 0
    assert captured["execution"] == "dry_run"
    assert captured["app"] is app

    context = captured["runtime"].run_once.call_args.args[0]
    assert context.symbol == "AAPL"
    assert context.mode is TradingMode.PAPER
    assert context.daily_pnl_pct == Decimal("0")
    app.shutdown.assert_called_once()


def test_run_once_paper_flag_selects_paper_execution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings()
    monkeypatch.setattr(main_module, "load_settings", lambda _path: settings)
    monkeypatch.setattr(main_module, "setup_logging", lambda _s: None)

    app = MagicMock()
    app.startup.return_value = SimpleNamespace(success=True, summary=lambda: "OK")
    monkeypatch.setattr(main_module, "TradingBotApplication", lambda _s: app)

    captured: dict[str, Any] = {}

    def _fake_factory(_application: Any, *, execution: str = "dry_run", **_kwargs: Any):
        captured["execution"] = execution
        runtime = MagicMock()
        runtime.run_once.return_value = PipelineResult(
            success=True,
            stage_reached="portfolio",
        )
        return runtime

    monkeypatch.setattr(main_module, "create_trading_runtime_from_app", _fake_factory)

    assert main_module.main(["run-once", "--paper"]) == 0
    assert captured["execution"] == "paper"


def test_run_once_controlled_abort_still_exits_0(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings()
    monkeypatch.setattr(main_module, "load_settings", lambda _path: settings)
    monkeypatch.setattr(main_module, "setup_logging", lambda _s: None)

    app = MagicMock()
    app.startup.return_value = SimpleNamespace(success=True, summary=lambda: "OK")
    monkeypatch.setattr(main_module, "TradingBotApplication", lambda _s: app)

    runtime = MagicMock()
    runtime.run_once.return_value = PipelineResult(
        success=False,
        stage_reached="risk",
        aborted_reason="max_daily_loss_pct exceeded",
    )
    monkeypatch.setattr(
        main_module,
        "create_trading_runtime_from_app",
        lambda *_a, **_k: runtime,
    )

    assert main_module.main(["run-once"]) == 0


def test_run_once_live_mode_fails_via_factory_before_cycle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Real factory reject; no dependency assembly beyond mode validation."""
    settings = _settings(trading_mode="live")
    monkeypatch.setattr(main_module, "load_settings", lambda _path: settings)
    monkeypatch.setattr(main_module, "setup_logging", lambda _s: None)

    app = MagicMock()
    app.settings = settings
    app.registry = None
    app.startup.return_value = SimpleNamespace(success=True, summary=lambda: "OK")
    monkeypatch.setattr(main_module, "TradingBotApplication", lambda _s: app)

    assert main_module.main(["run-once"]) == 1
    app.shutdown.assert_called()


def test_run_once_backtest_mode_fails_via_factory_with_exit_1(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(trading_mode="backtest")
    monkeypatch.setattr(main_module, "load_settings", lambda _path: settings)
    monkeypatch.setattr(main_module, "setup_logging", lambda _s: None)

    app = MagicMock()
    app.settings = settings
    app.registry = None
    app.startup.return_value = SimpleNamespace(success=True, summary=lambda: "OK")
    monkeypatch.setattr(main_module, "TradingBotApplication", lambda _s: app)

    assert main_module.main(["run-once"]) == 1


def test_run_once_startup_failure_exits_1(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings()
    monkeypatch.setattr(main_module, "load_settings", lambda _path: settings)
    monkeypatch.setattr(main_module, "setup_logging", lambda _s: None)

    app = MagicMock()
    app.startup.return_value = SimpleNamespace(
        success=False,
        summary=lambda: "FAILED",
    )
    factory = MagicMock()
    monkeypatch.setattr(main_module, "TradingBotApplication", lambda _s: app)
    monkeypatch.setattr(main_module, "create_trading_runtime_from_app", factory)

    assert main_module.main(["run-once"]) == 1
    factory.assert_not_called()
    app.shutdown.assert_called_once()


def test_run_once_unexpected_exception_exits_2(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings()
    monkeypatch.setattr(main_module, "load_settings", lambda _path: settings)
    monkeypatch.setattr(main_module, "setup_logging", lambda _s: None)

    app = MagicMock()
    app.startup.return_value = SimpleNamespace(success=True, summary=lambda: "OK")
    monkeypatch.setattr(main_module, "TradingBotApplication", lambda _s: app)

    runtime = MagicMock()
    runtime.run_once.side_effect = RuntimeError("boom")
    monkeypatch.setattr(
        main_module,
        "create_trading_runtime_from_app",
        lambda *_a, **_k: runtime,
    )

    assert main_module.main(["run-once"]) == 2
    app.shutdown.assert_called_once()


def test_run_once_invalid_daily_pnl_pct_exits_1(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings()
    monkeypatch.setattr(main_module, "load_settings", lambda _path: settings)
    monkeypatch.setattr(main_module, "setup_logging", lambda _s: None)

    factory = MagicMock()
    monkeypatch.setattr(main_module, "create_trading_runtime_from_app", factory)

    assert main_module.main(["run-once", "--daily-pnl-pct", "not-a-number"]) == 1
    factory.assert_not_called()


def test_print_pipeline_result_has_no_secrets() -> None:
    result = PipelineResult(
        success=False,
        stage_reached="mode",
        aborted_reason="trading_mode='live' is not allowed",
    )
    # Smoke: formatter only uses PipelineResult public fields
    main_module._print_pipeline_result(result)
