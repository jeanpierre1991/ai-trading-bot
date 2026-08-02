"""Fail-closed evidence checklist loader/validator (Milestone 14.3).

Deterministic validation: missing path, invalid JSON, schema mismatch, missing
required items, incomplete items, missing evidence files, hash mismatch, or
incomplete human sign-off always deny. No bypass flags.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

CHECKLIST_SCHEMA_VERSION = 1

# Stable, sorted required evidence item ids (design §3.6).
REQUIRED_EVIDENCE_ITEM_IDS: tuple[str, ...] = tuple(
    sorted(
        (
            "m10_strategy_validation_pack",
            "m12_paper_soak",
            "m13_sandbox_live_gated",
            "m13_shadow_validation",
            "m14_kill_cancel_dry_run",
        )
    )
)

_FORBIDDEN_SECRET_KEYS = frozenset(
    {
        "api_key",
        "api_secret",
        "broker_api_key",
        "broker_api_secret",
        "password",
        "token",
        "secret",
    }
)


@dataclass(frozen=True)
class ChecklistValidationResult:
    """Outcome of deterministic checklist validation."""

    ok: bool
    reason: str | None
    missing_items: tuple[str, ...] = ()
    checklist_id: str | None = None
    schema_version: int | None = None


def validate_evidence_checklist(
    path: Path | str | None,
    *,
    base_dir: Path | None = None,
    read_text: Callable[[Path], str] | None = None,
    path_exists: Callable[[Path], bool] | None = None,
    sha256_hex: Callable[[Path], str] | None = None,
) -> ChecklistValidationResult:
    """Load and validate a checklist JSON document.

    All failures return ``ok=False`` with a stable reason string. Never raises
    for validation problems (I/O/parse errors become deny reasons).
    """
    if path is None:
        return ChecklistValidationResult(
            ok=False,
            reason="T3: evidence checklist path is missing",
        )
    checklist_path = Path(path)
    if not str(checklist_path).strip():
        return ChecklistValidationResult(
            ok=False,
            reason="T3: evidence checklist path is empty",
        )

    exists = path_exists or (lambda p: p.is_file())
    if not exists(checklist_path):
        return ChecklistValidationResult(
            ok=False,
            reason="T3: evidence checklist file not found",
        )

    reader = read_text or (lambda p: p.read_text(encoding="utf-8"))
    try:
        raw = reader(checklist_path)
        data = json.loads(raw)
    except json.JSONDecodeError:
        return ChecklistValidationResult(
            ok=False,
            reason="T3: evidence checklist is not valid JSON",
        )
    except Exception as exc:  # noqa: BLE001 - fail closed
        return ChecklistValidationResult(
            ok=False,
            reason=f"T3: evidence checklist unreadable ({exc.__class__.__name__})",
        )

    if not isinstance(data, Mapping):
        return ChecklistValidationResult(
            ok=False,
            reason="T3: evidence checklist root must be a JSON object",
        )

    secret_hit = _find_forbidden_secret_key(data)
    if secret_hit is not None:
        return ChecklistValidationResult(
            ok=False,
            reason=(
                "T3: evidence checklist must not embed secret fields "
                f"(forbidden key {secret_hit!r})"
            ),
        )

    try:
        schema_version = int(data.get("schema_version"))
    except Exception:
        return ChecklistValidationResult(
            ok=False,
            reason="T3: evidence checklist schema_version missing or invalid",
        )
    if schema_version != CHECKLIST_SCHEMA_VERSION:
        return ChecklistValidationResult(
            ok=False,
            reason=(
                f"T3: evidence checklist schema_version must be "
                f"{CHECKLIST_SCHEMA_VERSION}; got {schema_version}"
            ),
            schema_version=schema_version,
        )

    checklist_id = str(data.get("checklist_id") or "").strip()
    if not checklist_id:
        return ChecklistValidationResult(
            ok=False,
            reason="T3: evidence checklist_id is required",
            schema_version=schema_version,
        )

    signed_off_by = str(data.get("signed_off_by") or "").strip()
    signed_off_at = str(data.get("signed_off_at") or "").strip()
    sign_off_confirmed = data.get("sign_off_confirmed")
    if not signed_off_by:
        return ChecklistValidationResult(
            ok=False,
            reason="T3: human sign-off missing signed_off_by",
            checklist_id=checklist_id,
            schema_version=schema_version,
        )
    if not signed_off_at:
        return ChecklistValidationResult(
            ok=False,
            reason="T3: human sign-off missing signed_off_at",
            checklist_id=checklist_id,
            schema_version=schema_version,
        )
    if sign_off_confirmed is not True:
        return ChecklistValidationResult(
            ok=False,
            reason="T3: human sign-off requires sign_off_confirmed=true",
            checklist_id=checklist_id,
            schema_version=schema_version,
        )

    items_raw = data.get("items")
    if not isinstance(items_raw, list):
        return ChecklistValidationResult(
            ok=False,
            reason="T3: evidence checklist items must be a list",
            checklist_id=checklist_id,
            schema_version=schema_version,
        )

    by_id: dict[str, Mapping[str, Any]] = {}
    for index, item in enumerate(items_raw):
        if not isinstance(item, Mapping):
            return ChecklistValidationResult(
                ok=False,
                reason=f"T3: evidence checklist item[{index}] must be an object",
                checklist_id=checklist_id,
                schema_version=schema_version,
            )
        item_id = str(item.get("id") or "").strip()
        if not item_id:
            return ChecklistValidationResult(
                ok=False,
                reason=f"T3: evidence checklist item[{index}] missing id",
                checklist_id=checklist_id,
                schema_version=schema_version,
            )
        if item_id in by_id:
            return ChecklistValidationResult(
                ok=False,
                reason=f"T3: duplicate evidence checklist item id {item_id!r}",
                checklist_id=checklist_id,
                schema_version=schema_version,
            )
        by_id[item_id] = item

    missing = tuple(
        item_id for item_id in REQUIRED_EVIDENCE_ITEM_IDS if item_id not in by_id
    )
    if missing:
        return ChecklistValidationResult(
            ok=False,
            reason=(
                "T3: evidence checklist missing required item(s): "
                + ",".join(missing)
            ),
            missing_items=missing,
            checklist_id=checklist_id,
            schema_version=schema_version,
        )

    root = base_dir if base_dir is not None else checklist_path.parent
    hasher = sha256_hex or _default_sha256_hex
    # Deterministic order: required ids ascending (already sorted tuple).
    for item_id in REQUIRED_EVIDENCE_ITEM_IDS:
        item = by_id[item_id]
        if item.get("complete") is not True:
            return ChecklistValidationResult(
                ok=False,
                reason=f"T3: evidence item {item_id!r} is not complete",
                checklist_id=checklist_id,
                schema_version=schema_version,
            )
        evidence_path_raw = str(item.get("evidence_path") or "").strip()
        if not evidence_path_raw:
            return ChecklistValidationResult(
                ok=False,
                reason=f"T3: evidence item {item_id!r} missing evidence_path",
                checklist_id=checklist_id,
                schema_version=schema_version,
            )
        evidence_path = Path(evidence_path_raw)
        if not evidence_path.is_absolute():
            evidence_path = root / evidence_path
        if not exists(evidence_path):
            return ChecklistValidationResult(
                ok=False,
                reason=(
                    f"T3: evidence item {item_id!r} evidence file not found"
                ),
                checklist_id=checklist_id,
                schema_version=schema_version,
            )
        expected_hash = str(item.get("evidence_sha256") or "").strip().lower()
        if expected_hash:
            try:
                actual = hasher(evidence_path).lower()
            except Exception as exc:  # noqa: BLE001
                return ChecklistValidationResult(
                    ok=False,
                    reason=(
                        f"T3: evidence item {item_id!r} hash unreadable "
                        f"({exc.__class__.__name__})"
                    ),
                    checklist_id=checklist_id,
                    schema_version=schema_version,
                )
            if actual != expected_hash:
                return ChecklistValidationResult(
                    ok=False,
                    reason=(
                        f"T3: evidence item {item_id!r} evidence_sha256 mismatch"
                    ),
                    checklist_id=checklist_id,
                    schema_version=schema_version,
                )

    return ChecklistValidationResult(
        ok=True,
        reason=None,
        missing_items=(),
        checklist_id=checklist_id,
        schema_version=schema_version,
    )


def _default_sha256_hex(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _find_forbidden_secret_key(value: object, *, _depth: int = 0) -> str | None:
    if _depth > 8:
        return None
    if isinstance(value, Mapping):
        for key, nested in value.items():
            key_l = str(key).strip().lower()
            if key_l in _FORBIDDEN_SECRET_KEYS:
                return key_l
            found = _find_forbidden_secret_key(nested, _depth=_depth + 1)
            if found is not None:
                return found
    elif isinstance(value, list):
        for nested in value:
            found = _find_forbidden_secret_key(nested, _depth=_depth + 1)
            if found is not None:
                return found
    return None
