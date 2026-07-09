#!/usr/bin/env python3
"""AI Trading Bot — application entry point and startup verifier."""

from __future__ import annotations

import argparse
import logging
import sys

from config.loader import load_settings
from core.application import TradingBotApplication
from utilities.logging_setup import setup_logging

logger = logging.getLogger("trading_bot.main")


def parse_args() -> argparse.Namespace:
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
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    try:
        settings = load_settings(args.env_file)
        setup_logging(settings)

        logger.info("Starting %s v%s [%s]", settings.app_name, settings.app_version, settings.environment)

        app = TradingBotApplication(settings)

        if args.health_only and app.is_started:
            results = app.run_health_checks()
            for health in results:
                status = "OK" if health.is_healthy else "FAIL"
                logger.info("%s — %s: %s", status, health.name, health.message)
            return 0

        report = app.startup()
        print(report.summary())

        if report.success:
            app.shutdown()
            return 0

        app.shutdown()
        return 1

    except KeyboardInterrupt:
        logger.info("Interrupted by user")
        return 130
    except Exception as exc:
        logger.exception("Fatal error during startup: %s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
