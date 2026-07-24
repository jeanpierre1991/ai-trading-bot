"""Map broker ExecutionResult values into portfolio Fill payloads.

Pure adapter: no I/O, no Portfolio mutation, no broker calls.
"""

from __future__ import annotations

from broker_interface.execution import ExecutionResult, ExecutionStatus
from core.types import Side, Symbol
from portfolio_manager.portfolio import Fill


def is_bookable(execution: ExecutionResult) -> bool:
    """Return True when ``execution`` should produce a portfolio Fill.

    Current bookable status: ``FILLED`` only.
    Future: ``PARTIAL`` with ``filled_quantity > 0`` may become bookable.
    ``REJECTED`` / ``CANCELLED`` (when added) are never bookable here.
    """
    return execution.status is ExecutionStatus.FILLED


def execution_to_fill(execution: ExecutionResult) -> Fill | None:
    """Convert a bookable ``ExecutionResult`` into a ``Fill``.

    Returns:
        ``Fill`` when status is ``FILLED`` and quantities/prices are valid.
        ``None`` when the result must not be booked (e.g. ``REJECTED``).

    Invariant: if ``is_bookable(execution)`` is True, this must return a
    ``Fill`` or raise ``ValueError`` — never ``None``. The Runtime treats
    ``None`` after a bookable check as a contract violation.

    Raises:
        ValueError: if status is ``FILLED`` but the payload breaks booking
            invariants (non-positive filled quantity, negative price/fee,
            empty symbol, or invalid side).
    """
    if not is_bookable(execution):
        return None

    symbol_text = str(execution.symbol).strip() if execution.symbol is not None else ""
    if not symbol_text:
        raise ValueError("FILLED execution requires a non-empty symbol")

    if execution.side not in (Side.BUY, Side.SELL):
        raise ValueError(f"FILLED execution has invalid side: {execution.side!r}")

    if execution.filled_quantity <= 0:
        raise ValueError("FILLED execution requires filled_quantity > 0")

    if execution.fill_price < 0:
        raise ValueError("FILLED execution cannot have a negative fill_price")

    if execution.fee < 0:
        raise ValueError("FILLED execution cannot have a negative fee")

    return Fill(
        symbol=Symbol(symbol_text),
        side=execution.side,
        quantity=execution.filled_quantity,
        price=execution.fill_price,
        fee=execution.fee,
    )
