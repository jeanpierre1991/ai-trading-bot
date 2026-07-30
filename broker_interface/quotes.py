"""Market-data-derived quote abstractions for paper fills (Milestone 11.1–11.3).

``PaperBroker`` never contacts the network. When a ``QuoteSource`` is injected,
fill prices come exclusively from that abstraction (latest closed bar close).
M11.2/M11.3 optionally validate last-bar freshness (session-aware) and hours policy.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, Protocol, runtime_checkable

from core.types import Symbol
from market_data.freshness import (
    evaluate_bar_freshness,
    resolve_max_age_seconds,
    utc_now,
)
from market_data.session_calendar import (
    MarketHoursPolicy,
    SessionCalendar,
    freshness_reference_now,
    is_trading_permitted,
)

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

    def __init__(
        self,
        market_data: Any,
        *,
        bar_limit: int = 1,
        freshness_enabled: bool = True,
        timeframe: str = "1h",
        max_age_seconds: int | None = None,
        bar_periods: int = 2,
        slack_seconds: int = 120,
        future_skew_seconds: int = 60,
        clock: Callable[[], datetime] | None = None,
        session_calendar: SessionCalendar | None = None,
        market_hours_enabled: bool = True,
        market_hours_policy: str = "allow",
    ) -> None:
        if market_data is None:
            raise QuoteUnavailableError("market_data is required for ClosedBarQuoteSource")
        if not isinstance(bar_limit, int) or isinstance(bar_limit, bool) or bar_limit < 1:
            raise QuoteUnavailableError(
                f"bar_limit must be a positive int; got {bar_limit!r}"
            )
        self._market_data = market_data
        self._bar_limit = bar_limit
        self._freshness_enabled = freshness_enabled
        self._timeframe = timeframe
        self._max_age_seconds = max_age_seconds
        self._bar_periods = bar_periods
        self._slack_seconds = slack_seconds
        self._future_skew_seconds = future_skew_seconds
        self._clock = clock if clock is not None else utc_now
        self._session_calendar = session_calendar
        self._market_hours_enabled = market_hours_enabled
        self._market_hours_policy = str(market_hours_policy).strip().lower()

    @property
    def market_data(self) -> Any:
        return self._market_data

    @property
    def freshness_enabled(self) -> bool:
        return self._freshness_enabled

    def get_closed_bar_price(self, symbol: Symbol | str) -> Decimal:
        resolved = str(symbol).strip() if symbol is not None else ""
        if not resolved:
            raise QuoteUnavailableError("symbol must be a non-empty string")

        try:
            bars = self._market_data.get_bars(symbol=resolved, limit=self._bar_limit)
        except TypeError:
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

        wall_now = self._clock()
        if self._market_hours_enabled:
            try:
                policy = MarketHoursPolicy(self._market_hours_policy)
            except ValueError as exc:
                raise QuoteUnavailableError(
                    f"market hours policy invalid: {self._market_hours_policy!r}"
                ) from exc
            if policy is MarketHoursPolicy.REJECT:
                if self._session_calendar is None:
                    raise QuoteUnavailableError(
                        "market hours reject policy requires a session calendar"
                    )
                snapshot = self._session_calendar.resolve(wall_now)
                if not is_trading_permitted(snapshot, policy):
                    detail = snapshot.reason or snapshot.state.value
                    raise QuoteUnavailableError(
                        f"market hours rejected for symbol {resolved!r} "
                        f"(state={snapshot.state.value}): {detail}"
                    )

        if self._freshness_enabled:
            try:
                max_age = resolve_max_age_seconds(
                    max_age_seconds=self._max_age_seconds,
                    timeframe=self._timeframe,
                    bar_periods=self._bar_periods,
                    slack_seconds=self._slack_seconds,
                )
            except ValueError as exc:
                raise QuoteUnavailableError(f"freshness config invalid: {exc}") from exc

            reference_now = wall_now
            if self._session_calendar is not None:
                snapshot = self._session_calendar.resolve(wall_now)
                try:
                    reference_now = freshness_reference_now(snapshot, wall_now)
                except ValueError as exc:
                    raise QuoteUnavailableError(
                        f"freshness calendar reference failed: {exc}"
                    ) from exc

            freshness = evaluate_bar_freshness(
                bar,
                now_utc=reference_now,
                max_age_seconds=max_age,
                future_skew_seconds=self._future_skew_seconds,
            )
            if not freshness.ok:
                raise QuoteUnavailableError(
                    f"stale or unverifiable closed bar for symbol {resolved!r}: "
                    f"{freshness.classification}: {freshness.reason}"
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
