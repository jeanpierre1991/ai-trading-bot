"""Dry-run executor wrapper that applies backtest commission fees.

Does not modify ``DryRunExecutor``, ``PaperBroker``, or global booking code.
Commission is attached once on each simulated FILLED result via ``ExecutionResult.fee``,
which then flows through the existing ``execution_to_fill`` → ``apply_fill`` path.
"""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

from broker_interface.execution import ExecutionResult, ExecutionStatus
from core.exceptions import ConfigurationError
from runtime.dry_run import DryRunExecutor
from runtime.executor import OrderExecutor
from runtime.models import TradeIntent

_MONEY = Decimal("0.01")


class CommissionDryRunExecutor(OrderExecutor):
    """Delegates to ``DryRunExecutor`` and sets fee = notional * commission_pct."""

    def __init__(
        self,
        commission_pct: Decimal,
        *,
        inner: DryRunExecutor | None = None,
    ) -> None:
        if commission_pct < 0:
            raise ConfigurationError(
                f"commission_pct must be >= 0; got {commission_pct}"
            )
        self._commission_pct = commission_pct
        self._inner = inner if inner is not None else DryRunExecutor()
        if not isinstance(self._inner, DryRunExecutor):
            raise ConfigurationError(
                "CommissionDryRunExecutor requires an inner DryRunExecutor "
                "(live/broker executors are not allowed)"
            )
        self._commissions_paid = Decimal("0")

    @property
    def commission_pct(self) -> Decimal:
        return self._commission_pct

    @property
    def inner(self) -> DryRunExecutor:
        return self._inner

    @property
    def commissions_paid(self) -> Decimal:
        return self._commissions_paid

    def reset_commission_total(self) -> None:
        self._commissions_paid = Decimal("0")

    def execute(self, intent: TradeIntent | None) -> ExecutionResult:
        result = self._inner.execute(intent)
        if result.status is not ExecutionStatus.FILLED:
            return result

        notional = (result.filled_quantity * result.fill_price).quantize(_MONEY)
        fee = (notional * self._commission_pct).quantize(_MONEY)
        self._commissions_paid = (self._commissions_paid + fee).quantize(_MONEY)
        return replace(result, fee=fee)
