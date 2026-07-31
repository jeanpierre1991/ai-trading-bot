"""No-submit / log-only shadow executor (Milestone 13.4).

Never constructs or calls a broker. Never writes the live order ledger.
Evaluates live caps hypothetically without consuming daily slots.
"""

from __future__ import annotations

import time
import uuid
from decimal import Decimal
from typing import Callable

from broker_interface.execution import ExecutionResult, ExecutionStatus
from broker_interface.quotes import QuoteSource, QuoteUnavailableError
from core.exceptions import ConfigurationError
from core.types import OrderId, OrderType, Side, Symbol
from runtime.executor import OrderExecutor
from runtime.live_caps import CapEvaluation, LiveOrderCounter, evaluate_order_caps
from runtime.models import TradeIntent
from runtime.shadow_compare import intended_notional
from runtime.shadow_record import (
    SHADOW_RECORD_SCHEMA_VERSION,
    ShadowAuditLog,
    ShadowCapEvaluation,
    ShadowRecord,
    decimal_to_str,
    utc_now_iso,
)


class ShadowExecutor(OrderExecutor):
    """Records a hypothetical live-intent decision; never calls place_order."""

    def __init__(
        self,
        *,
        audit: ShadowAuditLog,
        quote_source: QuoteSource,
        max_order_notional: Decimal,
        max_orders_per_day: int,
        broker_adapter_id: str,
        broker_endpoint_class: str,
        command: str,
        counter: LiveOrderCounter | None = None,
        clock_ms: Callable[[], float] | None = None,
    ) -> None:
        self._audit = audit
        self._quote_source = quote_source
        self._max_order_notional = Decimal(str(max_order_notional))
        self._max_orders_per_day = int(max_orders_per_day)
        self._broker_adapter_id = str(broker_adapter_id or "").strip() or "unknown"
        self._broker_endpoint_class = (
            str(broker_endpoint_class or "").strip() or "unknown"
        )
        self._command = str(command or "run-once").strip() or "run-once"
        self._counter = counter if counter is not None else LiveOrderCounter()
        self._clock_ms = clock_ms if clock_ms is not None else time.perf_counter

    @property
    def audit(self) -> ShadowAuditLog:
        return self._audit

    @property
    def counter(self) -> LiveOrderCounter:
        """Exposed for tests — shadow must never call record_submit."""
        return self._counter

    def execute(self, intent: TradeIntent | None) -> ExecutionResult:
        started = self._clock_ms()
        if intent is None:
            return self._reject(
                symbol=Symbol(""),
                side=Side.BUY,
                quantity=Decimal("0"),
                message="TradeIntent is required for shadow execution",
            )

        symbol_text = str(intent.symbol).strip() if intent.symbol is not None else ""
        if not symbol_text:
            return self._reject(
                symbol=Symbol(""),
                side=intent.side if isinstance(intent.side, Side) else Side.BUY,
                quantity=intent.quantity if intent.quantity is not None else Decimal("0"),
                message="Symbol must be a non-empty string",
            )

        quantity = intent.quantity if intent.quantity is not None else Decimal("0")
        if quantity <= 0:
            return self._reject(
                symbol=Symbol(symbol_text),
                side=intent.side if isinstance(intent.side, Side) else Side.BUY,
                quantity=quantity,
                message="Quantity must be positive",
            )

        if intent.side not in (Side.BUY, Side.SELL):
            return self._reject(
                symbol=Symbol(symbol_text),
                side=Side.BUY,
                quantity=quantity,
                message=f"Unsupported side for shadow execution: {intent.side!r}",
            )

        if intent.order_type is not OrderType.MARKET:
            return self._reject(
                symbol=Symbol(symbol_text),
                side=intent.side,
                quantity=quantity,
                message=(
                    f"Unsupported order_type for shadow: {intent.order_type!r} "
                    "(only MARKET is supported)"
                ),
            )

        try:
            arrival = self._quote_source.get_closed_bar_price(symbol_text)
        except QuoteUnavailableError as exc:
            raise ConfigurationError(
                f"shadow quote unavailable from market data: {exc}"
            ) from exc
        except Exception as exc:
            raise ConfigurationError(
                f"shadow quote unavailable from market data: {exc.__class__.__name__}"
            ) from exc

        count_before = self._counter.current_count()
        caps = evaluate_order_caps(
            quantity=quantity,
            quote=arrival,
            max_order_notional=self._max_order_notional,
            max_orders_per_day=self._max_orders_per_day,
            current_count=count_before,
            already_counted=False,
        )
        # Hard invariant: never consume / increment daily slots in shadow.
        if self._counter.current_count() != count_before:
            raise ConfigurationError(
                "shadow cap evaluation mutated LIVE_MAX_ORDERS_PER_DAY counter"
            )

        if not caps.order_notional_ok or not caps.daily_count_ok:
            decision = "blocked_cap"
            message = (
                "shadow no-submit: hypothetical LIVE cap breach "
                f"(notional_ok={caps.order_notional_ok} daily_ok={caps.daily_count_ok})"
            )
        else:
            decision = "would_submit"
            message = "shadow no-submit: would_submit (no broker place_order)"

        latency_ms = int(max(0.0, (self._clock_ms() - started) * 1000.0))
        notional = intended_notional(quantity=quantity, price=arrival)
        self._audit.append(
            ShadowRecord(
                schema_version=SHADOW_RECORD_SCHEMA_VERSION,
                ts_utc=utc_now_iso(),
                execution_mode="shadow",
                broker_adapter_id=self._broker_adapter_id,
                broker_endpoint_class=self._broker_endpoint_class,
                command=self._command,
                symbol=symbol_text.upper(),
                strategy_name=intent.strategy_name,
                signal_action=None,
                signal_confidence=intent.signal_confidence,
                signal_price=decimal_to_str(arrival),
                risk_decision="allow",
                risk_reason=None,
                requested_side=intent.side.value,
                order_type=intent.order_type.value,
                intended_quantity=decimal_to_str(quantity),
                intended_notional=decimal_to_str(notional),
                arrival_price=decimal_to_str(arrival),
                spread_at_decision=None,
                hypothetical_execution_decision=decision,
                cap_evaluation=_cap_to_shadow(caps),
                latency_ms=latency_ms,
                submitted_price=None,
                fill_price=None,
                slippage_bps=None,
                paper_comparison=None,
                client_order_id_hypothetical=str(uuid.uuid4()),
            )
        )

        return self._reject(
            symbol=Symbol(symbol_text),
            side=intent.side,
            quantity=quantity,
            message=message,
        )

    def record_observation(self, record: ShadowRecord) -> None:
        """Append a pre-built observation (risk/hold paths) without submit."""
        self._audit.append(record)

    @staticmethod
    def _reject(
        *,
        symbol: Symbol,
        side: Side,
        quantity: Decimal,
        message: str,
    ) -> ExecutionResult:
        return ExecutionResult(
            order_id=OrderId(str(uuid.uuid4())),
            symbol=symbol,
            side=side,
            requested_quantity=quantity,
            filled_quantity=Decimal("0"),
            fill_price=Decimal("0"),
            fee=Decimal("0"),
            status=ExecutionStatus.REJECTED,
            message=message,
        )


def _cap_to_shadow(caps: CapEvaluation) -> ShadowCapEvaluation:
    return ShadowCapEvaluation(
        order_notional_ok=caps.order_notional_ok,
        daily_count_ok=caps.daily_count_ok,
        would_consume_slot=False,
        notional=decimal_to_str(caps.notional),
        current_daily_count=caps.current_count,
    )


def build_shadow_observation(
    *,
    command: str,
    broker_adapter_id: str,
    broker_endpoint_class: str,
    symbol: str,
    strategy_name: str | None,
    signal_action: str | None,
    signal_confidence: float | None,
    signal_price: Decimal | None,
    risk_decision: str,
    risk_reason: str | None,
    hypothetical_execution_decision: str,
    requested_side: str | None = None,
    order_type: str | None = None,
    intended_quantity: Decimal | None = None,
    intended_notional: Decimal | None = None,
    arrival_price: Decimal | None = None,
    latency_ms: int | None = None,
    cap_evaluation: ShadowCapEvaluation | None = None,
) -> ShadowRecord:
    """Factory helper for runtime hold/risk shadow rows."""
    return ShadowRecord(
        schema_version=SHADOW_RECORD_SCHEMA_VERSION,
        ts_utc=utc_now_iso(),
        execution_mode="shadow",
        broker_adapter_id=broker_adapter_id,
        broker_endpoint_class=broker_endpoint_class,
        command=command,
        symbol=str(symbol).strip().upper(),
        strategy_name=strategy_name,
        signal_action=signal_action,
        signal_confidence=signal_confidence,
        signal_price=decimal_to_str(signal_price),
        risk_decision=risk_decision,
        risk_reason=risk_reason,
        requested_side=requested_side,
        order_type=order_type,
        intended_quantity=decimal_to_str(intended_quantity),
        intended_notional=decimal_to_str(intended_notional),
        arrival_price=decimal_to_str(arrival_price),
        spread_at_decision=None,
        hypothetical_execution_decision=hypothetical_execution_decision,
        cap_evaluation=cap_evaluation
        or ShadowCapEvaluation(
            order_notional_ok=True,
            daily_count_ok=True,
            would_consume_slot=False,
        ),
        latency_ms=latency_ms,
        submitted_price=None,
        fill_price=None,
        slippage_bps=None,
        paper_comparison=None,
        client_order_id_hypothetical=None,
    )
