"""M9.3 CLI smoke tests for main.py run-session (no network / Yahoo / live)."""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

import main as main_module
from config.settings import Settings
from core.exceptions import ConfigurationError
from runtime.session import MAX_SESSION_CYCLES, SessionResult
from runtime.models import PipelineResult


def _settings(**overrides: Any) -> Settings:
    base = {
        "trading_mode": "paper",
        "market_data_provider": "mock",
        "broker_name": "paper",
        "alerts_enabled": False,
        "default_symbol": "AAPL",
    }
    base.update(overrides)
    return Settings(**base)


def _session_ok(*, cycles: int = 2, stopped_early: bool = False) -> SessionResult:
    if stopped_early:
        results = (
            PipelineResult(success=True, stage_reached="portfolio"),
            PipelineResult(
                success=False,
                stage_reached="risk",
                aborted_reason="max_daily_loss_pct exceeded",
            ),
        )
        return SessionResult(
            cycles_requested=cycles,
            cycles_executed=2,
            results=results,
            session_start_equity=Decimal("100000"),
            session_end_equity=Decimal("100000"),
            stopped_early=True,
            stop_reason="max_daily_loss_pct exceeded",
        )

    results = tuple(
        PipelineResult(success=True, stage_reached="portfolio") for _ in range(cycles)
    )
    return SessionResult(
        cycles_requested=cycles,
        cycles_executed=cycles,
        results=results,
        session_start_equity=Decimal("100000"),
        session_end_equity=Decimal("100000"),
        stopped_early=False,
        stop_reason=None,
    )


def test_parse_args_run_session_requires_cycles() -> None:
    with pytest.raises(SystemExit):
        main_module.parse_args(["run-session"])


def test_parse_args_run_session_defaults_to_dry_run_execution() -> None:
    args = main_module.parse_args(["run-session", "--cycles", "3"])
    assert args.command == "run-session"
    assert args.cycles == 3
    assert args.paper is False
    assert args.dry_run is False
    assert main_module._execution_from_args(args) == "dry_run"
    assert not hasattr(args, "daily_pnl_pct") or "daily_pnl_pct" not in vars(args)


def test_parse_args_run_session_paper_explicit() -> None:
    args = main_module.parse_args(["run-session", "--cycles", "2", "--paper"])
    assert args.paper is True
    assert main_module._execution_from_args(args) == "paper"


def test_parse_args_run_session_dry_run_and_paper_mutually_exclusive() -> None:
    with pytest.raises(SystemExit):
        main_module.parse_args(
            ["run-session", "--cycles", "1", "--dry-run", "--paper"]
        )


def test_parse_args_run_session_has_no_daily_pnl_pct_flag() -> None:
    with pytest.raises(SystemExit):
        main_module.parse_args(
            ["run-session", "--cycles", "1", "--daily-pnl-pct", "0"]
        )


def test_run_session_dry_run_default_uses_factory_and_returns_0(
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
        captured["execution"] = execution
        captured["app"] = application
        return MagicMock(name="runtime")

    monkeypatch.setattr(main_module, "create_trading_runtime_from_app", _fake_factory)

    runner = MagicMock()
    runner.run.return_value = _session_ok(cycles=3)
    monkeypatch.setattr(main_module, "SessionRunner", lambda runtime: runner)

    assert main_module.main(["run-session", "--cycles", "3", "--symbol", "MSFT"]) == 0
    assert captured["execution"] == "dry_run"
    assert captured["app"] is app
    config = runner.run.call_args.args[0]
    assert config.cycles == 3
    assert config.symbol == "MSFT"
    app.shutdown.assert_called_once()


def test_run_session_paper_flag_selects_paper_execution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings()
    monkeypatch.setattr(main_module, "load_settings", lambda _path: settings)
    monkeypatch.setattr(main_module, "setup_logging", lambda _s: None)

    app = MagicMock()
    app.startup.return_value = SimpleNamespace(success=True, summary=lambda: "OK")
    monkeypatch.setattr(main_module, "TradingBotApplication", lambda _s: app)

    captured: dict[str, Any] = {}
    monkeypatch.setattr(
        main_module,
        "create_trading_runtime_from_app",
        lambda _app, *, execution="dry_run", **_k: captured.update(execution=execution)
        or MagicMock(),
    )
    runner = MagicMock()
    runner.run.return_value = _session_ok(cycles=1)
    monkeypatch.setattr(main_module, "SessionRunner", lambda runtime: runner)

    assert main_module.main(["run-session", "--cycles", "1", "--paper"]) == 0
    assert captured["execution"] == "paper"
    app.shutdown.assert_called_once()


def test_run_session_stopped_early_still_exits_0(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings()
    monkeypatch.setattr(main_module, "load_settings", lambda _path: settings)
    monkeypatch.setattr(main_module, "setup_logging", lambda _s: None)

    app = MagicMock()
    app.startup.return_value = SimpleNamespace(success=True, summary=lambda: "OK")
    monkeypatch.setattr(main_module, "TradingBotApplication", lambda _s: app)
    monkeypatch.setattr(
        main_module,
        "create_trading_runtime_from_app",
        lambda *_a, **_k: MagicMock(),
    )
    runner = MagicMock()
    runner.run.return_value = _session_ok(cycles=5, stopped_early=True)
    monkeypatch.setattr(main_module, "SessionRunner", lambda runtime: runner)

    assert main_module.main(["run-session", "--cycles", "5"]) == 0
    app.shutdown.assert_called_once()


def test_run_session_invalid_cycles_exits_1(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings()
    monkeypatch.setattr(main_module, "load_settings", lambda _path: settings)
    monkeypatch.setattr(main_module, "setup_logging", lambda _s: None)

    factory = MagicMock()
    monkeypatch.setattr(main_module, "create_trading_runtime_from_app", factory)
    app = MagicMock()
    monkeypatch.setattr(main_module, "TradingBotApplication", lambda _s: app)

    assert main_module.main(["run-session", "--cycles", "0"]) == 1
    factory.assert_not_called()
    app.startup.assert_not_called()

    assert main_module.main(["run-session", "--cycles", str(MAX_SESSION_CYCLES + 1)]) == 1
    factory.assert_not_called()


def test_run_session_startup_failure_exits_1(monkeypatch: pytest.MonkeyPatch) -> None:
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

    assert main_module.main(["run-session", "--cycles", "2"]) == 1
    factory.assert_not_called()
    app.shutdown.assert_called_once()


def test_run_session_configuration_error_exits_1(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(trading_mode="live")
    monkeypatch.setattr(main_module, "load_settings", lambda _path: settings)
    monkeypatch.setattr(main_module, "setup_logging", lambda _s: None)

    app = MagicMock()
    app.startup.return_value = SimpleNamespace(success=True, summary=lambda: "OK")
    monkeypatch.setattr(main_module, "TradingBotApplication", lambda _s: app)
    monkeypatch.setattr(
        main_module,
        "create_trading_runtime_from_app",
        MagicMock(
            side_effect=ConfigurationError(
                "trading_mode='live' is not allowed; "
                "Milestone 8 factory permits paper and dry-run only"
            )
        ),
    )

    assert main_module.main(["run-session", "--cycles", "1"]) == 1
    app.shutdown.assert_called()


def test_run_session_unexpected_exception_exits_2(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings()
    monkeypatch.setattr(main_module, "load_settings", lambda _path: settings)
    monkeypatch.setattr(main_module, "setup_logging", lambda _s: None)

    app = MagicMock()
    app.startup.return_value = SimpleNamespace(success=True, summary=lambda: "OK")
    monkeypatch.setattr(main_module, "TradingBotApplication", lambda _s: app)
    monkeypatch.setattr(
        main_module,
        "create_trading_runtime_from_app",
        lambda *_a, **_k: MagicMock(),
    )
    runner = MagicMock()
    runner.run.side_effect = RuntimeError("boom")
    monkeypatch.setattr(main_module, "SessionRunner", lambda runtime: runner)

    assert main_module.main(["run-session", "--cycles", "2"]) == 2
    app.shutdown.assert_called_once()


def test_print_session_result_has_no_secrets() -> None:
    main_module._print_session_result(_session_ok(cycles=1, stopped_early=True))
