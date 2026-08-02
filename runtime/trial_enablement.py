"""M14 trial enablement authority for LIVE_PRODUCTION (Milestone 14.3/14.4).

Conjunctive, fail-closed gates layered on M13 live enablement. Incomplete
evidence checklists always deny. LIVE_PRODUCTION stays default-denied: wiring
requires an explicit settings latch (default false) plus a non-empty production
adapter allowlist (empty by default). Human process approval remains mandatory
before any real-money trial.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Any, Callable

from broker_interface.adapter_registry import (
    ENDPOINT_LIVE_PRODUCTION,
    is_approved_adapter,
)
from core.types import TradingMode
from runtime.evidence_checklist import validate_evidence_checklist
from runtime.live_enablement import LiveAuthorization, LiveExecutionContext

# Distinct from M13 sandbox confirm phrase (design §3.3).
EXPECTED_LIVE_TRIAL_CONFIRM_TOKEN = "I_UNDERSTAND_THIS_IS_A_CONTROLLED_LIVE_TRIAL"


def evaluate_trial_enablement(
    settings: Any,
    context: LiveExecutionContext,
    *,
    checklist_validator: Callable[..., Any] | None = None,
) -> LiveAuthorization:
    """Authorize LIVE_PRODUCTION only when all M14 trial gates pass.

    Defaults deny. ``authorized=True`` requires complete checklist, trial token,
    trial limits, supervised context, an approved LIVE_PRODUCTION adapter, and
    ``live_production_trial_wiring_enabled=true``. None of those are enabled by
    default in-tree.
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

    # T0 — production endpoint class only
    if endpoint_class != ENDPOINT_LIVE_PRODUCTION:
        return deny(
            "T0: trial enablement applies only to broker_endpoint_class="
            f"'live_production'; got {endpoint_class!r}"
        )

    # T1 — trading_mode live
    trading_mode = str(getattr(settings, "trading_mode", "") or "").strip().lower()
    if trading_mode != "live":
        return deny(
            f"T1: trading_mode must be 'live' for production trial; got {trading_mode!r}"
        )

    # T2 — explicit LIVE_TRADING_ENABLED
    if getattr(settings, "live_trading_enabled", False) is not True:
        return deny("T2: LIVE_TRADING_ENABLED must be true for production trial")

    # T3 — evidence checklist (fail closed; incomplete always denies)
    validator = checklist_validator or validate_evidence_checklist
    checklist_path = getattr(settings, "trial_evidence_checklist_path", None)
    checklist = validator(checklist_path)
    if not getattr(checklist, "ok", False):
        return deny(
            getattr(checklist, "reason", None)
            or "T3: evidence checklist validation failed"
        )

    # T4 — dedicated trial confirm token (exact match; empty denies)
    provided = str(getattr(settings, "live_trial_confirm_token", "") or "")
    if not provided:
        return deny("T4: LIVE_TRIAL_CONFIRM_TOKEN is empty (production trial denied)")
    if provided != EXPECTED_LIVE_TRIAL_CONFIRM_TOKEN:
        return deny(
            "T4: LIVE_TRIAL_CONFIRM_TOKEN does not match the expected trial phrase"
        )

    # T5 — mandatory trial limits (stricter envelope must be configured)
    trial_notional = getattr(settings, "trial_max_order_notional", None)
    if trial_notional is None:
        return deny("T5: TRIAL_MAX_ORDER_NOTIONAL must be configured for production trial")
    try:
        notional = Decimal(str(trial_notional))
    except Exception:
        return deny("T5: TRIAL_MAX_ORDER_NOTIONAL is invalid")
    if notional <= 0:
        return deny("T5: TRIAL_MAX_ORDER_NOTIONAL must be > 0")

    trial_orders = getattr(settings, "trial_max_orders_per_day", None)
    if trial_orders is None:
        return deny("T5: TRIAL_MAX_ORDERS_PER_DAY must be configured for production trial")
    try:
        orders = int(trial_orders)
    except Exception:
        return deny("T5: TRIAL_MAX_ORDERS_PER_DAY is invalid")
    if isinstance(trial_orders, bool) or orders <= 0:
        return deny("T5: TRIAL_MAX_ORDERS_PER_DAY must be > 0")

    # T6 — credentials present (never echo values)
    api_key = str(getattr(settings, "broker_api_key", "") or "").strip()
    api_secret = str(getattr(settings, "broker_api_secret", "") or "").strip()
    base_url = str(getattr(settings, "broker_base_url", "") or "").strip()
    if not api_key:
        return deny("T6: BROKER_API_KEY must be non-empty for production trial")
    if not api_secret:
        return deny("T6: BROKER_API_SECRET must be non-empty for production trial")
    if not base_url:
        return deny("T6: BROKER_BASE_URL must be non-empty for production trial")

    # T7 — supervised command / context (no operator/backtest bypass)
    if context.command in {"run-backtest", "run-paper-operator"}:
        return deny(
            f"T7: command {context.command!r} cannot authorize production trial"
        )
    if context.command not in {"run-once", "run-session"}:
        return deny(
            "T7: command must be run-once or run-session for production trial; "
            f"got {context.command!r}"
        )
    if context.execution != "live":
        return deny(
            f"T7: factory execution must be 'live' for production trial; "
            f"got {context.execution!r}"
        )
    if not isinstance(context.context_mode, TradingMode):
        return deny(
            f"T7: invalid RuntimeContext.mode={context.context_mode!r}; "
            "expected TradingMode.LIVE"
        )
    if context.context_mode is not TradingMode.LIVE:
        return deny(
            "T7: RuntimeContext.mode must be LIVE for production trial; "
            f"got {context.context_mode.value!r}"
        )

    # T8 — production adapter allowlist (empty by default; human-gated registration)
    if not adapter_id:
        return deny("T8: BROKER_NAME is empty; no production trial adapter selected")
    if not is_approved_adapter(adapter_id, ENDPOINT_LIVE_PRODUCTION):
        return deny(
            f"T8: broker_name={adapter_id!r} is not an approved LIVE_PRODUCTION "
            "adapter (production allowlist empty by default; human registration required)"
        )

    # T9 — explicit wiring latch (default false; process approval outside code)
    wiring = getattr(settings, "live_production_trial_wiring_enabled", False) is True
    if not wiring:
        return deny(
            "T9: LIVE_PRODUCTION trial wiring disabled by default "
            "(LIVE_PRODUCTION_TRIAL_WIRING_ENABLED must be true after human approval)"
        )

    return LiveAuthorization(
        authorized=True,
        reason=None,
        endpoint_class=ENDPOINT_LIVE_PRODUCTION,
        adapter_id=adapter_id.lower(),
    )


def require_trial_checklist_path(path: Path | None) -> Path:
    """Fail-closed path helper for composition roots."""
    if path is None or not str(path).strip():
        raise ValueError(
            "production trial requires TRIAL_EVIDENCE_CHECKLIST_PATH "
            "(fail closed)"
        )
    return Path(path)
