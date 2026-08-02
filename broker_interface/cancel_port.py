"""Broker-agnostic emergency cancel port (Milestone 14.1).

Adapters implement ``CancelCapableBroker``. Core emergency-stop code must not
import venue-specific types.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class CancelAllResult:
    """Outcome of a best-effort cancel-all open orders request."""

    attempted: bool
    canceled_order_ids: tuple[str, ...] = ()
    failed_order_ids: tuple[str, ...] = ()
    partial: bool = False
    error: str | None = None
    message: str = ""

    @property
    def success(self) -> bool:
        return self.attempted and not self.partial and self.error is None


@runtime_checkable
class CancelCapableBroker(Protocol):
    """Minimum cancel surface for emergency stop."""

    def cancel_all_open_orders(self) -> CancelAllResult:
        """Best-effort cancel of open orders. Must not raise for partial failure."""
