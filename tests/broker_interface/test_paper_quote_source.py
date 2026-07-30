"""M11.1 PaperBroker QuoteSource / closed-bar fill price tests."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from broker_interface.broker import PaperBroker
from broker_interface.execution import ExecutionStatus
from broker_interface.orders import BrokerOrderRequest
from broker_interface.quotes import (
    ClosedBarQuoteSource,
    QuoteUnavailableError,
)
from core.types import MarketBar, OrderType, Side, SignalAction, Symbol
from portfolio_manager.portfolio import Portfolio
from risk_manager.basic import BasicRiskManager
from runtime.broker_executor import BrokerOrderExecutor
from runtime.context import RuntimeContext
from runtime.trading_runtime import BasicTradingRuntime
from strategy_engine.signal import StrategySignal
from config.settings import Settings


def _bar(
    *,
    symbol: str = "AAPL",
    close: Decimal = Decimal("123.45"),
) -> MarketBar:
    return MarketBar(
        timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
        open=close,
        high=close + Decimal("1"),
        low=close - Decimal("1"),
        close=close,
        volume=Decimal("1000"),
        symbol=symbol,
        timeframe="1h",
    )


def _request(
    *,
    symbol: str = "AAPL",
    side: Side = Side.BUY,
    quantity: Decimal = Decimal("1"),
) -> BrokerOrderRequest:
    return BrokerOrderRequest(
        symbol=Symbol(symbol),
        side=side,
        order_type=OrderType.MARKET,
        quantity=quantity,
    )


def test_paper_broker_uses_injected_quote_source_price() -> None:
    source = MagicMock()
    source.get_closed_bar_price.return_value = Decimal("155.5000")
    broker = PaperBroker(buying_power=Decimal("100000"), quote_source=source)
    broker.connect()

    result = broker.place_order(_request(quantity=Decimal("2")))

    assert result.status is ExecutionStatus.FILLED
    assert result.fill_price == Decimal("155.5000")
    source.get_closed_bar_price.assert_called_once_with(Symbol("AAPL"))
    # Must not use legacy static AAPL quote 190.25
    assert result.fill_price != Decimal("190.25")


def test_quote_source_symbol_specific_prices() -> None:
    md = MagicMock()

    def _bars(symbol=None, limit=1):  # type: ignore[no-untyped-def]
        if symbol == "AAPL":
            return [_bar(symbol="AAPL", close=Decimal("111.1100"))]
        if symbol == "MSFT":
            return [_bar(symbol="MSFT", close=Decimal("222.2200"))]
        return []

    md.get_bars.side_effect = _bars
    source = ClosedBarQuoteSource(md)
    broker = PaperBroker(buying_power=Decimal("1000000"), quote_source=source)
    broker.connect()

    aapl = broker.place_order(_request(symbol="AAPL"))
    msft = broker.place_order(_request(symbol="MSFT"))

    assert aapl.status is ExecutionStatus.FILLED
    assert msft.status is ExecutionStatus.FILLED
    assert aapl.fill_price == Decimal("111.1100")
    assert msft.fill_price == Decimal("222.2200")


def test_missing_quote_rejects_safely_no_static_fallback() -> None:
    md = MagicMock()
    md.get_bars.return_value = []
    broker = PaperBroker(
        buying_power=Decimal("100000"),
        quote_source=ClosedBarQuoteSource(md),
    )
    broker.connect()
    before = broker.get_status().buying_power

    result = broker.place_order(_request())

    assert result.status is ExecutionStatus.REJECTED
    assert result.filled_quantity == Decimal("0")
    assert result.fill_price == Decimal("0")
    assert "quote unavailable" in result.message.lower()
    assert broker.get_status().buying_power == before


def test_invalid_non_positive_quote_rejects() -> None:
    source = MagicMock()
    source.get_closed_bar_price.side_effect = QuoteUnavailableError(
        "non-positive quote for symbol 'AAPL': 0"
    )
    broker = PaperBroker(buying_power=Decimal("100000"), quote_source=source)
    broker.connect()

    result = broker.place_order(_request())

    assert result.status is ExecutionStatus.REJECTED
    assert "quote unavailable" in result.message.lower()


def test_closed_bar_quote_source_rejects_non_positive_close() -> None:
    md = MagicMock()
    md.get_bars.return_value = [_bar(close=Decimal("0"))]
    source = ClosedBarQuoteSource(md)

    with pytest.raises(QuoteUnavailableError, match="non-positive"):
        source.get_closed_bar_price("AAPL")


def test_closed_bar_quote_source_rejects_non_finite_close() -> None:
    md = MagicMock()
    md.get_bars.return_value = [_bar(close=Decimal("NaN"))]
    source = ClosedBarQuoteSource(md)

    with pytest.raises(QuoteUnavailableError, match="non-finite"):
        source.get_closed_bar_price("AAPL")


def test_closed_bar_quote_source_rejects_symbol_mismatch() -> None:
    md = MagicMock()
    md.get_bars.return_value = [_bar(symbol="MSFT", close=Decimal("10"))]
    source = ClosedBarQuoteSource(md)

    with pytest.raises(QuoteUnavailableError, match="does not match"):
        source.get_closed_bar_price("AAPL")


def test_legacy_paper_broker_without_quote_source_keeps_static_map() -> None:
    broker = PaperBroker(buying_power=Decimal("100000"))
    broker.connect()

    result = broker.place_order(_request())

    assert result.status is ExecutionStatus.FILLED
    assert result.fill_price == Decimal("190.25")


def test_integration_closed_bar_price_becomes_paper_fill_price() -> None:
    """Mocked market-data closed-bar price P → paper fill price P via runtime."""
    price = Decimal("187.6543")
    md = MagicMock()
    md.get_bars.return_value = [_bar(symbol="AAPL", close=price)]
    source = ClosedBarQuoteSource(md)
    paper = PaperBroker(buying_power=Decimal("100000"), quote_source=source)
    paper.connect()
    settings = Settings(trading_mode="paper", max_position_size_pct=Decimal("0.05"))
    strategy = MagicMock()
    strategy.evaluate.return_value = StrategySignal(
        symbol="AAPL",
        action=SignalAction.BUY,
        confidence=0.9,
        strategy_name="ema_crossover",
        price=price,
    )
    runtime = BasicTradingRuntime(
        settings=settings,
        market_data=md,
        strategy_engine=strategy,
        risk_manager=BasicRiskManager(settings),
        portfolio=Portfolio(cash=Decimal("100000")),
        executor=BrokerOrderExecutor(paper),
    )

    result = runtime.run_once(
        RuntimeContext(symbol="AAPL", daily_pnl_pct=Decimal("0"))
    )

    assert result.success is True
    assert result.execution is not None
    assert result.execution.status is ExecutionStatus.FILLED
    assert result.execution.fill_price == price.quantize(Decimal("0.0001"))
