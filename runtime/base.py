"""Trading runtime abstractions.

Coordinates a single decision cycle without owning broker execution.
Concrete runtimes may target paper, live, or backtest modes later.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from runtime.context import RuntimeContext
from runtime.models import PipelineResult


class TradingRuntime(ABC):
    """Contract for orchestrating one trading cycle.

    Intended stage order (execution/broker deferred):
    1. Market data
    2. Strategy evaluation
    3. Risk gate
    4. Trade intent
    5. Portfolio snapshot
    """

    @abstractmethod
    def run_once(self, context: RuntimeContext) -> PipelineResult:
        """Run a single coordinated cycle and return a pipeline result."""
