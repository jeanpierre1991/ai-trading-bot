"""Alpaca venue adapter (Adapter #1) — paper/sandbox only for M13.1.

Core trading components must not import Alpaca types. Future venues (IBKR,
TradeStation, Webull, …) should implement ``broker_interface.broker.Broker``
the same way without changing strategies, risk, OM, portfolio, or gates.
"""

from __future__ import annotations

from broker_interface.alpaca.adapter import (
    ALPACA_PAPER_BASE_URL,
    AlpacaBroker,
    AlpacaBrokerConfig,
    alpaca_paper_broker_from_settings,
)

__all__ = [
    "ALPACA_PAPER_BASE_URL",
    "AlpacaBroker",
    "AlpacaBrokerConfig",
    "alpaca_paper_broker_from_settings",
]
