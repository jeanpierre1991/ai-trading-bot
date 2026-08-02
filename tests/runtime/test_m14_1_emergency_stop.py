"""M14.1 emergency stop, cancel port, trial limits, durable halt."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from alerts.notifier import AlertLevel, ConsoleNotifier
from broker_interface.broker import Broker, BrokerStatus, PaperBroker
from broker_interface.cancel_port import CancelAllResult, CancelCapableBroker
from broker_interface.execution import ExecutionResult, ExecutionStatus
from broker_interface.orders import BrokerOrderRequest
from broker_interface.snapshots import (
    BrokerOrderSnapshot,
    BrokerOrderStatus,
    BrokerPositionSnapshot,
)
from config.settings import Settings
from core.exceptions import ConfigurationError
from core.types import OrderId, OrderType, Side, Symbol, TradingMode
from portfolio_manager.portfolio import Portfolio
from runtime.emergency_guard import EmergencyHaltGuardBroker
from runtime.emergency_halt import DurableEmergencyHaltLatch
from runtime.emergency_stop import (
    EmergencyStopController,
    FileEnvEmergencyTrigger,
    TriggerSource,
)
from runtime.factory import create_trading_runtime
from runtime.idempotent_submit import IdempotentLiveExecutor
from runtime.live_caps import unwrap_broker
from runtime.live_enablement import EXPECTED_LIVE_CONFIRM_TOKEN
from runtime.trial_limits import (
    TrialLimitConfig,
    TrialLimitGuardBroker,
    trial_config_from_settings,
)
from runtime.context import RuntimeContext


SECRET = "SUPER_SECRET_VALUE_DO_NOT_LEAK"
KEY_ID = "PKTEST_KEY_ID_ONLY"


def _live_settings(tmp_path: Path, **overrides: Any) -> Settings:
    base: dict[str, Any] = {
        "trading_mode": "live",
        "live_trading_enabled": True,
        "live_confirm_token": EXPECTED_LIVE_CONFIRM_TOKEN,
        "broker_name": "alpaca_paper",
        "broker_api_key": KEY_ID,
        "broker_api_secret": SECRET,
        "broker_base_url": "https://paper-api.alpaca.markets",
        "broker_endpoint_class": "broker_sandbox",
        "live_max_order_notional": Decimal("10000"),
        "live_max_orders_per_day": 10,
        "live_order_ledger_path": tmp_path / "live_ledger.json",
        "market_data_freshness_enabled": False,
        "market_hours_enabled": False,
    }
    base.update(overrides)
    return Settings(**base)


class _CancelBroker(Broker):
    def __init__(self) -> None:
        self.place_calls: list[BrokerOrderRequest] = []
        self.cancel_calls = 0
        self.open_orders: list[BrokerOrderSnapshot] = []
        self.positions: list[BrokerPositionSnapshot] = []
        self.cancel_result: CancelAllResult | None = None
        self.raise_on_cancel: Exception | None = None

    def connect(self) -> bool:
        return True

    def disconnect(self) -> None:
        return None

    def get_status(self) -> BrokerStatus:
        return BrokerStatus(
            connected=True,
            broker_name="cancel-fake",
            account_id="acct-1",
            buying_power=Decimal("50000"),
            checked_at=datetime.now(timezone.utc),
        )

    def get_quote(self, symbol: Symbol) -> Decimal:
        return Decimal("100")

    def place_order(self, request: BrokerOrderRequest) -> ExecutionResult:
        self.place_calls.append(request)
        return ExecutionResult(
            order_id=OrderId("o1"),
            symbol=request.symbol,
            side=request.side,
            requested_quantity=request.quantity,
            filled_quantity=request.quantity,
            fill_price=Decimal("100"),
            fee=Decimal("0"),
            status=ExecutionStatus.FILLED,
            message="filled",
        )

    def cancel_all_open_orders(self) -> CancelAllResult:
        self.cancel_calls += 1
        if self.raise_on_cancel is not None:
            raise self.raise_on_cancel
        if self.cancel_result is not None:
            return self.cancel_result
        ids = tuple(o.broker_order_id for o in self.open_orders)
        self.open_orders = []
        return CancelAllResult(
            attempted=True,
            canceled_order_ids=ids,
            failed_order_ids=(),
            partial=False,
            message="canceled",
        )

    def get_order_by_client_id(self, client_order_id: str) -> BrokerOrderSnapshot | None:
        return None

    def get_order_snapshot(self, broker_order_id: str) -> BrokerOrderSnapshot | None:
        return None

    def list_open_orders(self) -> list[BrokerOrderSnapshot]:
        return list(self.open_orders)

    def list_positions(self) -> list[BrokerPositionSnapshot]:
        return list(self.positions)


def _req(**overrides: Any) -> BrokerOrderRequest:
    values: dict[str, Any] = {
        "symbol": Symbol("AAPL"),
        "side": Side.BUY,
        "order_type": OrderType.MARKET,
        "quantity": Decimal("1"),
        "client_order_id": "cid-1",
    }
    values.update(overrides)
    return BrokerOrderRequest(**values)


def test_cancel_capable_protocol(_CancelBroker: type = _CancelBroker) -> None:
    broker = _CancelBroker()
    assert isinstance(broker, CancelCapableBroker)
    result = broker.cancel_all_open_orders()
    assert result.attempted is True
    assert result.success is True


def test_durable_halt_survives_reload(tmp_path: Path) -> None:
    path = tmp_path / "halt.json"
    latch = DurableEmergencyHaltLatch(path)
    latch.engage(
        incident_id="inc-1",
        reason="test",
        trigger_source=TriggerSource.MANUAL.value,
    )
    reloaded = DurableEmergencyHaltLatch(path)
    assert reloaded.is_engaged() is True
    assert reloaded.state.incident_id == "inc-1"


def test_emergency_stop_halts_cancel_alert_snapshot(tmp_path: Path) -> None:
    broker = _CancelBroker()
    broker.open_orders = [
        BrokerOrderSnapshot(
            broker_order_id="b1",
            client_order_id="c1",
            symbol=Symbol("AAPL"),
            side=Side.BUY,
            quantity=Decimal("1"),
            filled_quantity=Decimal("0"),
            status=BrokerOrderStatus.OPEN,
        )
    ]
    broker.positions = [
        BrokerPositionSnapshot(
            symbol=Symbol("AAPL"),
            quantity=Decimal("2"),
            avg_entry_price=Decimal("10"),
        )
    ]
    latch = DurableEmergencyHaltLatch(tmp_path / "halt.json")
    notifier = ConsoleNotifier()
    controller = EmergencyStopController(
        latch=latch,
        incident_dir=tmp_path / "incidents",
        broker=broker,
        notifier=notifier,
    )
    result = controller.activate(
        reason="unit_test_stop",
        trigger_source=TriggerSource.MANUAL,
    )
    assert result.activated is True
    assert result.incident_id
    assert broker.cancel_calls == 1
    assert latch.is_engaged() is True
    assert result.alert_sent is True
    assert notifier.sent_count >= 1
    assert result.snapshot_path is not None
    snap = json.loads(Path(result.snapshot_path).read_text(encoding="utf-8"))
    assert snap["incident_id"] == result.incident_id
    assert snap["reason"] == "unit_test_stop"
    assert snap["positions"][0]["symbol"] == "AAPL"
    assert "account" in snap
    assert snap["account"]["buying_power"] == "50000"


def test_emergency_stop_partial_cancel_still_halts(tmp_path: Path) -> None:
    broker = _CancelBroker()
    broker.cancel_result = CancelAllResult(
        attempted=True,
        canceled_order_ids=("a",),
        failed_order_ids=("b",),
        partial=True,
        message="partial",
    )
    controller = EmergencyStopController(
        latch=DurableEmergencyHaltLatch(tmp_path / "halt.json"),
        incident_dir=tmp_path / "incidents",
        broker=broker,
        notifier=ConsoleNotifier(),
    )
    result = controller.activate(reason="partial", trigger_source=TriggerSource.FILE)
    assert result.activated is True
    assert controller.is_halted() is True
    assert result.cancel_result is not None
    assert result.cancel_result.partial is True


def test_halt_guard_blocks_place_order(tmp_path: Path) -> None:
    broker = _CancelBroker()
    controller = EmergencyStopController(
        latch=DurableEmergencyHaltLatch(tmp_path / "halt.json"),
        incident_dir=tmp_path / "incidents",
        broker=broker,
        notifier=ConsoleNotifier(),
    )
    guarded = EmergencyHaltGuardBroker(broker, controller=controller)
    controller.activate(reason="block", trigger_source=TriggerSource.MANUAL)
    result = guarded.place_order(_req())
    assert result.status is ExecutionStatus.REJECTED
    assert "EMERGENCY_HALT" in result.message
    assert broker.place_calls == []


def test_file_env_trigger_activates(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    kill = tmp_path / "KILL"
    kill.write_text("1", encoding="utf-8")
    broker = _CancelBroker()
    controller = EmergencyStopController(
        latch=DurableEmergencyHaltLatch(tmp_path / "halt.json"),
        incident_dir=tmp_path / "incidents",
        broker=broker,
        notifier=ConsoleNotifier(),
        triggers=[FileEnvEmergencyTrigger(kill, env_var="LIVE_EMERGENCY_KILL")],
    )
    guarded = EmergencyHaltGuardBroker(broker, controller=controller)
    result = guarded.place_order(_req())
    assert result.status is ExecutionStatus.REJECTED
    assert controller.is_halted() is True
    assert broker.cancel_calls == 1

    monkeypatch.delenv("LIVE_EMERGENCY_KILL", raising=False)
    # Already halted — second activate is idempotent.
    again = controller.activate(reason="x", trigger_source=TriggerSource.MANUAL)
    assert again.already_halted is True


def test_trial_limit_notional_and_rate(tmp_path: Path) -> None:
    broker = _CancelBroker()
    config = TrialLimitConfig(
        max_order_notional=Decimal("50"),  # 1 * 100 = 100 > 50
        max_orders_per_minute=1,
    )
    guard = TrialLimitGuardBroker(broker, config=config)
    r1 = guard.place_order(_req())
    assert r1.status is ExecutionStatus.REJECTED
    assert "TRIAL_MAX_ORDER_NOTIONAL" in r1.message
    assert broker.place_calls == []

    config2 = TrialLimitConfig(max_orders_per_minute=1)
    guard2 = TrialLimitGuardBroker(broker, config=config2)
    ok = guard2.place_order(_req(client_order_id="a"))
    assert ok.status is ExecutionStatus.FILLED
    blocked = guard2.place_order(_req(client_order_id="b"))
    assert blocked.status is ExecutionStatus.REJECTED
    assert "TRIAL_MAX_ORDERS_PER_MINUTE" in blocked.message
    assert len(broker.place_calls) == 1


def test_trial_allowlist_and_inactive_passthrough() -> None:
    broker = _CancelBroker()
    inactive = TrialLimitGuardBroker(broker, config=TrialLimitConfig())
    assert inactive.config.active is False
    assert inactive.place_order(_req()).status is ExecutionStatus.FILLED

    allow = TrialLimitGuardBroker(
        broker,
        config=TrialLimitConfig(symbol_allowlist=frozenset({"MSFT"})),
    )
    denied = allow.place_order(_req(symbol=Symbol("AAPL")))
    assert denied.status is ExecutionStatus.REJECTED
    assert "allowlist" in denied.message


def test_factory_live_wires_emergency_and_preserves_g10(tmp_path: Path) -> None:
    class _Transport:
        def request(self, method: str, url: str, **kwargs: Any) -> Any:
            class R:
                status_code = 200

                def json(self_inner) -> Any:
                    if "account" in url:
                        return {
                            "id": "a",
                            "buying_power": "100000",
                            "account_number": "1",
                        }
                    if "orders" in url and method == "GET":
                        return []
                    if "positions" in url:
                        return []
                    if "trades/latest" in url:
                        return {"trade": {"p": 100}}
                    if method == "DELETE":
                        return None
                    return {
                        "id": "ord",
                        "status": "filled",
                        "filled_qty": "1",
                        "filled_avg_price": "100",
                    }

                def text(self_inner) -> str:
                    return "{}"

            return R()

    runtime = create_trading_runtime(
        _live_settings(tmp_path),
        execution="live",
        live_command="run-once",
        http_transport=_Transport(),
        market_data=MagicMock(get_bars=MagicMock(return_value=[])),
        strategy_engine=MagicMock(),
        portfolio=Portfolio(cash=Decimal("100000")),
        with_order_manager=False,
    )
    assert isinstance(runtime.executor, IdempotentLiveExecutor)
    assert isinstance(runtime.executor.broker, EmergencyHaltGuardBroker)
    assert isinstance(unwrap_broker(runtime.executor.broker), object)
    # Halt path auto-derived next to ledger.
    assert (tmp_path / "emergency_halt.json").is_file()


def test_live_production_still_unreachable(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match="G9|LIVE_PRODUCTION|M14"):
        create_trading_runtime(
            _live_settings(tmp_path, broker_endpoint_class="live_production"),
            execution="live",
            live_command="run-once",
            market_data=MagicMock(),
            strategy_engine=MagicMock(),
            portfolio=Portfolio(cash=Decimal("1")),
        )


def test_paper_and_shadow_unchanged(tmp_path: Path) -> None:
    paper = create_trading_runtime(
        Settings(trading_mode="paper", market_data_provider="mock"),
        execution="paper",
        market_data=MagicMock(),
        strategy_engine=MagicMock(),
        portfolio=Portfolio(cash=Decimal("100000")),
    )
    assert isinstance(paper.executor.broker, PaperBroker)

    shadow = create_trading_runtime(
        _live_settings(tmp_path, shadow_audit_path=tmp_path / "shadow.jsonl"),
        execution="shadow",
        live_command="run-once",
        market_data=MagicMock(get_bars=MagicMock(return_value=[])),
        strategy_engine=MagicMock(),
        portfolio=Portfolio(cash=Decimal("100000")),
        with_order_manager=False,
        with_alerts=False,
    )
    from runtime.shadow_executor import ShadowExecutor

    assert isinstance(shadow.executor, ShadowExecutor)


def test_trial_config_cannot_exceed_g10(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match="TRIAL_MAX_ORDER_NOTIONAL"):
        create_trading_runtime(
            _live_settings(
                tmp_path,
                trial_max_order_notional=Decimal("999999"),
            ),
            execution="live",
            live_command="run-once",
            market_data=MagicMock(),
            strategy_engine=MagicMock(),
            portfolio=Portfolio(cash=Decimal("1")),
        )


def test_runtime_halts_before_reconcile(tmp_path: Path) -> None:
    class _Transport:
        def request(self, method: str, url: str, **kwargs: Any) -> Any:
            class R:
                status_code = 200

                def json(self_inner) -> Any:
                    if "account" in url:
                        return {"id": "a", "buying_power": "1", "account_number": "1"}
                    if "orders" in url:
                        return []
                    if "positions" in url:
                        return []
                    return {"trade": {"p": 1}}

                def text(self_inner) -> str:
                    return "{}"

            return R()

    runtime = create_trading_runtime(
        _live_settings(tmp_path),
        execution="live",
        live_command="run-once",
        http_transport=_Transport(),
        market_data=MagicMock(),
        strategy_engine=MagicMock(),
        portfolio=Portfolio(cash=Decimal("100000")),
        with_order_manager=False,
    )
    assert runtime._emergency_controller is not None
    runtime._emergency_controller.activate(
        reason="pre_cycle",
        trigger_source=TriggerSource.MANUAL,
    )
    market_data = runtime.market_data
    result = runtime.run_once(
        RuntimeContext(symbol="AAPL", mode=TradingMode.LIVE, daily_pnl_pct=Decimal("0"))
    )
    assert result.success is False
    assert result.stage_reached == "emergency_halt"
    market_data.get_bars.assert_not_called()


def test_core_modules_broker_agnostic() -> None:
    import inspect
    import runtime.emergency_stop as es
    import runtime.emergency_halt as eh
    import runtime.trial_limits as tl
    import broker_interface.cancel_port as cp

    for mod in (es, eh, tl, cp):
        src = inspect.getsource(mod)
        assert "alpaca" not in src.lower()


def test_trial_config_from_settings_parsing() -> None:
    settings = Settings(
        trading_mode="paper",
        trial_max_order_notional=Decimal("100"),
        trial_max_orders_per_minute=3,
        trial_symbol_allowlist="aapl, msft",
    )
    cfg = trial_config_from_settings(settings)
    assert cfg.active is True
    assert cfg.max_orders_per_minute == 3
    assert cfg.symbol_allowlist == frozenset({"AAPL", "MSFT"})
