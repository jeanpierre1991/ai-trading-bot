"""Execution mode policy for Milestone 8 (paper / dry-run only).

Pure checks: no I/O, no portfolio mutation, no broker calls.
"""

from __future__ import annotations

from broker_interface.broker import PaperBroker
from core.types import TradingMode
from runtime.broker_executor import BrokerOrderExecutor
from runtime.executor import OrderExecutor

_ALLOWED_SETTINGS_MODES = frozenset({"paper"})
_ALLOWED_CONTEXT_MODES = frozenset({TradingMode.PAPER})


def mode_policy_violation(
    *,
    settings_trading_mode: str,
    context_mode: object,
    executor: OrderExecutor | None,
) -> str | None:
    """Return an abort reason when mode/executor wiring is disallowed.

    Allowed for M8:
    - ``settings.trading_mode == "paper"``
    - ``RuntimeContext.mode == TradingMode.PAPER``
    - executor ``None``, ``DryRunExecutor``, ``BrokerOrderExecutor(PaperBroker)``,
      or other non-broker test/stub executors

    Disallowed:
    - ``live`` / ``backtest`` / unknown settings modes
    - ``RuntimeContext.mode`` live, backtest, or non-``TradingMode``
    - ``BrokerOrderExecutor`` wired to a non-``PaperBroker`` (live-risk)
    """
    if settings_trading_mode == "live":
        return (
            "trading_mode='live' is not allowed; "
            "Milestone 8 permits paper and dry-run only"
        )
    if settings_trading_mode not in _ALLOWED_SETTINGS_MODES:
        return (
            f"trading_mode={settings_trading_mode!r} is not allowed; "
            "Milestone 8 permits paper and dry-run only"
        )

    if not isinstance(context_mode, TradingMode):
        return (
            f"invalid RuntimeContext.mode={context_mode!r}; "
            "expected a TradingMode"
        )
    if context_mode is TradingMode.LIVE:
        return (
            "RuntimeContext.mode=live is not allowed; "
            "Milestone 8 permits paper and dry-run only"
        )
    if context_mode not in _ALLOWED_CONTEXT_MODES:
        return (
            f"RuntimeContext.mode={context_mode.value!r} is not allowed; "
            "Milestone 8 permits paper and dry-run only"
        )

    if isinstance(executor, BrokerOrderExecutor) and not isinstance(
        executor.broker,
        PaperBroker,
    ):
        return (
            f"executor broker {type(executor.broker).__name__} is not allowed; "
            "only PaperBroker is permitted with BrokerOrderExecutor "
            "(live brokers are blocked)"
        )

    return None
