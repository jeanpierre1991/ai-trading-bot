"""M12.4 state-path parent/writability preflight tests (no network)."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import pytest

from config.settings import Settings
from core.exceptions import ConfigurationError
from order_manager.manager import OrderManager
from portfolio_manager.portfolio import Portfolio
from runtime.kill_switch import FileEnvKillSwitch
from runtime.models import PipelineResult
from runtime.paper_operator import PaperOperator, PaperOperatorConfig
from runtime.paper_state import (
    OPERATOR_STATE_SCHEMA_VERSION,
    OperatorState,
    order_manager_to_snapshot,
    portfolio_to_snapshot,
)
from runtime.paper_state_store import JsonPaperStateStore


def _settings() -> Settings:
    return Settings(
        trading_mode="paper",
        market_data_provider="mock",
        market_hours_enabled=True,
        market_hours_policy="reject",
        alerts_enabled=False,
    )


def _minimal_state() -> OperatorState:
    portfolio = Portfolio(cash=Decimal("100000"))
    return OperatorState(
        schema_version=OPERATOR_STATE_SCHEMA_VERSION,
        portfolio=portfolio_to_snapshot(portfolio),
        order_manager=order_manager_to_snapshot(OrderManager()),
        cycles_completed_total=0,
        operator_start_equity=Decimal("100000"),
        last_cycle_at=None,
        last_actionable_bar_timestamps={},
    )


def test_ensure_parent_writable_creates_nested_missing_parent(
    tmp_path: Path,
) -> None:
    state_path = tmp_path / "nested" / "deeper" / "state.json"
    assert not state_path.parent.exists()
    store = JsonPaperStateStore(state_path)
    store.ensure_parent_writable()
    assert state_path.parent.is_dir()
    assert not state_path.exists()


def test_ensure_parent_writable_mkdir_failure_fails_closed(
    tmp_path: Path,
) -> None:
    state_path = tmp_path / "blocked" / "state.json"
    store = JsonPaperStateStore(state_path)
    with patch.object(Path, "mkdir", side_effect=OSError("permission denied")):
        with pytest.raises(ConfigurationError, match="failed to create state directory"):
            store.ensure_parent_writable()
    assert not state_path.exists()


def test_ensure_parent_writable_probe_failure_fails_closed(
    tmp_path: Path,
) -> None:
    parent = tmp_path / "ro"
    parent.mkdir()
    state_path = parent / "state.json"
    store = JsonPaperStateStore(state_path)
    with patch(
        "runtime.paper_state_store.tempfile.mkstemp",
        side_effect=OSError("read-only filesystem"),
    ):
        with pytest.raises(ConfigurationError, match="not writable"):
            store.ensure_parent_writable()
    assert not state_path.exists()


def test_save_still_creates_parent_and_round_trips(tmp_path: Path) -> None:
    state_path = tmp_path / "a" / "b" / "state.json"
    store = JsonPaperStateStore(state_path)
    store.ensure_parent_writable()
    store.save(_minimal_state())
    loaded = store.load()
    assert loaded.operator_start_equity == Decimal("100000")


def test_operator_preflight_fails_before_run_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("PAPER_OPERATOR_KILL", raising=False)
    state_path = tmp_path / "missing_parent" / "state.json"

    class _Runtime:
        def __init__(self) -> None:
            self.portfolio = Portfolio(cash=Decimal("100000"))
            self.order_manager = OrderManager()
            self.calls = 0

        def run_once(self, _context: object) -> PipelineResult:
            self.calls += 1
            return PipelineResult(success=True, stage_reached="portfolio")

    runtime = _Runtime()
    with patch.object(
        JsonPaperStateStore,
        "ensure_parent_writable",
        side_effect=ConfigurationError("operator state directory is not writable"),
    ):
        with pytest.raises(ConfigurationError, match="not writable"):
            PaperOperator(
                runtime,  # type: ignore[arg-type]
                settings=_settings(),
                kill_switch=FileEnvKillSwitch(tmp_path / "KILL"),
                sleeper=lambda _s: None,
            ).run(
                PaperOperatorConfig(
                    symbol="AAPL",
                    max_cycles=3,
                    max_wall_time_seconds=60.0,
                    interval_seconds=1.0,
                    state_path=state_path,
                )
            )
    assert runtime.calls == 0
    assert runtime.portfolio.cash == Decimal("100000")
    assert not state_path.exists()


def test_operator_preflight_does_not_wipe_corrupt_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("PAPER_OPERATOR_KILL", raising=False)
    state_path = tmp_path / "state.json"
    corrupt = "{bad-json"
    state_path.write_text(corrupt, encoding="utf-8")

    class _Runtime:
        portfolio = Portfolio(cash=Decimal("100000"))
        order_manager = OrderManager()
        calls = 0

        def run_once(self, _context: object) -> PipelineResult:
            self.calls += 1
            return PipelineResult(success=True, stage_reached="portfolio")

    runtime = _Runtime()
    with pytest.raises(ConfigurationError, match="corrupt"):
        PaperOperator(
            runtime,  # type: ignore[arg-type]
            settings=_settings(),
            kill_switch=FileEnvKillSwitch(tmp_path / "KILL"),
            sleeper=lambda _s: None,
        ).run(
            PaperOperatorConfig(
                symbol="AAPL",
                max_cycles=1,
                max_wall_time_seconds=60.0,
                interval_seconds=1.0,
                state_path=state_path,
            )
        )
    assert runtime.calls == 0
    assert state_path.read_text(encoding="utf-8") == corrupt
    assert runtime.portfolio.cash == Decimal("100000")
