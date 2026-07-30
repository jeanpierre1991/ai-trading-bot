"""M12.2 OperatorState schema + JsonPaperStateStore tests (no network)."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from core.exceptions import ConfigurationError
from core.types import OrderId, OrderType, PositionId, Side, Symbol
from order_manager.manager import OrderManager, OrderRecord, OrderState
from portfolio_manager.portfolio import Portfolio, Position
from runtime.paper_state import (
    OPERATOR_STATE_SCHEMA_VERSION,
    OperatorState,
    apply_order_manager_snapshot,
    apply_portfolio_snapshot,
    decimal_to_str,
    order_manager_to_snapshot,
    portfolio_to_snapshot,
)
from runtime.paper_state_store import JsonPaperStateStore


def _portfolio_with_position() -> Portfolio:
    portfolio = Portfolio(cash=Decimal("99999.99"))
    portfolio.positions["AAPL"] = Position(
        position_id=PositionId("pos-1"),
        symbol=Symbol("AAPL"),
        side=Side.BUY,
        quantity=Decimal("1.2345"),
        entry_price=Decimal("190.25"),
        current_price=Decimal("191.125"),
        opened_at=datetime(2026, 7, 15, 14, 30, tzinfo=timezone.utc),
    )
    return portfolio


def _order_manager_with_order() -> OrderManager:
    om = OrderManager()
    om.replace_all(
        {
            "ord-1": OrderRecord(
                order_id=OrderId("ord-1"),
                symbol=Symbol("AAPL"),
                side=Side.BUY,
                order_type=OrderType.MARKET,
                quantity=Decimal("1.2345"),
                price=None,
                state=OrderState.FILLED,
                created_at=datetime(2026, 7, 15, 14, 31, tzinfo=timezone.utc),
                updated_at=datetime(2026, 7, 15, 14, 32, tzinfo=timezone.utc),
            )
        }
    )
    return om


def _state(**overrides: object) -> OperatorState:
    portfolio = _portfolio_with_position()
    om = _order_manager_with_order()
    base = dict(
        schema_version=OPERATOR_STATE_SCHEMA_VERSION,
        portfolio=portfolio_to_snapshot(portfolio),
        order_manager=order_manager_to_snapshot(om),
        cycles_completed_total=3,
        operator_start_equity=Decimal("100000.00"),
        last_cycle_at=datetime(2026, 7, 15, 15, 0, tzinfo=timezone.utc),
        last_actionable_bar_timestamps={
            "AAPL": "2026-07-15T14:00:00+00:00",
        },
    )
    base.update(overrides)
    if isinstance(base.get("operator_start_equity"), Decimal):
        pass
    return OperatorState(**base)  # type: ignore[arg-type]


def test_decimal_string_round_trip_preserves_precision() -> None:
    value = Decimal("190.2500001")
    encoded = decimal_to_str(value)
    assert "." in encoded
    assert Decimal(encoded) == value
    assert "e" not in encoded.lower()


def test_portfolio_snapshot_round_trip_decimal_precision() -> None:
    original = _portfolio_with_position()
    snap = portfolio_to_snapshot(original)
    assert isinstance(snap["cash"], str)
    assert isinstance(snap["positions"]["AAPL"]["quantity"], str)
    assert "float" not in str(type(snap["cash"]))

    restored = Portfolio(cash=Decimal("0"))
    apply_portfolio_snapshot(restored, snap)
    assert restored.cash == original.cash
    assert restored.positions["AAPL"].quantity == Decimal("1.2345")
    assert restored.positions["AAPL"].entry_price == Decimal("190.25")
    assert restored.positions["AAPL"].current_price == Decimal("191.125")


def test_order_manager_snapshot_round_trip() -> None:
    original = _order_manager_with_order()
    snap = order_manager_to_snapshot(original)
    assert isinstance(snap["orders"][0]["quantity"], str)
    restored = OrderManager()
    apply_order_manager_snapshot(restored, snap)
    assert restored.order_count == 1
    order = restored.get_order("ord-1")
    assert order is not None
    assert order.quantity == Decimal("1.2345")
    assert order.state is OrderState.FILLED
    assert order.price is None


def test_operator_state_json_round_trip(tmp_path: Path) -> None:
    store = JsonPaperStateStore(tmp_path / "state.json")
    original = _state()
    store.save(original)
    loaded = store.load()
    assert loaded.schema_version == OPERATOR_STATE_SCHEMA_VERSION
    assert loaded.cycles_completed_total == 3
    assert loaded.operator_start_equity == Decimal("100000.00")
    assert loaded.last_cycle_at == datetime(2026, 7, 15, 15, 0, tzinfo=timezone.utc)
    assert "AAPL" in loaded.last_actionable_bar_timestamps
    assert loaded.portfolio["cash"] == "99999.99"


def test_last_cycle_at_utc_serialization(tmp_path: Path) -> None:
    store = JsonPaperStateStore(tmp_path / "state.json")
    store.save(_state())
    raw = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert raw["last_cycle_at"].endswith("Z") or "+00:00" in raw["last_cycle_at"]
    loaded = store.load()
    assert loaded.last_cycle_at is not None
    assert loaded.last_cycle_at.tzinfo is not None
    assert loaded.last_cycle_at.utcoffset().total_seconds() == 0


def test_missing_state_exists_false(tmp_path: Path) -> None:
    store = JsonPaperStateStore(tmp_path / "missing.json")
    assert store.exists() is False
    with pytest.raises(ConfigurationError, match="not found"):
        store.load()


def test_corrupt_json_refuses(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    path.write_text("{not-json", encoding="utf-8")
    with pytest.raises(ConfigurationError, match="corrupt"):
        JsonPaperStateStore(path).load()


def test_unsupported_version_refuses(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    state = _state().to_json_dict()
    state["schema_version"] = 999
    path.write_text(json.dumps(state), encoding="utf-8")
    with pytest.raises(ConfigurationError, match="unsupported"):
        JsonPaperStateStore(path).load()


def test_missing_required_field_refuses(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    state = _state().to_json_dict()
    del state["operator_start_equity"]
    path.write_text(json.dumps(state), encoding="utf-8")
    with pytest.raises(ConfigurationError, match="missing required field"):
        JsonPaperStateStore(path).load()


def test_float_equity_refuses(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    state = _state().to_json_dict()
    state["operator_start_equity"] = 100000.0
    path.write_text(json.dumps(state), encoding="utf-8")
    with pytest.raises(ConfigurationError, match="floats are not allowed"):
        JsonPaperStateStore(path).load()


def test_invalid_portfolio_refuses(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    state = _state().to_json_dict()
    state["portfolio"] = {"cash": "1"}  # missing positions
    path.write_text(json.dumps(state), encoding="utf-8")
    with pytest.raises(ConfigurationError, match="cash/positions"):
        JsonPaperStateStore(path).load()


def test_atomic_replacement_does_not_leave_tmp_as_final(
    tmp_path: Path, monkeypatch
) -> None:
    path = tmp_path / "state.json"
    store = JsonPaperStateStore(path)
    store.save(_state(cycles_completed_total=1))

    real_replace = os.replace

    def _boom(src: str | os.PathLike[str], dst: str | os.PathLike[str]) -> None:
        raise OSError("simulated replace failure")

    monkeypatch.setattr(os, "replace", _boom)
    with pytest.raises(ConfigurationError, match="atomically save"):
        store.save(_state(cycles_completed_total=2))

    # Original file remains readable previous version; no successful v2.
    monkeypatch.setattr(os, "replace", real_replace)
    loaded = store.load()
    assert loaded.cycles_completed_total == 1
    tmp_leftovers = list(tmp_path.glob(".state.json.*.tmp"))
    assert tmp_leftovers == []


def test_atomic_save_overwrites_cleanly(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    store = JsonPaperStateStore(path)
    store.save(_state(cycles_completed_total=1))
    store.save(_state(cycles_completed_total=7))
    assert store.load().cycles_completed_total == 7
    assert list(tmp_path.glob("*.tmp")) == []
