#!/usr/bin/env python3
"""AI Trading Bot — application entry point, startup verifier, and CLI."""

from __future__ import annotations

import argparse
import logging
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path

from backtesting.bars_io import load_bars_from_csv, make_synthetic_bars
from backtesting.commission import CommissionDryRunExecutor
from backtesting.engine import BacktestResult
from backtesting.runner import (
    MAX_BACKTEST_CYCLES,
    BacktestConfig,
    BacktestRunner,
)
from config.loader import load_settings
from config.settings import Settings
from core.application import TradingBotApplication
from core.exceptions import ConfigurationError
from core.types import TimeFrame, TradingMode
from market_data.historical_provider import (
    MAX_HISTORICAL_BARS,
    HistoricalMarketDataProvider,
    HistoricalRuntimeMarketData,
)
from portfolio_manager.portfolio import Portfolio
from risk_manager.basic import BasicRiskManager
from runtime.context import RuntimeContext
from runtime.dry_run import DryRunExecutor
from runtime.factory import create_trading_runtime_from_app
from runtime.kill_switch import FileEnvKillSwitch
from runtime.models import PipelineResult
from runtime.paper_operator import (
    MAX_OPERATOR_CYCLES,
    PaperOperator,
    PaperOperatorConfig,
    PaperOperatorResult,
)
from runtime.session import MAX_SESSION_CYCLES, SessionConfig, SessionResult, SessionRunner
from runtime.trading_runtime import BasicTradingRuntime
from utilities.logging_setup import setup_logging

logger = logging.getLogger("trading_bot.main")


def _add_execution_flags(
    parser: argparse.ArgumentParser,
    *,
    allow_live: bool = False,
) -> None:
    execution = parser.add_mutually_exclusive_group()
    execution.add_argument(
        "--dry-run",
        action="store_true",
        help="Use DryRunExecutor (safe default when neither flag is set)",
    )
    execution.add_argument(
        "--paper",
        action="store_true",
        help="Use BrokerOrderExecutor with PaperBroker (must be explicit)",
    )
    if allow_live:
        execution.add_argument(
            "--live",
            action="store_true",
            help=(
                "M13.2 supervised BROKER_SANDBOX path only when all live gates "
                "pass (not a real-money trial; LIVE_PRODUCTION remains denied)"
            ),
        )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="AI Trading Bot — modular quantitative trading platform",
    )
    parser.add_argument(
        "--env-file",
        default=".env",
        help="Path to environment file (default: .env)",
    )
    parser.add_argument(
        "--health-only",
        action="store_true",
        help="Run health checks on already-loaded modules without full startup",
    )

    subparsers = parser.add_subparsers(dest="command")

    run_once_parser = subparsers.add_parser(
        "run-once",
        help="Run one paper/dry-run decision cycle (Milestone 8); optional gated --live sandbox",
    )
    _add_execution_flags(run_once_parser, allow_live=True)
    run_once_parser.add_argument(
        "--symbol",
        default=None,
        help="Symbol for the cycle (default: settings.default_symbol)",
    )
    run_once_parser.add_argument(
        "--strategy",
        default=None,
        help="Strategy name (default: strategy engine default)",
    )
    run_once_parser.add_argument(
        "--bar-limit",
        type=int,
        default=100,
        help="Number of bars to request (default: 100)",
    )
    run_once_parser.add_argument(
        "--daily-pnl-pct",
        default="0",
        help=(
            "Daily P&L fraction for operational risk "
            "(default: 0; required conceptually when max_daily_loss_pct is active)"
        ),
    )

    run_session_parser = subparsers.add_parser(
        "run-session",
        help=(
            "Run a bounded paper/dry-run multi-cycle session (Milestone 9); "
            f"optional gated --live sandbox; cycles must be 1..{MAX_SESSION_CYCLES}"
        ),
    )
    _add_execution_flags(run_session_parser, allow_live=True)
    run_session_parser.add_argument(
        "--cycles",
        type=int,
        required=True,
        help=f"Number of cycles to run (required; 1..{MAX_SESSION_CYCLES})",
    )
    run_session_parser.add_argument(
        "--symbol",
        default=None,
        help="Symbol for the session (default: settings.default_symbol)",
    )
    run_session_parser.add_argument(
        "--strategy",
        default=None,
        help="Strategy name (default: strategy engine default)",
    )
    run_session_parser.add_argument(
        "--bar-limit",
        type=int,
        default=100,
        help="Number of bars to request per cycle (default: 100)",
    )

    run_backtest_parser = subparsers.add_parser(
        "run-backtest",
        help=(
            "Run a bounded historical paper backtest (Milestone 10); "
            "DryRun/CommissionDryRun only — no broker path"
        ),
    )
    bars_source = run_backtest_parser.add_mutually_exclusive_group(required=True)
    bars_source.add_argument(
        "--bars-file",
        default=None,
        help="Local OHLCV CSV path (network-free; required columns: "
        "timestamp,open,high,low,close,volume)",
    )
    bars_source.add_argument(
        "--synthetic-bars",
        type=int,
        default=None,
        help=(
            "Generate N deterministic synthetic bars "
            f"(1..{MAX_HISTORICAL_BARS}; network-free)"
        ),
    )
    run_backtest_parser.add_argument(
        "--symbol",
        default=None,
        help="Symbol for the backtest (default: settings.default_symbol)",
    )
    run_backtest_parser.add_argument(
        "--strategy",
        default=None,
        help="Strategy name (default: strategy engine default)",
    )
    run_backtest_parser.add_argument(
        "--bar-limit",
        type=int,
        default=100,
        help="Bars requested per cycle (default: 100)",
    )
    run_backtest_parser.add_argument(
        "--warmup-bars",
        type=int,
        default=None,
        help="Visible bars before the first cycle (default: min(bar-limit, series length))",
    )
    run_backtest_parser.add_argument(
        "--max-cycles",
        type=int,
        default=None,
        help=(
            "Hard cap on run_once cycles "
            f"(default: series length; max {MAX_BACKTEST_CYCLES})"
        ),
    )
    run_backtest_parser.add_argument(
        "--commission-pct",
        default=None,
        help=(
            "Commission as a fraction of notional "
            "(default: settings.backtest_commission_pct)"
        ),
    )
    run_backtest_parser.add_argument(
        "--timeframe",
        default=None,
        help="Bar timeframe for the series (default: settings.default_timeframe)",
    )

    run_paper_operator_parser = subparsers.add_parser(
        "run-paper-operator",
        help=(
            "Run a hard-bounded unattended paper/dry-run operator "
            "(Milestone 12); requires explicit bounds, --state-path, and --kill-file"
        ),
    )
    _add_execution_flags(run_paper_operator_parser)
    run_paper_operator_parser.add_argument(
        "--max-cycles",
        type=int,
        required=True,
        help=f"Hard cycle bound (required; 1..{MAX_OPERATOR_CYCLES})",
    )
    run_paper_operator_parser.add_argument(
        "--max-wall-time-seconds",
        type=float,
        required=True,
        help="Hard wall-time bound in seconds (required; > 0)",
    )
    run_paper_operator_parser.add_argument(
        "--interval-seconds",
        type=float,
        required=True,
        help="Sleep between cycles in seconds (required; > 0)",
    )
    run_paper_operator_parser.add_argument(
        "--state-path",
        required=True,
        help="Durable operator state JSON path (required; no unattended default)",
    )
    run_paper_operator_parser.add_argument(
        "--kill-file",
        required=True,
        help=(
            "Kill-switch file path (required). Presence engages the switch; "
            "PAPER_OPERATOR_KILL env also engages"
        ),
    )
    run_paper_operator_parser.add_argument(
        "--symbol",
        default=None,
        help="Symbol for the operator (default: settings.default_symbol)",
    )
    run_paper_operator_parser.add_argument(
        "--strategy",
        default=None,
        help="Strategy name (default: strategy engine default)",
    )
    run_paper_operator_parser.add_argument(
        "--bar-limit",
        type=int,
        default=100,
        help="Number of bars to request per cycle (default: 100)",
    )

    return parser.parse_args(argv)


def _execution_from_args(args: argparse.Namespace) -> str:
    """Map CLI flags to factory execution. Default is dry_run."""
    if getattr(args, "live", False):
        return "live"
    if getattr(args, "paper", False):
        return "paper"
    return "dry_run"


def _context_mode_for_execution(execution: str) -> TradingMode:
    if execution == "live":
        return TradingMode.LIVE
    return TradingMode.PAPER


def _print_pipeline_result(result: PipelineResult) -> None:
    """Print a minimal PipelineResult summary (no secrets)."""
    lines = [
        "=== run-once result ===",
        f"success={result.success}",
        f"stage_reached={result.stage_reached}",
        f"aborted_reason={result.aborted_reason}",
        f"alerts_sent={result.alerts_sent}",
    ]
    if result.signal is not None:
        lines.append(f"signal_action={result.signal.action}")
    if result.intent is not None:
        lines.append(
            f"intent={result.intent.side.value} {result.intent.quantity} "
            f"{result.intent.symbol}"
        )
    if result.execution is not None:
        lines.append(
            f"execution_status={result.execution.status.value} "
            f"message={result.execution.message}"
        )
    if result.order is not None:
        lines.append(f"order_state={result.order.state.value}")
    if result.portfolio_snapshot is not None:
        lines.append(f"portfolio_snapshot={result.portfolio_snapshot}")
    print("\n".join(lines))


def _print_session_result(result: SessionResult) -> None:
    """Print a minimal SessionResult summary (no secrets)."""
    lines = [
        "=== run-session result ===",
        f"cycles_requested={result.cycles_requested}",
        f"cycles_executed={result.cycles_executed}",
        f"stopped_early={result.stopped_early}",
        f"stop_reason={result.stop_reason}",
        f"completed_all_cycles={result.completed_all_cycles}",
        f"session_start_equity={result.session_start_equity}",
        f"session_end_equity={result.session_end_equity}",
    ]
    for index, cycle in enumerate(result.results):
        lines.append(
            f"cycle[{index}] success={cycle.success} "
            f"stage={cycle.stage_reached} aborted_reason={cycle.aborted_reason}"
        )
    print("\n".join(lines))


def _print_backtest_result(result: BacktestResult) -> None:
    """Print BacktestResult metrics (M10.2); no secrets."""
    lines = [
        "=== run-backtest result ===",
        f"strategy_name={result.strategy_name}",
        f"initial_capital={result.initial_capital}",
        f"ending_equity={result.ending_equity}",
        f"return_pct={result.return_pct}",
        f"total_trades={result.total_trades}",
        f"wins={result.wins}",
        f"losses={result.losses}",
        f"win_rate={result.win_rate}",
        f"realized_pnl={result.realized_pnl}",
        f"commissions_paid={result.commissions_paid}",
        f"cycles_executed={result.cycles_executed}",
        f"start_date={result.start_date.isoformat()}",
        f"end_date={result.end_date.isoformat()}",
    ]
    print("\n".join(lines))


def _print_paper_operator_result(result: PaperOperatorResult) -> None:
    """Print a minimal PaperOperatorResult summary (no secrets)."""
    lines = [
        "=== run-paper-operator result ===",
        f"cycles_requested={result.cycles_requested}",
        f"cycles_executed={result.cycles_executed}",
        f"cycles_completed_total={result.cycles_completed_total}",
        f"resumed={result.resumed}",
        f"stopped_early={result.stopped_early}",
        f"stop_reason={result.stop_reason}",
        f"kill_engaged={result.kill_engaged}",
        f"bound_reached={result.bound_reached}",
        f"completed_all_cycles={result.completed_all_cycles}",
        f"operator_start_equity={result.operator_start_equity}",
        f"operator_end_equity={result.operator_end_equity}",
    ]
    for index, cycle in enumerate(result.results):
        lines.append(
            f"cycle[{index}] success={cycle.success} "
            f"stage={cycle.stage_reached} aborted_reason={cycle.aborted_reason}"
        )
    print("\n".join(lines))


def _parse_required_positive_float(raw: object, *, flag: str) -> float:
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        raise ConfigurationError(f"{flag} must be a positive finite number; got {raw!r}")
    value = float(raw)
    if value != value or value in (float("inf"), float("-inf")) or value <= 0:
        raise ConfigurationError(f"{flag} must be a positive finite number; got {raw!r}")
    return value


def _run_paper_operator_command(settings: Settings, args: argparse.Namespace) -> int:
    """Startup → factory → PaperOperator → summary (Decision H exit codes)."""
    if args.bar_limit <= 0:
        raise ConfigurationError("--bar-limit must be a positive integer")

    if (
        not isinstance(args.max_cycles, int)
        or isinstance(args.max_cycles, bool)
        or args.max_cycles < 1
        or args.max_cycles > MAX_OPERATOR_CYCLES
    ):
        raise ConfigurationError(
            f"--max-cycles must be an int in 1..{MAX_OPERATOR_CYCLES}; "
            f"got {args.max_cycles!r}"
        )

    max_wall = _parse_required_positive_float(
        args.max_wall_time_seconds, flag="--max-wall-time-seconds"
    )
    interval = _parse_required_positive_float(
        args.interval_seconds, flag="--interval-seconds"
    )

    state_raw = str(args.state_path).strip()
    if not state_raw:
        raise ConfigurationError("--state-path is required and must be non-empty")
    state_path = Path(state_raw)

    kill_raw = str(args.kill_file).strip()
    if not kill_raw:
        raise ConfigurationError("--kill-file is required and must be non-empty")
    kill_path = Path(kill_raw)

    app = TradingBotApplication(settings)
    report = app.startup()
    if not report.success:
        print(report.summary())
        app.shutdown()
        return 1

    exit_code = 0
    try:
        if getattr(args, "live", False):
            raise ConfigurationError(
                "run-paper-operator does not support --live (G8); paper-only"
            )
        if settings.trading_mode != "paper":
            raise ConfigurationError(
                "run-paper-operator requires trading_mode='paper'; "
                f"got {settings.trading_mode!r}"
            )
        execution = _execution_from_args(args)
        if execution == "live":
            raise ConfigurationError(
                "run-paper-operator cannot use execution='live' (G8)"
            )
        runtime = create_trading_runtime_from_app(
            app,
            execution=execution,  # type: ignore[arg-type]
        )
        config = PaperOperatorConfig(
            symbol=args.symbol or settings.default_symbol,
            max_cycles=args.max_cycles,
            max_wall_time_seconds=max_wall,
            interval_seconds=interval,
            strategy_name=args.strategy,
            bar_limit=args.bar_limit,
            execution=execution,  # type: ignore[arg-type]
            state_path=state_path,
        )
        operator = PaperOperator(
            runtime,
            settings=settings,
            kill_switch=FileEnvKillSwitch(kill_path),
        )
        result = operator.run(config)
        _print_paper_operator_result(result)
        # Decision H: controlled kill/bound/hard-stop/completion → 0
        exit_code = 0
    except ConfigurationError:
        raise
    except Exception:
        logger.exception("Unexpected error during run-paper-operator")
        exit_code = 2
    finally:
        app.shutdown()

    return exit_code


def _run_startup(app: TradingBotApplication, *, health_only: bool) -> int:
    """Preserve pre-M8.7 startup / health-only behavior."""
    if health_only and app.is_started:
        results = app.run_health_checks()
        for health in results:
            status = "OK" if health.is_healthy else "FAIL"
            logger.info("%s — %s: %s", status, health.name, health.message)
        return 0

    report = app.startup()
    print(report.summary())

    app.shutdown()
    return 0 if report.success else 1


def _run_once_command(settings: Settings, args: argparse.Namespace) -> int:
    """Startup → factory → run_once → summary. Domain logic stays in runtime."""
    try:
        daily_pnl_pct = Decimal(str(args.daily_pnl_pct))
    except (InvalidOperation, ValueError) as exc:
        logger.error("Invalid --daily-pnl-pct: %s", args.daily_pnl_pct)
        raise ConfigurationError(
            f"Invalid --daily-pnl-pct={args.daily_pnl_pct!r}"
        ) from exc

    if args.bar_limit <= 0:
        raise ConfigurationError("--bar-limit must be a positive integer")

    app = TradingBotApplication(settings)
    report = app.startup()
    if not report.success:
        print(report.summary())
        app.shutdown()
        return 1

    exit_code = 0
    try:
        execution = _execution_from_args(args)
        runtime = create_trading_runtime_from_app(
            app,
            execution=execution,  # type: ignore[arg-type]
            live_command="run-once",
        )
        context = RuntimeContext(
            symbol=args.symbol or settings.default_symbol,
            mode=_context_mode_for_execution(execution),
            strategy_name=args.strategy,
            bar_limit=args.bar_limit,
            daily_pnl_pct=daily_pnl_pct,
        )
        result = runtime.run_once(context)
        _print_pipeline_result(result)
    except ConfigurationError:
        raise
    except Exception:
        logger.exception("Unexpected error during run-once")
        exit_code = 2
    finally:
        app.shutdown()

    return exit_code


def _run_session_command(settings: Settings, args: argparse.Namespace) -> int:
    """Startup → factory → SessionRunner → summary. Domain logic stays in runtime."""
    if args.bar_limit <= 0:
        raise ConfigurationError("--bar-limit must be a positive integer")

    # Fail fast on cycles before application startup (SessionRunner enforces too).
    if (
        not isinstance(args.cycles, int)
        or isinstance(args.cycles, bool)
        or args.cycles < 1
        or args.cycles > MAX_SESSION_CYCLES
    ):
        raise ConfigurationError(
            f"--cycles must be an int in 1..{MAX_SESSION_CYCLES}; got {args.cycles!r}"
        )

    app = TradingBotApplication(settings)
    report = app.startup()
    if not report.success:
        print(report.summary())
        app.shutdown()
        return 1

    exit_code = 0
    try:
        execution = _execution_from_args(args)
        runtime = create_trading_runtime_from_app(
            app,
            execution=execution,  # type: ignore[arg-type]
            live_command="run-session",
        )
        config = SessionConfig(
            cycles=args.cycles,
            symbol=args.symbol or settings.default_symbol,
            strategy_name=args.strategy,
            bar_limit=args.bar_limit,
            mode=_context_mode_for_execution(execution),
        )
        session_result = SessionRunner(runtime).run(config)
        _print_session_result(session_result)
    except ConfigurationError:
        raise
    except Exception:
        logger.exception("Unexpected error during run-session")
        exit_code = 2
    finally:
        app.shutdown()

    return exit_code


def _parse_commission_pct(settings: Settings, raw: str | None) -> Decimal:
    if raw is None:
        return settings.backtest_commission_pct
    try:
        value = Decimal(str(raw))
    except (InvalidOperation, ValueError) as exc:
        raise ConfigurationError(f"Invalid --commission-pct={raw!r}") from exc
    if value < 0:
        raise ConfigurationError(f"--commission-pct must be >= 0; got {value}")
    return value


def _resolve_backtest_timeframe(settings: Settings, raw: str | None) -> TimeFrame:
    text = (raw or settings.default_timeframe).strip()
    try:
        return TimeFrame(text)
    except ValueError as exc:
        raise ConfigurationError(f"Invalid timeframe={text!r}") from exc


def _load_backtest_bars(
    settings: Settings,
    args: argparse.Namespace,
    *,
    symbol: str,
    timeframe: TimeFrame,
) -> list:
    if args.bars_file is not None:
        return load_bars_from_csv(
            args.bars_file,
            symbol=symbol,
            timeframe=timeframe,
        )
    if args.synthetic_bars is not None:
        return make_synthetic_bars(
            args.synthetic_bars,
            symbol=symbol,
            timeframe=timeframe,
        )
    raise ConfigurationError(
        "run-backtest requires --bars-file or --synthetic-bars "
        "(Yahoo/network market data is not used on this path)"
    )


def _run_backtest_command(settings: Settings, args: argparse.Namespace) -> int:
    """Bounded historical paper backtest (Option A — no factory broker path)."""
    if settings.trading_mode != "paper":
        raise ConfigurationError(
            f"run-backtest requires trading_mode='paper'; got {settings.trading_mode!r} "
            "(TradingMode.BACKTEST / trading_mode=backtest are not used)"
        )
    if args.bar_limit <= 0:
        raise ConfigurationError("--bar-limit must be a positive integer")
    if args.warmup_bars is not None and (
        not isinstance(args.warmup_bars, int)
        or isinstance(args.warmup_bars, bool)
        or args.warmup_bars < 1
    ):
        raise ConfigurationError(
            f"--warmup-bars must be a positive int; got {args.warmup_bars!r}"
        )
    if args.max_cycles is not None and (
        not isinstance(args.max_cycles, int)
        or isinstance(args.max_cycles, bool)
        or args.max_cycles < 1
        or args.max_cycles > MAX_BACKTEST_CYCLES
    ):
        raise ConfigurationError(
            f"--max-cycles must be an int in 1..{MAX_BACKTEST_CYCLES}; "
            f"got {args.max_cycles!r}"
        )

    symbol = args.symbol or settings.default_symbol
    timeframe = _resolve_backtest_timeframe(settings, args.timeframe)
    commission_pct = _parse_commission_pct(settings, args.commission_pct)
    bars = _load_backtest_bars(settings, args, symbol=symbol, timeframe=timeframe)

    provider = HistoricalMarketDataProvider(
        bars,
        symbol=symbol,
        timeframe=timeframe,
        initial_end_exclusive=0,
    )
    market_data = HistoricalRuntimeMarketData(provider)

    app = TradingBotApplication(settings)
    report = app.startup()
    if not report.success:
        print(report.summary())
        app.shutdown()
        return 1

    exit_code = 0
    try:
        strategy_engine = app.get_module("strategy_engine")
        # Always simulation: CommissionDryRunExecutor (fee may be 0) — never BrokerOrderExecutor.
        executor: DryRunExecutor | CommissionDryRunExecutor
        if commission_pct == 0:
            executor = DryRunExecutor()
        else:
            executor = CommissionDryRunExecutor(commission_pct)

        runtime = BasicTradingRuntime(
            settings=settings,
            market_data=market_data,
            strategy_engine=strategy_engine,
            risk_manager=BasicRiskManager(settings),
            portfolio=Portfolio(cash=settings.backtest_initial_capital),
            executor=executor,
            order_manager=None,
            alert_notifier=None,
        )
        result = BacktestRunner(runtime, historical=market_data).run(
            BacktestConfig(
                symbol=symbol,
                strategy_name=args.strategy,
                bar_limit=args.bar_limit,
                warmup_bars=args.warmup_bars,
                max_cycles=args.max_cycles,
            )
        )
        _print_backtest_result(result)
    except ConfigurationError:
        raise
    except Exception:
        logger.exception("Unexpected error during run-backtest")
        exit_code = 2
    finally:
        app.shutdown()

    return exit_code


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    try:
        settings = load_settings(args.env_file)
        setup_logging(settings)

        logger.info(
            "Starting %s v%s [%s]",
            settings.app_name,
            settings.app_version,
            settings.environment,
        )

        if args.command == "run-once":
            return _run_once_command(settings, args)

        if args.command == "run-session":
            return _run_session_command(settings, args)

        if args.command == "run-backtest":
            return _run_backtest_command(settings, args)

        if args.command == "run-paper-operator":
            return _run_paper_operator_command(settings, args)

        app = TradingBotApplication(settings)
        return _run_startup(app, health_only=args.health_only)

    except KeyboardInterrupt:
        logger.info("Interrupted by user")
        return 130
    except ConfigurationError as exc:
        logger.error("Configuration error: %s", exc)
        return 1
    except Exception as exc:
        logger.exception("Fatal error: %s", exc)
        return 2


if __name__ == "__main__":
    sys.exit(main())
