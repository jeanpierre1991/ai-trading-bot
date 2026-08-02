"""Broker-agnostic approved adapter registry (Milestone 13.2).

Core live authorization depends only on adapter ids and endpoint classes here.
Venue-specific construction is deferred via lazy imports inside factory helpers.
Future IBKR / TradeStation / Webull adapters register the same way.
"""

from __future__ import annotations

from typing import Any, Callable, Final

from broker_interface.broker import Broker
from core.exceptions import ConfigurationError

ENDPOINT_LOCAL_PAPER: Final = "local_paper"
ENDPOINT_BROKER_SANDBOX: Final = "broker_sandbox"
ENDPOINT_LIVE_PRODUCTION: Final = "live_production"

# M13.2: sandbox-only approvals.
# M14.4: production allowlist remains empty by default. Any future entry requires
# human-approved registration after checklist + wiring latch + external sign-off.
_SANDBOX_ADAPTERS: Final[frozenset[str]] = frozenset({"alpaca_paper"})
_PRODUCTION_ADAPTERS: Final[frozenset[str]] = frozenset()


def normalize_adapter_id(adapter_id: str) -> str:
    return str(adapter_id or "").strip().lower()


def is_approved_adapter(adapter_id: str, endpoint_class: str) -> bool:
    """Return True when adapter_id is approved for the endpoint class."""
    name = normalize_adapter_id(adapter_id)
    klass = str(endpoint_class or "").strip().lower()
    if not name:
        return False
    if klass == ENDPOINT_BROKER_SANDBOX:
        return name in _SANDBOX_ADAPTERS
    if klass == ENDPOINT_LIVE_PRODUCTION:
        # Empty by default — production unreachable without human-gated registration.
        return name in _PRODUCTION_ADAPTERS
    return False


def construct_sandbox_broker(
    settings: Any,
    *,
    transport: Any | None = None,
) -> Broker:
    """Construct an approved BROKER_SANDBOX adapter from settings.

    Raises ConfigurationError for unknown/unapproved names — never falls back
    to PaperBroker.
    """
    adapter_id = normalize_adapter_id(getattr(settings, "broker_name", ""))
    if not is_approved_adapter(adapter_id, ENDPOINT_BROKER_SANDBOX):
        raise ConfigurationError(
            f"no approved BROKER_SANDBOX adapter for broker_name={adapter_id!r}; "
            "refusing silent PaperBroker fallback under execution='live'"
        )

    builder = _SANDBOX_BUILDERS.get(adapter_id)
    if builder is None:
        raise ConfigurationError(
            f"approved adapter {adapter_id!r} has no sandbox builder registered"
        )
    return builder(settings, transport=transport)


def _build_alpaca_paper(settings: Any, *, transport: Any | None = None) -> Broker:
    # Lazy import keeps registry importable without pulling HTTP stack eagerly.
    from broker_interface.alpaca.adapter import alpaca_paper_broker_from_settings

    return alpaca_paper_broker_from_settings(settings, transport=transport)


_SANDBOX_BUILDERS: dict[str, Callable[..., Broker]] = {
    "alpaca_paper": _build_alpaca_paper,
}
