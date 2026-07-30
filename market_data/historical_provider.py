"""Deterministic in-memory historical market data (Milestone 10.1).

Network-free bar source for paper backtests. Does not fetch Yahoo or any
external API. Cursor/window semantics expose a growing prefix of bars so a
backtest runner can advance one bar at a time without mutating history.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal

from core.exceptions import ConfigurationError
from core.types import MarketBar, Symbol, TimeFrame
from market_data.provider import MarketDataProvider

# Hard cap so historical series cannot become unbounded in memory/tests.
MAX_HISTORICAL_BARS = 10_000


class HistoricalMarketDataProvider(MarketDataProvider):
    """In-memory ``MarketDataProvider`` over a fixed, ordered bar series.

    Visible history is ``bars[:end_exclusive]``. ``get_bars`` returns the
    trailing ``limit`` bars of that prefix. Call ``advance`` to reveal the
    next bar (bounded by the series length).
    """

    def __init__(
        self,
        bars: Sequence[MarketBar],
        *,
        symbol: str | Symbol | None = None,
        timeframe: TimeFrame | str | None = None,
        initial_end_exclusive: int | None = None,
    ) -> None:
        if not bars:
            raise ConfigurationError("historical bars must be a non-empty sequence")
        bar_count = len(bars)
        if bar_count > MAX_HISTORICAL_BARS:
            raise ConfigurationError(
                f"historical bars exceed MAX_HISTORICAL_BARS={MAX_HISTORICAL_BARS}; "
                f"got {bar_count}"
            )
        series = list(bars)
        if not series:
            raise ConfigurationError("historical bars must be a non-empty sequence")

        self._validate_timestamps(series)
        self._bars = series
        self._symbol = self._resolve_symbol(series, symbol)
        self._timeframe = self._resolve_timeframe(series, timeframe)

        if initial_end_exclusive is None:
            # Default: all bars visible (provider usable like a static series).
            self._end_exclusive = len(series)
        else:
            self._set_end_exclusive(initial_end_exclusive)

    @property
    def symbol(self) -> str:
        return self._symbol

    @property
    def timeframe(self) -> TimeFrame:
        return self._timeframe

    @property
    def bar_count(self) -> int:
        return len(self._bars)

    @property
    def end_exclusive(self) -> int:
        """Number of bars currently visible (exclusive end index)."""
        return self._end_exclusive

    @property
    def visible_count(self) -> int:
        return self._end_exclusive

    @property
    def remaining(self) -> int:
        return len(self._bars) - self._end_exclusive

    def reset(self, *, end_exclusive: int = 0) -> None:
        """Reset the visible window (default: no bars visible)."""
        self._set_end_exclusive(end_exclusive)

    def advance(self, steps: int = 1) -> int:
        """Reveal ``steps`` more bars. Returns the new ``end_exclusive``.

        Raises:
            ConfigurationError: if ``steps`` is not a positive int or would
                advance past the end of the series.
        """
        if not isinstance(steps, int) or isinstance(steps, bool) or steps < 1:
            raise ConfigurationError(f"steps must be a positive int; got {steps!r}")
        new_end = self._end_exclusive + steps
        if new_end > len(self._bars):
            raise ConfigurationError(
                f"cannot advance by {steps}: only {self.remaining} bar(s) remaining"
            )
        self._end_exclusive = new_end
        return self._end_exclusive

    def can_advance(self, steps: int = 1) -> bool:
        if not isinstance(steps, int) or isinstance(steps, bool) or steps < 1:
            return False
        return self._end_exclusive + steps <= len(self._bars)

    def get_latest_price(self, symbol: Symbol) -> Decimal:
        self._ensure_symbol(symbol)
        visible = self._visible_bars()
        if not visible:
            raise ConfigurationError("no visible historical bars for latest price")
        return visible[-1].close

    def get_bars(self, symbol: Symbol, timeframe: TimeFrame, limit: int) -> list[MarketBar]:
        self._ensure_symbol(symbol)
        self._ensure_timeframe(timeframe)
        if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
            raise ConfigurationError(f"limit must be a positive int; got {limit!r}")

        visible = self._visible_bars()
        if not visible:
            return []
        if limit >= len(visible):
            return list(visible)
        return list(visible[-limit:])

    def _visible_bars(self) -> list[MarketBar]:
        return self._bars[: self._end_exclusive]

    def _set_end_exclusive(self, end_exclusive: int) -> None:
        if (
            not isinstance(end_exclusive, int)
            or isinstance(end_exclusive, bool)
            or end_exclusive < 0
            or end_exclusive > len(self._bars)
        ):
            raise ConfigurationError(
                f"end_exclusive must be in 0..{len(self._bars)}; got {end_exclusive!r}"
            )
        self._end_exclusive = end_exclusive

    def _ensure_symbol(self, symbol: Symbol) -> None:
        requested = str(symbol).strip()
        if requested != self._symbol:
            raise ConfigurationError(
                f"historical series symbol is {self._symbol!r}; got {requested!r}"
            )

    def _ensure_timeframe(self, timeframe: TimeFrame) -> None:
        if not isinstance(timeframe, TimeFrame):
            raise ConfigurationError(
                f"timeframe must be a TimeFrame; got {timeframe!r}"
            )
        if timeframe is not self._timeframe:
            raise ConfigurationError(
                f"historical series timeframe is {self._timeframe.value!r}; "
                f"got {timeframe.value!r}"
            )

    @staticmethod
    def _validate_timestamps(bars: list[MarketBar]) -> None:
        previous = bars[0].timestamp
        for index in range(1, len(bars)):
            current = bars[index].timestamp
            if current <= previous:
                raise ConfigurationError(
                    "historical bars require strictly increasing timestamps; "
                    f"violation at index {index}"
                )
            previous = current

    @staticmethod
    def _resolve_symbol(
        bars: list[MarketBar],
        symbol: str | Symbol | None,
    ) -> str:
        if symbol is not None:
            resolved = str(symbol).strip()
            if not resolved:
                raise ConfigurationError("symbol must be a non-empty string")
            return resolved

        symbols = {str(bar.get("symbol", "")).strip() for bar in bars}
        symbols.discard("")
        if len(symbols) != 1:
            raise ConfigurationError(
                "historical bars must share one symbol or pass symbol= explicitly; "
                f"found {sorted(symbols)!r}"
            )
        return next(iter(symbols))

    @staticmethod
    def _resolve_timeframe(
        bars: list[MarketBar],
        timeframe: TimeFrame | str | None,
    ) -> TimeFrame:
        if timeframe is not None:
            return timeframe if isinstance(timeframe, TimeFrame) else TimeFrame(timeframe)

        values = {str(bar.get("timeframe", "")).strip() for bar in bars}
        values.discard("")
        if len(values) != 1:
            raise ConfigurationError(
                "historical bars must share one timeframe or pass timeframe= "
                f"explicitly; found {sorted(values)!r}"
            )
        return TimeFrame(next(iter(values)))


class HistoricalRuntimeMarketData:
    """Duck-types ``MarketDataModule.get_bars`` for ``BasicTradingRuntime``.

    Runtime calls ``get_bars(symbol=..., limit=...)`` without a timeframe
    argument. This adapter forwards to ``HistoricalMarketDataProvider`` using
    the provider's fixed series timeframe.
    """

    def __init__(self, provider: HistoricalMarketDataProvider) -> None:
        self._provider = provider

    @property
    def provider(self) -> HistoricalMarketDataProvider:
        return self._provider

    def get_bars(self, symbol: str | None = None, limit: int = 100) -> list[MarketBar]:
        resolved = (symbol or self._provider.symbol).strip()
        if not resolved:
            raise ConfigurationError("symbol must be a non-empty string")
        return self._provider.get_bars(
            Symbol(resolved),
            self._provider.timeframe,
            limit,
        )

    def get_price(self, symbol: str | None = None) -> Decimal:
        resolved = (symbol or self._provider.symbol).strip()
        if not resolved:
            raise ConfigurationError("symbol must be a non-empty string")
        return self._provider.get_latest_price(Symbol(resolved))
