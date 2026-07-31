"""Execution mode policy (paper / dry-run + gated live sandbox + shadow).

Pure checks: no I/O, no portfolio mutation, no broker calls.
M13.2: LIVE paths re-evaluate ``LiveEnablementAuthority`` independently (D-A).
M13.4: SHADOW paths use ``evaluate_shadow_enablement`` (G6b) and require
``ShadowExecutor`` only.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from broker_interface.broker import PaperBroker
from config.settings import Settings
from core.types import TradingMode
from runtime.broker_executor import BrokerOrderExecutor
from runtime.executor import OrderExecutor
from runtime.idempotent_submit import IdempotentLiveExecutor
from runtime.live_caps import LiveCapGuardBroker
from runtime.live_enablement import (
    LiveExecutionContext,
    evaluate_live_enablement,
    evaluate_shadow_enablement,
)
from runtime.shadow_executor import ShadowExecutor

_ALLOWED_PAPER_SETTINGS_MODES = frozenset({"paper"})
_ALLOWED_PAPER_CONTEXT_MODES = frozenset({TradingMode.PAPER})


def mode_policy_violation(
    *,
    context_mode: object,
    executor: OrderExecutor | None,
    settings: Settings | None = None,
    settings_trading_mode: str | None = None,
    execution: str = "dry_run",
    command: str = "run-once",
) -> str | None:
    """Return an abort reason when mode/executor wiring is disallowed."""
    resolved = _resolve_settings(settings, settings_trading_mode)

    if execution == "live":
        return _live_mode_policy_violation(
            settings=resolved,
            context_mode=context_mode,
            executor=executor,
            execution=execution,
            command=command,
        )

    if execution == "shadow":
        return _shadow_mode_policy_violation(
            settings=resolved,
            context_mode=context_mode,
            executor=executor,
            execution=execution,
            command=command,
        )

    return _paper_mode_policy_violation(
        settings=resolved,
        context_mode=context_mode,
        executor=executor,
    )


def _resolve_settings(
    settings: Settings | None,
    settings_trading_mode: str | None,
) -> Any:
    if settings is not None:
        return settings
    if settings_trading_mode is None:
        raise TypeError(
            "mode_policy_violation requires settings= or settings_trading_mode="
        )
    if settings_trading_mode in {"paper", "live", "backtest"}:
        return Settings(trading_mode=settings_trading_mode)  # type: ignore[arg-type]
    # Preserve unknown-mode rejection in unit tests without pydantic validation.
    return SimpleNamespace(trading_mode=settings_trading_mode)


def _live_mode_policy_violation(
    *,
    settings: Settings,
    context_mode: object,
    executor: OrderExecutor | None,
    execution: str,
    command: str,
) -> str | None:
    auth = evaluate_live_enablement(
        settings,
        LiveExecutionContext(
            command=command,
            execution=execution,
            context_mode=context_mode,
        ),
    )
    if not auth.authorized:
        return auth.reason or "live enablement denied"

    if isinstance(executor, ShadowExecutor):
        return "execution='live' cannot use ShadowExecutor; use execution='shadow'"

    if not isinstance(executor, BrokerOrderExecutor):
        return (
            "execution='live' requires BrokerOrderExecutor; "
            f"got {type(executor).__name__ if executor is not None else 'None'}"
        )

    broker: Any = executor.broker
    if isinstance(broker, LiveCapGuardBroker):
        broker = broker.inner
    if isinstance(broker, PaperBroker):
        return (
            "execution='live' cannot use PaperBroker; "
            "approved BROKER_SANDBOX adapter required"
        )
    return None


def _shadow_mode_policy_violation(
    *,
    settings: Settings,
    context_mode: object,
    executor: OrderExecutor | None,
    execution: str,
    command: str,
) -> str | None:
    auth = evaluate_shadow_enablement(
        settings,
        LiveExecutionContext(
            command=command,
            execution=execution,
            context_mode=context_mode,
        ),
    )
    if not auth.authorized:
        return auth.reason or "shadow enablement denied"

    if isinstance(executor, IdempotentLiveExecutor):
        return (
            "execution='shadow' forbids IdempotentLiveExecutor "
            "(no-submit shadow must never place orders)"
        )
    if isinstance(executor, BrokerOrderExecutor):
        return (
            "execution='shadow' forbids BrokerOrderExecutor "
            "(no-submit shadow must never place orders)"
        )
    if not isinstance(executor, ShadowExecutor):
        return (
            "execution='shadow' requires ShadowExecutor; "
            f"got {type(executor).__name__ if executor is not None else 'None'}"
        )
    return None


def _paper_mode_policy_violation(
    *,
    settings: Settings,
    context_mode: object,
    executor: OrderExecutor | None,
) -> str | None:
    mode = settings.trading_mode

    if mode == "live":
        return (
            "trading_mode='live' requires execution='live' or execution='shadow' "
            "with all live/shadow gates; paper/dry-run paths remain paper-only"
        )
    if mode not in _ALLOWED_PAPER_SETTINGS_MODES:
        return (
            f"trading_mode={mode!r} is not allowed; "
            "paper/dry-run paths permit trading_mode='paper' only"
        )

    if not isinstance(context_mode, TradingMode):
        return (
            f"invalid RuntimeContext.mode={context_mode!r}; "
            "expected a TradingMode"
        )
    if context_mode is TradingMode.LIVE:
        return (
            "RuntimeContext.mode=live is not allowed on paper/dry-run execution; "
            "use execution='live' or execution='shadow' with gates"
        )
    if context_mode not in _ALLOWED_PAPER_CONTEXT_MODES:
        return (
            f"RuntimeContext.mode={context_mode.value!r} is not allowed; "
            "paper/dry-run paths permit TradingMode.PAPER only"
        )

    if isinstance(executor, ShadowExecutor):
        return (
            "ShadowExecutor is not allowed on paper/dry-run execution; "
            "use execution='shadow'"
        )

    if isinstance(executor, BrokerOrderExecutor):
        broker: Any = executor.broker
        if isinstance(broker, LiveCapGuardBroker):
            broker = broker.inner
        if not isinstance(broker, PaperBroker):
            return (
                f"executor broker {type(broker).__name__} is not allowed; "
                "only PaperBroker is permitted with BrokerOrderExecutor "
                "on paper/dry-run execution (live brokers are blocked)"
            )

    return None
