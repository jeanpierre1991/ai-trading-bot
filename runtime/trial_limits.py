"""Trial limit guard (Milestone 14.1).

Additive stricter envelope over M13.2 G10. Enforced before broker place_order.
When no trial limits are configured, the guard is a transparent pass-through
(preserves existing sandbox behavior).
"""

from __future__ import annotations

import time
import uuid
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Callable

from broker_interface.broker import Broker, BrokerStatus
from broker_interface.execution import ExecutionResult, ExecutionStatus
from broker_interface.orders import BrokerOrderRequest
from core.types import OrderId, Side, Symbol


@dataclass(frozen=True)
class TrialLimitConfig:
    """Optional trial limits. ``None`` fields are not enforced."""

    max_order_notional: Decimal | None = None
    max_orders_per_day: int | None = None
    max_orders_per_minute: int | None = None
    symbol_allowlist: frozenset[str] | None = None
    max_daily_loss_pct: Decimal | None = None

    @property
    def active(self) -> bool:
        return any(
            (
                self.max_order_notional is not None,
                self.max_orders_per_day is not None,
                self.max_orders_per_minute is not None,
                self.symbol_allowlist is not None,
                self.max_daily_loss_pct is not None,
            )
        )


class MinuteOrderRateLimiter:
    """Sliding 60s window counter for trial rate limiting."""

    def __init__(self) -> None:
        self._timestamps: deque[float] = deque()

    def current_count(self, *, now: float | None = None) -> int:
        self._prune(now)
        return len(self._timestamps)

    def record(self, *, now: float | None = None) -> None:
        ts = time.time() if now is None else float(now)
        self._prune(ts)
        self._timestamps.append(ts)

    def _prune(self, now: float | None) -> None:
        ts = time.time() if now is None else float(now)
        cutoff = ts - 60.0
        while self._timestamps and self._timestamps[0] < cutoff:
            self._timestamps.popleft()


class DayOrderCounter:
    """Simple UTC-day counter for trial max orders (independent of G10 C1)."""

    def __init__(self) -> None:
        self._day: str | None = None
        self._count = 0

    def current_count(self, *, now: datetime | None = None) -> int:
        self._roll(now)
        return self._count

    def record(self, *, now: datetime | None = None) -> None:
        self._roll(now)
        self._count += 1

    def _roll(self, now: datetime | None) -> None:
        current = now if now is not None else datetime.now(timezone.utc)
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        else:
            current = current.astimezone(timezone.utc)
        day = current.date().isoformat()
        if self._day != day:
            self._day = day
            self._count = 0


class TrialLimitGuardBroker(Broker):
    """Decorator enforcing optional trial limits before place_order."""

    def __init__(
        self,
        inner: Broker,
        *,
        config: TrialLimitConfig,
        day_counter: DayOrderCounter | None = None,
        rate_limiter: MinuteOrderRateLimiter | None = None,
        daily_pnl_pct_provider: Callable[[], Decimal | None] | None = None,
    ) -> None:
        self._inner = inner
        self._config = config
        self._day_counter = day_counter if day_counter is not None else DayOrderCounter()
        self._rate_limiter = (
            rate_limiter if rate_limiter is not None else MinuteOrderRateLimiter()
        )
        self._daily_pnl_pct_provider = daily_pnl_pct_provider

    @property
    def inner(self) -> Broker:
        return self._inner

    @property
    def config(self) -> TrialLimitConfig:
        return self._config

    def connect(self) -> bool:
        return self._inner.connect()

    def disconnect(self) -> None:
        self._inner.disconnect()

    def get_status(self) -> BrokerStatus:
        return self._inner.get_status()

    def get_quote(self, symbol: Symbol) -> Decimal:
        return self._inner.get_quote(symbol)

    def place_order(self, request: BrokerOrderRequest) -> ExecutionResult:
        if not self._config.active:
            return self._inner.place_order(request)

        symbol = str(request.symbol).strip().upper()
        if self._config.symbol_allowlist is not None:
            if symbol not in self._config.symbol_allowlist:
                return self._reject(
                    request,
                    message=(
                        f"TRIAL symbol {symbol!r} not in allowlist; "
                        "order blocked before broker submission"
                    ),
                )

        if self._config.max_daily_loss_pct is not None:
            pnl = None
            if self._daily_pnl_pct_provider is not None:
                try:
                    pnl = self._daily_pnl_pct_provider()
                except Exception:  # noqa: BLE001
                    return self._reject(
                        request,
                        message=(
                            "TRIAL daily loss check failed: pnl unavailable; "
                            "order blocked before broker submission"
                        ),
                    )
            if pnl is not None and Decimal(str(pnl)) <= -abs(
                Decimal(str(self._config.max_daily_loss_pct))
            ):
                return self._reject(
                    request,
                    message=(
                        "TRIAL_MAX_DAILY_LOSS_PCT exceeded; "
                        "order blocked before broker submission"
                    ),
                )

        if self._config.max_orders_per_day is not None:
            if self._day_counter.current_count() >= int(self._config.max_orders_per_day):
                return self._reject(
                    request,
                    message=(
                        "TRIAL_MAX_ORDERS_PER_DAY exceeded; "
                        "order blocked before broker submission"
                    ),
                )

        if self._config.max_orders_per_minute is not None:
            if self._rate_limiter.current_count() >= int(
                self._config.max_orders_per_minute
            ):
                return self._reject(
                    request,
                    message=(
                        "TRIAL_MAX_ORDERS_PER_MINUTE exceeded; "
                        "order blocked before broker submission"
                    ),
                )

        if self._config.max_order_notional is not None:
            try:
                quote = self._inner.get_quote(request.symbol)
            except Exception as exc:
                return self._reject(
                    request,
                    message=(
                        "TRIAL notional check failed: quote unavailable "
                        f"({exc.__class__.__name__})"
                    ),
                )
            notional = Decimal(str(request.quantity)) * Decimal(str(quote))
            if notional > Decimal(str(self._config.max_order_notional)):
                return self._reject(
                    request,
                    message=(
                        "TRIAL_MAX_ORDER_NOTIONAL exceeded; "
                        "order blocked before broker submission"
                    ),
                )

        # Record trial counters only for attempts that pass trial checks
        # (still before venue). G10 may still reject afterward.
        if self._config.max_orders_per_day is not None:
            self._day_counter.record()
        if self._config.max_orders_per_minute is not None:
            self._rate_limiter.record()

        return self._inner.place_order(request)

    @staticmethod
    def _reject(request: BrokerOrderRequest, *, message: str) -> ExecutionResult:
        side = request.side if isinstance(request.side, Side) else Side.BUY
        return ExecutionResult(
            order_id=OrderId(str(uuid.uuid4())),
            symbol=request.symbol,
            side=side,
            requested_quantity=request.quantity,
            filled_quantity=Decimal("0"),
            fill_price=Decimal("0"),
            fee=Decimal("0"),
            status=ExecutionStatus.REJECTED,
            message=message,
        )


def trial_config_from_settings(settings: object) -> TrialLimitConfig:
    """Build TrialLimitConfig from settings; inactive when all fields empty."""
    allowlist_raw = getattr(settings, "trial_symbol_allowlist", None)
    allowlist: frozenset[str] | None = None
    if allowlist_raw:
        if isinstance(allowlist_raw, str):
            parts = [p.strip().upper() for p in allowlist_raw.split(",") if p.strip()]
        else:
            parts = [str(p).strip().upper() for p in allowlist_raw if str(p).strip()]
        allowlist = frozenset(parts) if parts else None

    max_notional = getattr(settings, "trial_max_order_notional", None)
    max_day = getattr(settings, "trial_max_orders_per_day", None)
    max_min = getattr(settings, "trial_max_orders_per_minute", None)
    max_loss = getattr(settings, "trial_max_daily_loss_pct", None)

    return TrialLimitConfig(
        max_order_notional=(
            Decimal(str(max_notional)) if max_notional is not None else None
        ),
        max_orders_per_day=int(max_day) if max_day is not None else None,
        max_orders_per_minute=int(max_min) if max_min is not None else None,
        symbol_allowlist=allowlist,
        max_daily_loss_pct=(
            Decimal(str(max_loss)) if max_loss is not None else None
        ),
    )
