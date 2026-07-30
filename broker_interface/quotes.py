"""Market-data-derived quote abstractions for paper fills (Milestone 11.1).

``PaperBroker`` never contacts the network. When a ``QuoteSource`` is injected,
fill prices come exclusively from that abstraction (latest closed bar close).
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any, Protocol, runtime_checkable

from core.types import Symbol

_PRICE = Decimal("0.0001")


class QuoteUnavailableError(Exception):
    """Raised when a quote source cannot produce a valid closed-bar price."""


@runtime_checkable
class QuoteSource(Protocol):
    """Provides the latest closed-bar close for a symbol (no network I/O here)."""

    def get_closed_bar_price(self, symbol: Symbol | str) -> Decimal:
        """Return a finite, positive close price for ``symbol``.

        Raises:
            QuoteUnavailableError: when no valid price is available.
        """
        ...


class ClosedBarQuoteSource:
    """Resolves fill price from ``market_data.get_bars`` last bar close.

    Expects a runtime-style market-data object exposing
    ``get_bars(symbol=..., limit=...)`` (e.g. ``MarketDataModule``).
    Does not perform network calls itself.
    """

    def __init__(self, market_data: Any, *, bar_limit: int = 1) -> None:
        if market_data is None:
            raise QuoteUnavailableError("market_data is required for ClosedBarQuoteSource")
        if not isinstance(bar_limit, int) or isinstance(bar_limit, bool) or bar_limit < 1:
            raise QuoteUnavailableError(
                f"bar_limit must be a positive int; got {bar_limit!r}"
            )
        self._market_data = market_data
        self._bar_limit = bar_limit

    @property
    def market_data(self) -> Any:
        return self._market_data

    def get_closed_bar_price(self, symbol: Symbol | str) -> Decimal:
        resolved = str(symbol).strip() if symbol is not None else ""
        if not resolved:
            raise QuoteUnavailableError("symbol must be a non-empty string")

        try:
            bars = self._market_data.get_bars(symbol=resolved, limit=self._bar_limit)
        except TypeError:
            # Some providers use positional (symbol, timeframe, limit); not used
            # by runtime MD. Surface as unavailable rather than inventing a price.
            raise QuoteUnavailableError(
                f"market_data.get_bars failed for symbol {resolved!r}"
            ) from None
        except Exception as exc:  # noqa: BLE001 - quote path must fail closed
            raise QuoteUnavailableError(
                f"market_data.get_bars failed for symbol {resolved!r}: {exc}"
            ) from exc

        if not bars:
            raise QuoteUnavailableError(
                f"no closed bars available for symbol {resolved!r}"
            )

        bar = bars[-1]
        bar_symbol = _bar_symbol(bar)
        if bar_symbol and bar_symbol != resolved:
            raise QuoteUnavailableError(
                f"closed bar symbol {bar_symbol!r} does not match requested "
                f"{resolved!r}"
            )

        try:
            close = _bar_close(bar)
        except (InvalidOperation, TypeError, ValueError, KeyError, AttributeError) as exc:
            raise QuoteUnavailableError(
                f"invalid closed bar close for symbol {resolved!r}: {exc}"
            ) from exc

        return _validate_price(close, symbol=resolved)


def _bar_symbol(bar: Any) -> str:
    if isinstance(bar, dict):
        return str(bar.get("symbol", "")).strip()
    symbol = getattr(bar, "symbol", None)
    if symbol is None:
        return ""
    return str(symbol).strip()


def _bar_close(bar: Any) -> Decimal:
    if hasattr(bar, "close") and not isinstance(bar, type):
        # MarketBar.close property or object attribute
        try:
            value = bar.close
            if not callable(value):
                return Decimal(str(value))
        except Exception:  # noqa: BLE001 - fall through to mapping access
            pass
    if isinstance(bar, dict) and "close" in bar:
        return Decimal(str(bar["close"]))
    raise ValueError("bar has no close price")


def _validate_price(price: Decimal, *, symbol: str) -> Decimal:
    if not isinstance(price, Decimal):
        try:
            price = Decimal(str(price))
        except (InvalidOperation, ValueError) as exc:
            raise QuoteUnavailableError(
                f"non-numeric quote for symbol {symbol!r}"
            ) from exc
    if not price.is_finite():
        raise QuoteUnavailableError(
            f"non-finite quote for symbol {symbol!r}: {price}"
        )
    if price <= 0:
        raise QuoteUnavailableError(
            f"non-positive quote for symbol {symbol!r}: {price}"
        )
    return price.quantize(_PRICE)
