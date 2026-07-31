"""Append-only JSONL shadow audit artifacts (Milestone 13.4).

Broker-agnostic schema. Never persists credentials or confirm tokens.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from core.exceptions import ConfigurationError

SHADOW_RECORD_SCHEMA_VERSION = 1

_SECRET_KEY_FRAGMENTS = (
    "api_key",
    "api_secret",
    "secret",
    "password",
    "token",
    "authorization",
    "credential",
)


@dataclass(frozen=True)
class ShadowCapEvaluation:
    order_notional_ok: bool
    daily_count_ok: bool
    would_consume_slot: bool = False
    notional: str | None = None
    current_daily_count: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "order_notional_ok": self.order_notional_ok,
            "daily_count_ok": self.daily_count_ok,
            "would_consume_slot": self.would_consume_slot,
            "notional": self.notional,
            "current_daily_count": self.current_daily_count,
        }


@dataclass(frozen=True)
class ShadowRecord:
    """One supervised shadow cycle observation (schema_version=1)."""

    schema_version: int
    ts_utc: str
    execution_mode: str
    broker_adapter_id: str
    broker_endpoint_class: str
    command: str
    symbol: str
    strategy_name: str | None
    signal_action: str | None
    signal_confidence: float | None
    signal_price: str | None
    risk_decision: str
    risk_reason: str | None
    requested_side: str | None
    order_type: str | None
    intended_quantity: str | None
    intended_notional: str | None
    arrival_price: str | None
    spread_at_decision: str | None
    hypothetical_execution_decision: str
    cap_evaluation: ShadowCapEvaluation
    latency_ms: int | None
    submitted_price: str | None = None
    fill_price: str | None = None
    slippage_bps: str | None = None
    paper_comparison: dict[str, Any] | None = None
    client_order_id_hypothetical: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def to_json_dict(self) -> dict[str, Any]:
        payload = {
            "schema_version": self.schema_version,
            "ts_utc": self.ts_utc,
            "execution_mode": self.execution_mode,
            "broker_adapter_id": self.broker_adapter_id,
            "broker_endpoint_class": self.broker_endpoint_class,
            "command": self.command,
            "symbol": self.symbol,
            "strategy_name": self.strategy_name,
            "signal_action": self.signal_action,
            "signal_confidence": self.signal_confidence,
            "signal_price": self.signal_price,
            "risk_decision": self.risk_decision,
            "risk_reason": self.risk_reason,
            "requested_side": self.requested_side,
            "order_type": self.order_type,
            "intended_quantity": self.intended_quantity,
            "intended_notional": self.intended_notional,
            "arrival_price": self.arrival_price,
            "spread_at_decision": self.spread_at_decision,
            "hypothetical_execution_decision": self.hypothetical_execution_decision,
            "cap_evaluation": self.cap_evaluation.to_dict(),
            "latency_ms": self.latency_ms,
            "submitted_price": self.submitted_price,
            "fill_price": self.fill_price,
            "slippage_bps": self.slippage_bps,
            "paper_comparison": self.paper_comparison,
            "client_order_id_hypothetical": self.client_order_id_hypothetical,
        }
        if self.extra:
            payload["extra"] = dict(self.extra)
        _assert_no_secrets(payload)
        return payload


def require_shadow_audit_path(path: object) -> Path:
    text = str(path or "").strip()
    if not text:
        raise ConfigurationError(
            "execution='shadow' requires SHADOW_AUDIT_PATH "
            "(append-only JSONL shadow audit artifact)"
        )
    resolved = Path(text)
    parent = resolved.parent
    if resolved.exists() and resolved.is_dir():
        raise ConfigurationError(
            f"SHADOW_AUDIT_PATH must be a file path, not a directory: {resolved}"
        )
    try:
        parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ConfigurationError(
            f"SHADOW_AUDIT_PATH parent directory is not writable: {parent}: {exc}"
        ) from exc
    if not os.access(parent, os.W_OK):
        raise ConfigurationError(
            f"SHADOW_AUDIT_PATH parent directory is not writable: {parent}"
        )
    return resolved


def decimal_to_str(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return format(Decimal(str(value)), "f")


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class ShadowAuditLog:
    """Append-only JSONL writer for shadow records."""

    def __init__(self, path: Path | str) -> None:
        self._path = require_shadow_audit_path(path)

    @property
    def path(self) -> Path:
        return self._path

    def append(self, record: ShadowRecord) -> None:
        payload = record.to_json_dict()
        line = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        try:
            with self._path.open("a", encoding="utf-8") as handle:
                handle.write(line)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
        except OSError as exc:
            raise ConfigurationError(
                f"failed to append shadow audit record to {self._path}: {exc}"
            ) from exc


def _assert_no_secrets(payload: dict[str, Any]) -> None:
    stack: list[Any] = [payload]
    while stack:
        current = stack.pop()
        if isinstance(current, dict):
            for key, value in current.items():
                key_l = str(key).lower()
                if any(frag in key_l for frag in _SECRET_KEY_FRAGMENTS):
                    raise ConfigurationError(
                        f"shadow audit refused to persist secret-like field: {key}"
                    )
                stack.append(value)
        elif isinstance(current, list):
            stack.extend(current)


def shadow_record_from_dict(raw: dict[str, Any]) -> ShadowRecord:
    """Parse a JSONL row for tests/audit (fail closed on schema mismatch)."""
    if not isinstance(raw, dict):
        raise ConfigurationError("shadow record must be an object")
    if raw.get("schema_version") != SHADOW_RECORD_SCHEMA_VERSION:
        raise ConfigurationError(
            f"unsupported shadow schema_version={raw.get('schema_version')!r}"
        )
    cap_raw = raw.get("cap_evaluation") or {}
    if not isinstance(cap_raw, dict):
        raise ConfigurationError("cap_evaluation must be an object")
    return ShadowRecord(
        schema_version=SHADOW_RECORD_SCHEMA_VERSION,
        ts_utc=str(raw.get("ts_utc") or ""),
        execution_mode=str(raw.get("execution_mode") or ""),
        broker_adapter_id=str(raw.get("broker_adapter_id") or ""),
        broker_endpoint_class=str(raw.get("broker_endpoint_class") or ""),
        command=str(raw.get("command") or ""),
        symbol=str(raw.get("symbol") or ""),
        strategy_name=_opt_str(raw.get("strategy_name")),
        signal_action=_opt_str(raw.get("signal_action")),
        signal_confidence=(
            float(raw["signal_confidence"])
            if raw.get("signal_confidence") is not None
            else None
        ),
        signal_price=_opt_str(raw.get("signal_price")),
        risk_decision=str(raw.get("risk_decision") or ""),
        risk_reason=_opt_str(raw.get("risk_reason")),
        requested_side=_opt_str(raw.get("requested_side")),
        order_type=_opt_str(raw.get("order_type")),
        intended_quantity=_opt_str(raw.get("intended_quantity")),
        intended_notional=_opt_str(raw.get("intended_notional")),
        arrival_price=_opt_str(raw.get("arrival_price")),
        spread_at_decision=_opt_str(raw.get("spread_at_decision")),
        hypothetical_execution_decision=str(
            raw.get("hypothetical_execution_decision") or ""
        ),
        cap_evaluation=ShadowCapEvaluation(
            order_notional_ok=bool(cap_raw.get("order_notional_ok", False)),
            daily_count_ok=bool(cap_raw.get("daily_count_ok", False)),
            would_consume_slot=bool(cap_raw.get("would_consume_slot", False)),
            notional=_opt_str(cap_raw.get("notional")),
            current_daily_count=(
                int(cap_raw["current_daily_count"])
                if cap_raw.get("current_daily_count") is not None
                else None
            ),
        ),
        latency_ms=(
            int(raw["latency_ms"]) if raw.get("latency_ms") is not None else None
        ),
        submitted_price=_opt_str(raw.get("submitted_price")),
        fill_price=_opt_str(raw.get("fill_price")),
        slippage_bps=_opt_str(raw.get("slippage_bps")),
        paper_comparison=(
            dict(raw["paper_comparison"])
            if isinstance(raw.get("paper_comparison"), dict)
            else None
        ),
        client_order_id_hypothetical=_opt_str(raw.get("client_order_id_hypothetical")),
        extra=dict(raw["extra"]) if isinstance(raw.get("extra"), dict) else {},
    )


def _opt_str(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
