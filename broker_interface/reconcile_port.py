"""Broker-agnostic reconcile observation port (Milestone 13.3).

Adapters (Alpaca today; IBKR/TradeStation/Webull later) implement this Protocol.
Core reconcile/idempotency code must not import venue-specific types.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from broker_interface.snapshots import BrokerOrderSnapshot, BrokerPositionSnapshot


@runtime_checkable
class ReconcileCapableBroker(Protocol):
    """Minimum observation surface for abort-only reconciliation."""

    def get_order_by_client_id(self, client_order_id: str) -> BrokerOrderSnapshot | None:
        """Return the order for a client id, or None if the venue has no such order."""

    def get_order_snapshot(self, broker_order_id: str) -> BrokerOrderSnapshot | None:
        """Return the order for a venue id, or None if unknown."""

    def list_open_orders(self) -> list[BrokerOrderSnapshot]:
        """Return currently open (including partial) orders at the venue."""

    def list_positions(self) -> list[BrokerPositionSnapshot]:
        """Return non-zero positions at the venue."""
