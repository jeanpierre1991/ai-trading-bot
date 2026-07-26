#!/usr/bin/env python3
"""AI Trading Bot — application entry point, startup verifier, and run-once CLI."""

from __future__ import annotations

import argparse
import logging
import sys
from decimal import Decimal, InvalidOperation

from config.loader import load_settings
from config.settings import Settings
from core.application import TradingBotApplication
from core.exceptions import ConfigurationError
from core.types import TradingMode
from runtime.context import RuntimeContext
from runtime.factory import create_trading_runtime_from_app
from runtime.models import PipelineResult
from utilities.logging_setup import setup_logging

logger = logging.getLogger("trading_bot.main")


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
        help="Run one paper/dry-run decision cycle (Milestone 8)",
    )
    execution = run_once_parser.add_mutually_exclusive_group()
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

    return parser.parse_args(argv)


def _execution_from_args(args: argparse.Namespace) -> str:
    """Map CLI flags to factory execution. Default is dry_run."""
    if getattr(args, "paper", False):
        return "paper"
    return "dry_run"


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
        runtime = create_trading_runtime_from_app(app, execution=execution)
        context = RuntimeContext(
            symbol=args.symbol or settings.default_symbol,
            mode=TradingMode.PAPER,
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
