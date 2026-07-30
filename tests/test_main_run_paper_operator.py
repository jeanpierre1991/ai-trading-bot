"""M12.3 CLI tests for main.py run-paper-operator (no network / real sleep)."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

import main as main_module
from config.settings import Settings
from runtime.models import PipelineResult
from runtime.paper_operator import MAX_OPERATOR_CYCLES, PaperOperator, PaperOperatorResult


def _settings(**overrides: Any) -> Settings:
    base = {
        "trading_mode": "paper",
        "market_data_provider": "mock",
        "broker_name": "paper",
        "alerts_enabled": False,
        "default_symbol": "AAPL",
        "market_hours_enabled": True,
        "market_hours_policy": "reject",
    }
    base.update(overrides)
    return Settings(**base)


def _operator_ok(*, cycles: int = 2, **overrides: Any) -> PaperOperatorResult:
    results = tuple(
        PipelineResult(success=True, stage_reached="portfolio") for _ in range(cycles)
    )
    base: dict[str, Any] = dict(
        cycles_requested=cycles,
        cycles_executed=cycles,
        results=results,
        operator_start_equity=Decimal("100000"),
        operator_end_equity=Decimal("100000"),
        stopped_early=False,
        stop_reason=None,
        kill_engaged=False,
        bound_reached="max_cycles",
        cycles_completed_total=cycles,
        resumed=False,
    )
    base.update(overrides)
    return PaperOperatorResult(**base)


def _base_argv(tmp_path: Path, *extra: str) -> list[str]:
    return [
        "run-paper-operator",
        "--max-cycles",
        "2",
        "--max-wall-time-seconds",
        "60",
        "--interval-seconds",
        "1",
        "--state-path",
        str(tmp_path / "state.json"),
        "--kill-file",
        str(tmp_path / "KILL"),
        *extra,
    ]


def test_parse_args_run_paper_operator_requires_bounds_and_paths(
    tmp_path: Path,
) -> None:
    with pytest.raises(SystemExit):
        main_module.parse_args(["run-paper-operator"])
    with pytest.raises(SystemExit):
        main_module.parse_args(
            [
                "run-paper-operator",
                "--max-cycles",
                "1",
                "--max-wall-time-seconds",
                "1",
                "--interval-seconds",
                "1",
            ]
        )
    with pytest.raises(SystemExit):
        main_module.parse_args(
            [
                "run-paper-operator",
                "--max-cycles",
                "1",
                "--max-wall-time-seconds",
                "1",
                "--interval-seconds",
                "1",
                "--kill-file",
                str(tmp_path / "KILL"),
            ]
        )
    with pytest.raises(SystemExit):
        main_module.parse_args(
            [
                "run-paper-operator",
                "--max-cycles",
                "1",
                "--max-wall-time-seconds",
                "1",
                "--interval-seconds",
                "1",
                "--state-path",
                str(tmp_path / "state.json"),
            ]
        )


def test_parse_args_run_paper_operator_defaults_dry_run(tmp_path: Path) -> None:
    args = main_module.parse_args(_base_argv(tmp_path))
    assert args.command == "run-paper-operator"
    assert args.max_cycles == 2
    assert args.paper is False
    assert main_module._execution_from_args(args) == "dry_run"
    assert args.state_path == str(tmp_path / "state.json")
    assert args.kill_file == str(tmp_path / "KILL")


def test_parse_args_run_paper_operator_paper_explicit(tmp_path: Path) -> None:
    args = main_module.parse_args(_base_argv(tmp_path, "--paper"))
    assert args.paper is True
    assert main_module._execution_from_args(args) == "paper"


def test_parse_args_run_paper_operator_dry_run_and_paper_mutually_exclusive(
    tmp_path: Path,
) -> None:
    with pytest.raises(SystemExit):
        main_module.parse_args(_base_argv(tmp_path, "--dry-run", "--paper"))


def test_run_paper_operator_success_exits_0(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings()
    monkeypatch.setattr(main_module, "load_settings", lambda _path: settings)
    monkeypatch.setattr(main_module, "setup_logging", lambda _s: None)

    app = MagicMock()
    app.startup.return_value = SimpleNamespace(success=True, summary=lambda: "OK")
    monkeypatch.setattr(main_module, "TradingBotApplication", lambda _s: app)

    captured: dict[str, Any] = {}

    def _fake_factory(application: Any, *, execution: str = "dry_run", **_k: Any):
        captured["execution"] = execution
        return MagicMock(name="runtime")

    monkeypatch.setattr(main_module, "create_trading_runtime_from_app", _fake_factory)

    operator = MagicMock()
    operator.run.return_value = _operator_ok(cycles=2)
    monkeypatch.setattr(
        main_module,
        "PaperOperator",
        lambda runtime, **kwargs: operator,
    )

    code = main_module.main(_base_argv(tmp_path, "--symbol", "MSFT"))
    assert code == 0
    assert captured["execution"] == "dry_run"
    config = operator.run.call_args.args[0]
    assert config.symbol == "MSFT"
    assert config.max_cycles == 2
    assert config.state_path == tmp_path / "state.json"
    assert config.interval_seconds == 1.0
    app.shutdown.assert_called_once()


def test_run_paper_operator_controlled_kill_exits_0(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
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
    operator = MagicMock()
    operator.run.return_value = _operator_ok(
        cycles=1,
        cycles_requested=5,
        cycles_executed=1,
        stopped_early=True,
        stop_reason="kill_switch_engaged",
        kill_engaged=True,
        bound_reached=None,
        results=(PipelineResult(success=True, stage_reached="portfolio"),),
    )
    monkeypatch.setattr(main_module, "PaperOperator", lambda *_a, **_k: operator)
    assert main_module.main(_base_argv(tmp_path)) == 0


def test_run_paper_operator_invalid_bounds_exits_1(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings()
    monkeypatch.setattr(main_module, "load_settings", lambda _path: settings)
    monkeypatch.setattr(main_module, "setup_logging", lambda _s: None)
    factory = MagicMock()
    monkeypatch.setattr(main_module, "create_trading_runtime_from_app", factory)
    app = MagicMock()
    monkeypatch.setattr(main_module, "TradingBotApplication", lambda _s: app)

    argv = _base_argv(tmp_path)
    idx = argv.index("--max-cycles")
    argv[idx + 1] = "0"
    assert main_module.main(argv) == 1
    factory.assert_not_called()


@pytest.mark.parametrize(
    "interval_argv",
    [
        ["--interval-seconds", "0"],
        ["--interval-seconds", "-1"],
        ["--interval-seconds", "nan"],
        ["--interval-seconds", "inf"],
        # Combined form: bare "-inf" is parsed as a new option by argparse.
        ["--interval-seconds=-inf"],
    ],
)
def test_run_paper_operator_invalid_interval_exits_1(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    interval_argv: list[str],
) -> None:
    """M1: invalid --interval-seconds must refuse start with Decision H exit 1."""
    settings = _settings()
    monkeypatch.setattr(main_module, "load_settings", lambda _path: settings)
    monkeypatch.setattr(main_module, "setup_logging", lambda _s: None)
    factory = MagicMock()
    monkeypatch.setattr(main_module, "create_trading_runtime_from_app", factory)
    app = MagicMock()
    monkeypatch.setattr(main_module, "TradingBotApplication", lambda _s: app)

    argv = [
        "run-paper-operator",
        "--max-cycles",
        "2",
        "--max-wall-time-seconds",
        "60",
        *interval_argv,
        "--state-path",
        str(tmp_path / "state.json"),
        "--kill-file",
        str(tmp_path / "KILL"),
    ]
    assert main_module.main(argv) == 1
    factory.assert_not_called()


def test_run_paper_operator_corrupt_state_exits_1_without_fresh_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """M2: corrupt state file refuses start (exit 1); no silent fresh-state run."""
    settings = _settings()
    monkeypatch.setattr(main_module, "load_settings", lambda _path: settings)
    monkeypatch.setattr(main_module, "setup_logging", lambda _s: None)
    app = MagicMock()
    app.startup.return_value = SimpleNamespace(success=True, summary=lambda: "OK")
    monkeypatch.setattr(main_module, "TradingBotApplication", lambda _s: app)

    from portfolio_manager.portfolio import Portfolio
    from order_manager.manager import OrderManager

    portfolio = Portfolio(cash=Decimal("100000"))
    order_manager = OrderManager()

    class _RT:
        def __init__(self) -> None:
            self.portfolio = portfolio
            self.order_manager = order_manager
            self.run_once_calls = 0

        def run_once(self, _context: Any) -> PipelineResult:
            self.run_once_calls += 1
            return PipelineResult(success=True, stage_reached="portfolio")

    runtime = _RT()
    monkeypatch.setattr(
        main_module, "create_trading_runtime_from_app", lambda *_a, **_k: runtime
    )
    monkeypatch.setattr(main_module, "PaperOperator", PaperOperator)

    state_path = tmp_path / "state.json"
    state_path.write_text("{bad-json", encoding="utf-8")
    corrupt_before = state_path.read_text(encoding="utf-8")

    assert main_module.main(_base_argv(tmp_path)) == 1
    assert runtime.run_once_calls == 0
    assert portfolio.cash == Decimal("100000")
    assert portfolio.position_count == 0
    assert state_path.read_text(encoding="utf-8") == corrupt_before
    app.shutdown.assert_called_once()


def test_run_paper_operator_state_fs_preflight_failure_exits_1(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """M12.4 S1b: unwritable state parent → CLI exit 1; no run_once."""
    from core.exceptions import ConfigurationError
    from order_manager.manager import OrderManager
    from portfolio_manager.portfolio import Portfolio
    from runtime.paper_state_store import JsonPaperStateStore

    settings = _settings()
    monkeypatch.setattr(main_module, "load_settings", lambda _path: settings)
    monkeypatch.setattr(main_module, "setup_logging", lambda _s: None)
    app = MagicMock()
    app.startup.return_value = SimpleNamespace(success=True, summary=lambda: "OK")
    monkeypatch.setattr(main_module, "TradingBotApplication", lambda _s: app)

    portfolio = Portfolio(cash=Decimal("100000"))

    class _RT:
        def __init__(self) -> None:
            self.portfolio = portfolio
            self.order_manager = OrderManager()
            self.run_once_calls = 0

        def run_once(self, _context: Any) -> PipelineResult:
            self.run_once_calls += 1
            return PipelineResult(success=True, stage_reached="portfolio")

    runtime = _RT()
    monkeypatch.setattr(
        main_module, "create_trading_runtime_from_app", lambda *_a, **_k: runtime
    )
    monkeypatch.setattr(main_module, "PaperOperator", PaperOperator)

    def _refuse(self: Any) -> None:
        raise ConfigurationError(
            "operator state directory is not writable: simulated"
        )

    monkeypatch.setattr(JsonPaperStateStore, "ensure_parent_writable", _refuse)

    assert main_module.main(_base_argv(tmp_path)) == 1
    assert runtime.run_once_calls == 0
    assert portfolio.cash == Decimal("100000")
    app.shutdown.assert_called_once()


def test_run_paper_operator_hours_allow_refuses_start_exit_1(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(market_hours_policy="allow")
    monkeypatch.setattr(main_module, "load_settings", lambda _path: settings)
    monkeypatch.setattr(main_module, "setup_logging", lambda _s: None)
    app = MagicMock()
    app.startup.return_value = SimpleNamespace(success=True, summary=lambda: "OK")
    monkeypatch.setattr(main_module, "TradingBotApplication", lambda _s: app)

    class _RT:
        portfolio = type("P", (), {"total_value": Decimal("100000")})()

    monkeypatch.setattr(
        main_module, "create_trading_runtime_from_app", lambda *_a, **_k: _RT()
    )
    monkeypatch.setattr(main_module, "PaperOperator", PaperOperator)

    assert main_module.main(_base_argv(tmp_path)) == 1
    app.shutdown.assert_called_once()


def test_run_paper_operator_kill_engaged_at_start_exits_1(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings()
    monkeypatch.setattr(main_module, "load_settings", lambda _path: settings)
    monkeypatch.setattr(main_module, "setup_logging", lambda _s: None)
    app = MagicMock()
    app.startup.return_value = SimpleNamespace(success=True, summary=lambda: "OK")
    monkeypatch.setattr(main_module, "TradingBotApplication", lambda _s: app)

    class _RT:
        portfolio = type("P", (), {"total_value": Decimal("100000")})()

    monkeypatch.setattr(
        main_module, "create_trading_runtime_from_app", lambda *_a, **_k: _RT()
    )
    monkeypatch.setattr(main_module, "PaperOperator", PaperOperator)

    kill = tmp_path / "KILL"
    kill.write_text("stop\n", encoding="utf-8")
    assert main_module.main(_base_argv(tmp_path)) == 1


def test_run_paper_operator_unexpected_exception_exits_2(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
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

    class _Boom:
        def __init__(self, *_a: Any, **_k: Any) -> None:
            pass

        def run(self, _config: Any) -> PaperOperatorResult:
            raise RuntimeError("boom")

    monkeypatch.setattr(main_module, "PaperOperator", _Boom)
    assert main_module.main(_base_argv(tmp_path)) == 2
    app.shutdown.assert_called_once()


def test_run_paper_operator_keyboard_interrupt_exits_130(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _raise(_path: str) -> Settings:
        raise KeyboardInterrupt

    monkeypatch.setattr(main_module, "load_settings", _raise)
    assert main_module.main(_base_argv(tmp_path)) == 130


def test_run_paper_operator_max_cycles_ceiling_exits_1(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings()
    monkeypatch.setattr(main_module, "load_settings", lambda _path: settings)
    monkeypatch.setattr(main_module, "setup_logging", lambda _s: None)
    assert (
        main_module.main(
            [
                "run-paper-operator",
                "--max-cycles",
                str(MAX_OPERATOR_CYCLES + 1),
                "--max-wall-time-seconds",
                "1",
                "--interval-seconds",
                "1",
                "--state-path",
                str(tmp_path / "s.json"),
                "--kill-file",
                str(tmp_path / "KILL"),
            ]
        )
        == 1
    )
