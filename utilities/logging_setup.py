"""Logging configuration for the trading bot."""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from config.settings import Settings
from utilities.helpers import ensure_directory


def setup_logging(settings: Settings) -> logging.Logger:
    """Configure root and application loggers."""
    log_dir = ensure_directory(settings.log_dir)
    log_file = log_dir / "trading_bot.log"

    level = getattr(logging, settings.log_level.upper(), logging.INFO)
    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.setLevel(level)

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    console_handler.setLevel(level)
    root_logger.addHandler(console_handler)

    file_handler = RotatingFileHandler(
        log_file,
        maxBytes=5 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    file_handler.setLevel(level)
    root_logger.addHandler(file_handler)

    app_logger = logging.getLogger("trading_bot")
    app_logger.setLevel(level)

    logging.getLogger("trading_bot").info(
        "Logging initialized: level=%s file=%s",
        settings.log_level,
        log_file.resolve(),
    )

    return app_logger
