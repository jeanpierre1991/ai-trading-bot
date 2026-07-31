"""Pure helpers for shadow notional / slippage math (Milestone 13.4).

Broker-agnostic. No I/O. No broker imports.
"""

from __future__ import annotations

from decimal import Decimal


def intended_notional(*, quantity: Decimal, price: Decimal) -> Decimal:
    return Decimal(str(quantity)) * Decimal(str(price))


def slippage_bps(
    *,
    reference_price: Decimal,
    fill_price: Decimal,
) -> Decimal | None:
    """Return slippage in basis points, or None if not measurable."""
    ref = Decimal(str(reference_price))
    fill = Decimal(str(fill_price))
    if ref <= 0:
        return None
    return ((fill - ref) / ref) * Decimal("10000")
