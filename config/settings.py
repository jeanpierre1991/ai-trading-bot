"""Application settings loaded from environment variables and .env file."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Central configuration for the trading bot."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_nested_delimiter="__",
        case_sensitive=False,
        extra="ignore",
    )

    # Application
    app_name: str = "AI Trading Bot"
    app_version: str = "0.1.0"
    environment: Literal["development", "staging", "production"] = "development"
    debug: bool = False
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    log_dir: Path = Path("logs")

    # Trading
    trading_mode: Literal["paper", "live", "backtest"] = "paper"
    default_symbol: str = "AAPL"
    default_timeframe: str = "1h"
    base_currency: str = "USD"

    # Risk management defaults
    max_position_size_pct: Decimal = Decimal("0.05")
    max_daily_loss_pct: Decimal = Decimal("0.02")
    max_open_positions: int = 10

    # M13.2 live enablement (default deny; BROKER_SANDBOX only when all gates pass)
    live_trading_enabled: bool = False
    live_confirm_token: str = ""
    broker_endpoint_class: Literal[
        "local_paper",
        "broker_sandbox",
        "live_production",
    ] = "local_paper"
    live_max_order_notional: Decimal | None = None
    live_max_orders_per_day: int | None = None
    live_max_gross_notional: Decimal | None = None
    # M13.3: atomic JSON live order ledger (required for execution=live)
    live_order_ledger_path: Path | None = None
    # R2: relative epsilon for avg entry reconcile
    live_entry_price_epsilon: Decimal = Decimal("0.0001")

    # Broker
    broker_name: str = "paper"
    broker_api_key: str = ""
    broker_api_secret: str = ""
    broker_base_url: str = "https://paper-api.example.com"

    # Market data
    market_data_provider: str = "mock"
    market_data_api_key: str = ""
    # M11.2 freshness (wall-clock; not market-hours — see M11.3)
    market_data_freshness_enabled: bool = True
    market_data_max_age_seconds: int | None = None
    market_data_freshness_bar_periods: int = 2
    market_data_freshness_slack_seconds: int = 120
    market_data_future_skew_seconds: int = 60
    # M11.3 market hours / session awareness (US equity XNYS)
    market_hours_enabled: bool = True
    market_hours_policy: Literal["allow", "reject"] = "allow"
    market_hours_calendar: str = "xnys"
    market_hours_timezone: str = "America/New_York"

    # AI engine
    ai_provider: str = "mock"
    ai_model: str = "gpt-4"
    ai_api_key: str = ""
    ai_temperature: float = 0.2

    # News engine
    news_provider: str = "mock"
    news_api_key: str = ""

    # Alerts
    alerts_enabled: bool = True
    alert_email: str = ""
    alert_webhook_url: str = ""

    # Backtesting
    backtest_initial_capital: Decimal = Decimal("100000")
    backtest_commission_pct: Decimal = Decimal("0.001")

    @field_validator("log_dir", mode="before")
    @classmethod
    def _coerce_log_dir(cls, value: str | Path) -> Path:
        return Path(value)

    @field_validator("live_order_ledger_path", mode="before")
    @classmethod
    def _empty_path_to_none(cls, value: object) -> object:
        if value is None:
            return None
        if isinstance(value, str) and not value.strip():
            return None
        return Path(value) if not isinstance(value, Path) else value

    @field_validator(
        "live_max_order_notional",
        "live_max_gross_notional",
        mode="before",
    )
    @classmethod
    def _empty_decimal_to_none(cls, value: object) -> object:
        if value is None:
            return None
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("live_max_orders_per_day", mode="before")
    @classmethod
    def _empty_int_to_none(cls, value: object) -> object:
        if value is None:
            return None
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def is_paper_trading(self) -> bool:
        return self.trading_mode == "paper"
