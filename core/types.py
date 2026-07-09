"""Shared type definitions used across the trading bot."""

from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any, NewType

Symbol = NewType("Symbol", str)
OrderId = NewType("OrderId", str)
PositionId = NewType("PositionId", str)


class Side(str, Enum):
    BUY = "buy"
    SELL = "sell"


class OrderType(str, Enum):
    MARKET = "market"
    LIMIT = "limit"
    STOP = "stop"
    STOP_LIMIT = "stop_limit"


class TimeFrame(str, Enum):
    M1 = "1m"
    M5 = "5m"
    M15 = "15m"
    H1 = "1h"
    H4 = "4h"
    D1 = "1d"
    W1 = "1w"


class TradingMode(str, Enum):
    PAPER = "paper"
    LIVE = "live"
    BACKTEST = "backtest"


class SignalAction(str, Enum):
    HOLD = "hold"
    BUY = "buy"
    SELL = "sell"
    CLOSE = "close"


JSONDict = dict[str, Any]


class MarketBar(dict[str, Any]):
    """OHLCV bar represented as a typed dictionary."""

    @property
    def timestamp(self) -> datetime:
        return self["timestamp"]

    @property
    def open(self) -> Decimal:
        return Decimal(str(self["open"]))

    @property
    def high(self) -> Decimal:
        return Decimal(str(self["high"]))

    @property
    def low(self) -> Decimal:
        return Decimal(str(self["low"]))

    @property
    def close(self) -> Decimal:
        return Decimal(str(self["close"]))

    @property
    def volume(self) -> Decimal:
        return Decimal(str(self["volume"]))
