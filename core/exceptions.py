"""Custom exceptions for the trading bot application."""


class TradingBotError(Exception):
    """Base exception for all trading bot errors."""


class ConfigurationError(TradingBotError):
    """Raised when configuration is invalid or missing."""


class ModuleLoadError(TradingBotError):
    """Raised when a module fails to load or initialize."""


class ModuleHealthError(TradingBotError):
    """Raised when a module health check fails."""


class BrokerError(TradingBotError):
    """Raised when broker communication fails."""


class RiskViolationError(TradingBotError):
    """Raised when a trade violates risk management rules."""
