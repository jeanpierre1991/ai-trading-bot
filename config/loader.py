"""Configuration loading utilities."""

from __future__ import annotations

import logging
from pathlib import Path

from dotenv import load_dotenv

from config.settings import Settings
from core.exceptions import ConfigurationError

logger = logging.getLogger("trading_bot.config")


def load_settings(env_file: str | Path | None = None) -> Settings:
    """Load settings from environment variables and optional .env file."""
    env_path = Path(env_file) if env_file else Path(".env")

    if env_path.exists():
        loaded = load_dotenv(env_path, override=False)
        if loaded:
            logger.info("Loaded environment from %s", env_path.resolve())
        else:
            logger.warning("No variables loaded from %s", env_path.resolve())
    else:
        logger.info("No .env file found at %s; using environment defaults", env_path.resolve())

    try:
        settings = Settings()
    except Exception as exc:
        raise ConfigurationError(f"Invalid configuration: {exc}") from exc

    logger.debug(
        "Settings loaded: app=%s env=%s mode=%s",
        settings.app_name,
        settings.environment,
        settings.trading_mode,
    )
    return settings
