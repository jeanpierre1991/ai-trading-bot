"""M14.3 evidence checklist + trial enablement fail-closed tests."""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from broker_interface.adapter_registry import ENDPOINT_LIVE_PRODUCTION, is_approved_adapter
from config.settings import Settings
from core.exceptions import ConfigurationError
from core.types import TradingMode
from runtime.evidence_checklist import (
    REQUIRED_EVIDENCE_ITEM_IDS,
    validate_evidence_checklist,
)
from runtime.factory import create_trading_runtime
from runtime.live_enablement import (
    EXPECTED_LIVE_CONFIRM_TOKEN,
    LiveExecutionContext,
    evaluate_live_enablement,
)
from runtime.trial_enablement import (
    EXPECTED_LIVE_TRIAL_CONFIRM_TOKEN,
    evaluate_trial_enablement,
)


def _write_evidence_files(root: Path) -> dict[str, Path]:
    paths: dict[str, Path] = {}
    for item_id in REQUIRED_EVIDENCE_ITEM_IDS:
        path = root / f"{item_id}.txt"
        path.write_text(f"evidence for {item_id}\n", encoding="utf-8")
        paths[item_id] = path
    return paths


def _complete_checklist_dict(evidence: dict[str, Path], *, include_hash: bool = False) -> dict[str, Any]:
    items = []
    for item_id in REQUIRED_EVIDENCE_ITEM_IDS:
        entry: dict[str, Any] = {
            "id": item_id,
            "description": item_id,
            "evidence_path": str(evidence[item_id].name),
            "complete": True,
        }
        if include_hash:
            digest = hashlib.sha256(evidence[item_id].read_bytes()).hexdigest()
            entry["evidence_sha256"] = digest
        items.append(entry)
    return {
        "schema_version": 1,
        "checklist_id": "test-checklist",
        "signed_off_by": "tester",
        "signed_off_at": "2026-08-01T12:00:00+00:00",
        "sign_off_confirmed": True,
        "items": items,
    }


def _write_checklist(tmp_path: Path, payload: dict[str, Any]) -> Path:
    path = tmp_path / "checklist.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _trial_settings(tmp_path: Path, **overrides: Any) -> Settings:
    evidence = _write_evidence_files(tmp_path)
    checklist = _write_checklist(tmp_path, _complete_checklist_dict(evidence))
    values: dict[str, Any] = {
        "trading_mode": "live",
        "live_trading_enabled": True,
        "live_confirm_token": EXPECTED_LIVE_CONFIRM_TOKEN,
        "live_trial_confirm_token": EXPECTED_LIVE_TRIAL_CONFIRM_TOKEN,
        "broker_endpoint_class": "live_production",
        "broker_name": "alpaca_paper",
        "broker_api_key": "key",
        "broker_api_secret": "secret",
        "broker_base_url": "https://api.example.com",
        "live_max_order_notional": Decimal("100"),
        "live_max_orders_per_day": 2,
        "trial_max_order_notional": Decimal("50"),
        "trial_max_orders_per_day": 1,
        "trial_evidence_checklist_path": checklist,
        "live_order_ledger_path": tmp_path / "ledger.json",
        "market_data_freshness_enabled": False,
    }
    values.update(overrides)
    return Settings(**values)


def _live_ctx(**overrides: Any) -> LiveExecutionContext:
    values = {
        "command": "run-once",
        "execution": "live",
        "context_mode": TradingMode.LIVE,
    }
    values.update(overrides)
    return LiveExecutionContext(**values)


def test_validate_complete_checklist(tmp_path: Path) -> None:
    evidence = _write_evidence_files(tmp_path)
    path = _write_checklist(tmp_path, _complete_checklist_dict(evidence, include_hash=True))
    result = validate_evidence_checklist(path, base_dir=tmp_path)
    assert result.ok is True
    assert result.reason is None
    assert result.checklist_id == "test-checklist"


def test_example_checklist_validates() -> None:
    root = Path(__file__).resolve().parents[2]
    example = root / "docs" / "evidence" / "live_trial_checklist.example.json"
    result = validate_evidence_checklist(example)
    assert result.ok is True, result.reason


def test_missing_path_denies() -> None:
    result = validate_evidence_checklist(None)
    assert result.ok is False
    assert "T3" in (result.reason or "")


def test_missing_required_item_denies(tmp_path: Path) -> None:
    evidence = _write_evidence_files(tmp_path)
    payload = _complete_checklist_dict(evidence)
    payload["items"] = [i for i in payload["items"] if i["id"] != "m12_paper_soak"]
    path = _write_checklist(tmp_path, payload)
    result = validate_evidence_checklist(path, base_dir=tmp_path)
    assert result.ok is False
    assert "m12_paper_soak" in (result.reason or "")
    assert "m12_paper_soak" in result.missing_items


def test_incomplete_item_denies(tmp_path: Path) -> None:
    evidence = _write_evidence_files(tmp_path)
    payload = _complete_checklist_dict(evidence)
    for item in payload["items"]:
        if item["id"] == "m13_shadow_validation":
            item["complete"] = False
    path = _write_checklist(tmp_path, payload)
    result = validate_evidence_checklist(path, base_dir=tmp_path)
    assert result.ok is False
    assert "m13_shadow_validation" in (result.reason or "")


def test_missing_evidence_file_denies(tmp_path: Path) -> None:
    evidence = _write_evidence_files(tmp_path)
    payload = _complete_checklist_dict(evidence)
    for item in payload["items"]:
        if item["id"] == "m10_strategy_validation_pack":
            item["evidence_path"] = "does-not-exist.txt"
    path = _write_checklist(tmp_path, payload)
    result = validate_evidence_checklist(path, base_dir=tmp_path)
    assert result.ok is False
    assert "not found" in (result.reason or "")


def test_hash_mismatch_denies(tmp_path: Path) -> None:
    evidence = _write_evidence_files(tmp_path)
    payload = _complete_checklist_dict(evidence, include_hash=True)
    for item in payload["items"]:
        if item["id"] == "m14_kill_cancel_dry_run":
            item["evidence_sha256"] = "0" * 64
    path = _write_checklist(tmp_path, payload)
    result = validate_evidence_checklist(path, base_dir=tmp_path)
    assert result.ok is False
    assert "sha256 mismatch" in (result.reason or "")


def test_sign_off_required(tmp_path: Path) -> None:
    evidence = _write_evidence_files(tmp_path)
    payload = _complete_checklist_dict(evidence)
    payload["sign_off_confirmed"] = False
    path = _write_checklist(tmp_path, payload)
    result = validate_evidence_checklist(path, base_dir=tmp_path)
    assert result.ok is False
    assert "sign_off_confirmed" in (result.reason or "")


def test_secrets_in_checklist_denied(tmp_path: Path) -> None:
    evidence = _write_evidence_files(tmp_path)
    payload = _complete_checklist_dict(evidence)
    payload["api_key"] = "should-never-be-here"
    path = _write_checklist(tmp_path, payload)
    result = validate_evidence_checklist(path, base_dir=tmp_path)
    assert result.ok is False
    assert "secret" in (result.reason or "").lower()


def test_trial_enablement_incomplete_checklist_denies(tmp_path: Path) -> None:
    settings = _trial_settings(tmp_path, trial_evidence_checklist_path=None)
    auth = evaluate_trial_enablement(settings, _live_ctx())
    assert auth.authorized is False
    assert "T3" in (auth.reason or "")


def test_trial_enablement_complete_checklist_still_denies_production(tmp_path: Path) -> None:
    settings = _trial_settings(tmp_path)
    auth = evaluate_trial_enablement(settings, _live_ctx())
    assert auth.authorized is False
    assert "T8" in (auth.reason or "") or "T9" in (auth.reason or "")
    assert not is_approved_adapter("alpaca_paper", ENDPOINT_LIVE_PRODUCTION)


def test_live_enablement_production_uses_checklist_gate(tmp_path: Path) -> None:
    settings = _trial_settings(tmp_path, trial_evidence_checklist_path=None)
    auth = evaluate_live_enablement(settings, _live_ctx())
    assert auth.authorized is False
    assert "G9" in (auth.reason or "")
    assert "M14" in (auth.reason or "")
    assert "T3" in (auth.reason or "")


def test_live_enablement_sandbox_unaffected_by_missing_checklist(tmp_path: Path) -> None:
    settings = Settings(
        trading_mode="live",
        live_trading_enabled=True,
        live_confirm_token=EXPECTED_LIVE_CONFIRM_TOKEN,
        broker_endpoint_class="broker_sandbox",
        broker_name="alpaca_paper",
        broker_api_key="key",
        broker_api_secret="secret",
        broker_base_url="https://paper-api.alpaca.markets",
        live_max_order_notional=Decimal("1000"),
        live_max_orders_per_day=5,
        live_order_ledger_path=tmp_path / "ledger.json",
        trial_evidence_checklist_path=None,
    )
    auth = evaluate_live_enablement(settings, _live_ctx())
    assert auth.authorized is True


def test_factory_production_denied_with_incomplete_checklist(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match="G9|T3|LIVE_PRODUCTION|M14"):
        create_trading_runtime(
            _trial_settings(tmp_path, trial_evidence_checklist_path=None),
            execution="live",
            live_command="run-once",
            market_data=object(),
            strategy_engine=object(),
            portfolio=object(),
        )


def test_factory_production_denied_even_with_complete_checklist(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match="G9|T8|T9|LIVE_PRODUCTION|M14"):
        create_trading_runtime(
            _trial_settings(tmp_path),
            execution="live",
            live_command="run-once",
            market_data=object(),
            strategy_engine=object(),
            portfolio=object(),
        )


def test_wrong_trial_token_denies_without_echoing_secret(tmp_path: Path) -> None:
    settings = _trial_settings(tmp_path, live_trial_confirm_token="WRONG")
    auth = evaluate_trial_enablement(settings, _live_ctx())
    assert auth.authorized is False
    assert "T4" in (auth.reason or "")
    assert "WRONG" not in (auth.reason or "")
    assert EXPECTED_LIVE_TRIAL_CONFIRM_TOKEN not in (auth.reason or "")


def test_paper_operator_command_cannot_authorize_trial(tmp_path: Path) -> None:
    settings = _trial_settings(tmp_path)
    auth = evaluate_trial_enablement(
        settings,
        _live_ctx(command="run-paper-operator"),
    )
    assert auth.authorized is False
    assert "T7" in (auth.reason or "")


def test_no_bypass_via_sign_off_false_string(tmp_path: Path) -> None:
    evidence = _write_evidence_files(tmp_path)
    payload = _complete_checklist_dict(evidence)
    payload["sign_off_confirmed"] = "true"  # must be JSON boolean true
    path = _write_checklist(tmp_path, payload)
    result = validate_evidence_checklist(path, base_dir=tmp_path)
    assert result.ok is False
