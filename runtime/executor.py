"""Order execution abstractions for the trading runtime.

Brokers remain transport adapters. Executors consume TradeIntent and produce
ExecutionResult without owning portfolio mutation.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from broker_interface.execution import ExecutionResult
from runtime.models import TradeIntent


class OrderExecutor(ABC):
    """Contract for turning a TradeIntent into an ExecutionResult."""

    @abstractmethod
    def execute(self, intent: TradeIntent | None) -> ExecutionResult:
        """Execute or simulate ``intent`` and return an execution result."""
