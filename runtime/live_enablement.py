"""Broker-agnostic hard LIVE enablement gates (Milestone 13.2).

Pure checks: no I/O, no broker calls, no venue-specific imports.
Conjunctive fail-closed authorization for supervised BROKER_SANDBOX wiring only.
LIVE_PRODUCTION remains hard-denied in M13.2 (real-money trial is M14).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Literal

from broker_interface.adapter_registry import (
    ENDPOINT_BROKER_SANDBOX,
    ENDPOINT_LIVE_PRODUCTION,
    ENDPOINT_LOCAL_PAPER,
    is_approved_adapter,
)
from core.types import TradingMode

# D-B (a): fixed expected phrase; operator must set LIVE_CONFIRM_TOKEN explicitly.
# Default empty token always denies LIVE. Never log this value as a "provided" secret.
EXPECTED_LIVE_CONFIRM_TOKEN = "I_UNDERSTAND_LIVE_IS_GATED_NOT_A_TRIAL"

LiveCommand = Literal[
    "run-once",
    "run-session",
    "run-backtest",
    "run-paper-operator",
    "unknown",
]


@dataclass(frozen=True)
class LiveExecutionContext:
    """Call-site axes required for G6–G8 (and G9 class from settings)."""

    command: str
    execution: str
    context_mode: object


@dataclass(frozen=True)
class LiveAuthorization:
    """Result of conjunctive G1–G10 evaluation."""

    authorized: bool
    reason: str | None
    endpoint_class: str
    adapter_id: str


def evaluate_live_enablement(
    settings: Any,
    context: LiveExecutionContext,
) -> LiveAuthorization:
    """Return Authorized only when G1∧…∧G10 all pass for BROKER_SANDBOX.

    Deny reasons never include API secrets or the confirm token value.
    """
    adapter_id = str(getattr(settings, "broker_name", "") or "").strip()
    endpoint_class = str(
        getattr(settings, "broker_endpoint_class", "") or ""
    ).strip().lower()

    def deny(reason: str) -> LiveAuthorization:
        return LiveAuthorization(
            authorized=False,
            reason=reason,
            endpoint_class=endpoint_class or "unknown",
            adapter_id=adapter_id or "unknown",
        )

    # G1 — trading_mode == live
    trading_mode = str(getattr(settings, "trading_mode", "") or "").strip().lower()
    if trading_mode != "live":
        return deny(
            "G1: trading_mode must be 'live' for live enablement; "
            f"got {trading_mode!r}"
        )

    # G2 — explicit LIVE_TRADING_ENABLED
    if getattr(settings, "live_trading_enabled", False) is not True:
        return deny("G2: LIVE_TRADING_ENABLED must be true")

    # G3 — exact confirmation token match (empty default denies)
    provided = str(getattr(settings, "live_confirm_token", "") or "")
    if not provided:
        return deny("G3: LIVE_CONFIRM_TOKEN is empty (LIVE denied by default)")
    if provided != EXPECTED_LIVE_CONFIRM_TOKEN:
        return deny("G3: LIVE_CONFIRM_TOKEN does not match the expected confirmation phrase")

    # G9 — endpoint classification (before adapter detail; production always denied)
    if endpoint_class == ENDPOINT_LIVE_PRODUCTION:
        return deny(
            "G9: LIVE_PRODUCTION endpoint class is hard-denied in M13.2; "
            "real-money trial requires M14"
        )
    if endpoint_class != ENDPOINT_BROKER_SANDBOX:
        return deny(
            "G9: broker_endpoint_class must be 'broker_sandbox' for M13.2 live "
            f"enablement; got {endpoint_class!r}"
        )

    # G4 — approved adapter for sandbox class
    if not adapter_id:
        return deny("G4: BROKER_NAME is empty; no approved live adapter selected")
    if not is_approved_adapter(adapter_id, endpoint_class):
        return deny(
            f"G4: broker_name={adapter_id!r} is not an approved adapter for "
            f"endpoint class {endpoint_class!r}"
        )

    # G5 — credentials / base URL present (values never echoed)
    api_key = str(getattr(settings, "broker_api_key", "") or "").strip()
    api_secret = str(getattr(settings, "broker_api_secret", "") or "").strip()
    base_url = str(getattr(settings, "broker_base_url", "") or "").strip()
    if not api_key:
        return deny("G5: BROKER_API_KEY must be non-empty")
    if not api_secret:
        return deny("G5: BROKER_API_SECRET must be non-empty")
    if not base_url:
        return deny("G5: BROKER_BASE_URL must be non-empty")

    # G7 — backtest prohibited
    if trading_mode == "backtest":
        return deny("G7: backtest trading_mode cannot enable LIVE")
    if context.command == "run-backtest":
        return deny("G7: run-backtest cannot enable LIVE")
    if isinstance(context.context_mode, TradingMode) and (
        context.context_mode is TradingMode.BACKTEST
    ):
        return deny("G7: RuntimeContext.mode=backtest cannot enable LIVE")

    # G8 — paper operator prohibited
    if context.command == "run-paper-operator":
        return deny("G8: run-paper-operator cannot enable LIVE")

    # G6 — execution context explicitly allows LIVE
    if context.execution != "live":
        return deny(
            f"G6: factory execution must be 'live'; got {context.execution!r}"
        )
    if context.command not in {"run-once", "run-session"}:
        return deny(
            f"G6: command must be run-once or run-session for LIVE; "
            f"got {context.command!r}"
        )
    if not isinstance(context.context_mode, TradingMode):
        return deny(
            f"G6: invalid RuntimeContext.mode={context.context_mode!r}; "
            "expected TradingMode.LIVE"
        )
    if context.context_mode is not TradingMode.LIVE:
        return deny(
            "G6: RuntimeContext.mode must be LIVE when execution='live'; "
            f"got {context.context_mode.value!r}"
        )

    # G10 — mandatory absolute caps > 0
    max_notional = getattr(settings, "live_max_order_notional", None)
    max_orders = getattr(settings, "live_max_orders_per_day", None)
    if max_notional is None:
        return deny("G10: LIVE_MAX_ORDER_NOTIONAL must be configured")
    try:
        notional = Decimal(str(max_notional))
    except Exception:
        return deny("G10: LIVE_MAX_ORDER_NOTIONAL is invalid")
    if notional <= 0:
        return deny("G10: LIVE_MAX_ORDER_NOTIONAL must be > 0")

    if max_orders is None:
        return deny("G10: LIVE_MAX_ORDERS_PER_DAY must be configured")
    try:
        orders = int(max_orders)
    except Exception:
        return deny("G10: LIVE_MAX_ORDERS_PER_DAY is invalid")
    if isinstance(max_orders, bool) or orders <= 0:
        return deny("G10: LIVE_MAX_ORDERS_PER_DAY must be > 0")

    # Optional gross notional: if set, must be > 0 (D-E: optional)
    gross = getattr(settings, "live_max_gross_notional", None)
    if gross is not None:
        try:
            gross_dec = Decimal(str(gross))
        except Exception:
            return deny("G10: LIVE_MAX_GROSS_NOTIONAL is invalid")
        if gross_dec <= 0:
            return deny("G10: LIVE_MAX_GROSS_NOTIONAL must be > 0 when set")

    return LiveAuthorization(
        authorized=True,
        reason=None,
        endpoint_class=ENDPOINT_BROKER_SANDBOX,
        adapter_id=adapter_id.lower(),
    )


# Re-export endpoint class names for callers that must not import registry details.
ENDPOINT_CLASSES = (
    ENDPOINT_LOCAL_PAPER,
    ENDPOINT_BROKER_SANDBOX,
    ENDPOINT_LIVE_PRODUCTION,
)
