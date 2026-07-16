"""Parity tests between productive and Milestone 3 EMA crossover strategies."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from core.types import MarketBar, SignalAction
from strategy_engine.strategies.ema_crossover import EmaCrossoverStrategy
from strategy_engine.strategies.ema_crossover_strategy import EMACrossoverStrategy


def _bars_from_closes(closes: list[Decimal]) -> list[MarketBar]:
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return [
        MarketBar(
            timestamp=base + timedelta(hours=index),
            open=close,
            high=close,
            low=close,
            close=close,
            volume=Decimal("1000"),
            symbol="AAPL",
            timeframe="1h",
        )
        for index, close in enumerate(closes)
    ]


def _assert_signals_match(productive, milestone) -> None:
    assert milestone.action == productive.action
    assert milestone.confidence == productive.confidence
    assert milestone.symbol == productive.symbol
    assert milestone.price == productive.price
    assert milestone.strategy_name == productive.strategy_name
    assert milestone.metadata == productive.metadata


@pytest.mark.parametrize(
    ("fast_period", "slow_period", "closes", "symbol"),
    [
        (2, 3, [], "AAPL"),
        (2, 3, [Decimal("100"), Decimal("101")], "AAPL"),
        (
            2,
            4,
            [
                Decimal(str(value))
                for value in [
                    50,
                    49,
                    48,
                    47,
                    46,
                    45,
                    44,
                    43,
                    42,
                    41,
                    40,
                    39,
                    38,
                    37,
                    36,
                    35,
                    34,
                    33,
                    32,
                    31,
                    31,
                    32,
                    33,
                ]
            ],
            "AAPL",
        ),
        (
            2,
            4,
            [
                Decimal(str(value))
                for value in [
                    30,
                    31,
                    32,
                    33,
                    34,
                    35,
                    36,
                    37,
                    38,
                    39,
                    40,
                    41,
                    42,
                    43,
                    44,
                    45,
                    46,
                    47,
                    48,
                    49,
                    49,
                    48,
                    47,
                ]
            ],
            "MSFT",
        ),
        (2, 4, [Decimal(str(100 + i)) for i in range(20)], "AAPL"),
        (12, 26, [Decimal(str(100 + (i % 7) * 0.5)) for i in range(80)], "GOOGL"),
    ],
)
def test_emacrossover_matches_productive_signals(
    fast_period: int,
    slow_period: int,
    closes: list[Decimal],
    symbol: str,
) -> None:
    bars = _bars_from_closes(closes)
    productive = EmaCrossoverStrategy(fast_period=fast_period, slow_period=slow_period)
    milestone = EMACrossoverStrategy(fast_period=fast_period, slow_period=slow_period)

    productive_signal = productive.evaluate(bars, symbol=symbol)
    milestone_signal = milestone.evaluate(bars, symbol=symbol)

    _assert_signals_match(productive_signal, milestone_signal)


def test_emacrossover_parity_covers_buy_sell_and_hold() -> None:
    cases = {
        "buy": [
            Decimal(str(value))
            for value in [50, 49, 48, 47, 46, 45, 44, 43, 42, 41, 40, 39, 38, 37, 36, 35, 34, 33, 32, 31, 31, 32, 33]
        ],
        "sell": [
            Decimal(str(value))
            for value in [30, 31, 32, 33, 34, 35, 36, 37, 38, 39, 40, 41, 42, 43, 44, 45, 46, 47, 48, 49, 49, 48, 47]
        ],
        "hold": [Decimal(str(100 + i)) for i in range(20)],
    }
    expected = {
        "buy": SignalAction.BUY,
        "sell": SignalAction.SELL,
        "hold": SignalAction.HOLD,
    }

    for label, closes in cases.items():
        bars = _bars_from_closes(closes)
        productive = EmaCrossoverStrategy(fast_period=2, slow_period=4).evaluate(bars, symbol="AAPL")
        milestone = EMACrossoverStrategy(fast_period=2, slow_period=4).evaluate(bars, symbol="AAPL")

        assert productive.action == expected[label]
        _assert_signals_match(productive, milestone)


def test_emacrossover_constructor_parity() -> None:
    productive = EmaCrossoverStrategy(fast_period=5, slow_period=20)
    milestone = EMACrossoverStrategy(fast_period=5, slow_period=20)

    assert productive.name == milestone.name == "ema_crossover"
    assert productive.fast_period == milestone.fast_period == 5
    assert productive.slow_period == milestone.slow_period == 20
