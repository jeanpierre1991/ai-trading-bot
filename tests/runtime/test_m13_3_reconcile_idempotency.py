"""M13.3 abort-only reconcile + idempotent live submit tests."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from broker_interface.broker import Broker, BrokerStatus, PaperBroker
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
from risk_manager.basic import BasicRiskManager
from runtime.broker_executor import BrokerOrderExecutor
from runtime.context import RuntimeContext
from runtime.factory import create_trading_runtime
from runtime.idempotent_submit import IdempotentLiveExecutor
from runtime.live_caps import LiveCapGuardBroker, LiveOrderCounter
from runtime.live_enablement import EXPECTED_LIVE_CONFIRM_TOKEN
from runtime.live_order_ledger import JsonLiveOrderLedger, LiveOrderState
from runtime.models import TradeIntent
from runtime.reconcile import (
    ReconcileMismatchCode,
    ReconcilePolicy,
    reconcile_live,
)
from runtime.trading_runtime import BasicTradingRuntime
from unittest.mock import MagicMock
from datetime import datetime, timezone


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
        "live_max_orders_per_day": 2,
        "live_order_ledger_path": tmp_path / "live_ledger.json",
        "market_data_provider": "mock",
        "market_data_freshness_enabled": False,
        "market_hours_enabled": False,
    }
    base.update(overrides)
    return Settings(**base)


def _intent(**overrides: Any) -> TradeIntent:
    values: dict[str, Any] = {
        "symbol": Symbol("AAPL"),
        "side": Side.BUY,
        "order_type": OrderType.MARKET,
        "quantity": Decimal("1"),
        "limit_price": None,
        "strategy_name": "t",
        "signal_confidence": 1.0,
        "max_position_value": Decimal("10000"),
        "reason": "test",
    }
    values.update(overrides)
    return TradeIntent(**values)


class _FakeReconcileBroker(Broker):
    def __init__(self) -> None:
        self.place_calls: list[BrokerOrderRequest] = []
        self.open_orders: list[BrokerOrderSnapshot] = []
        self.positions: list[BrokerPositionSnapshot] = []
        self.by_client: dict[str, BrokerOrderSnapshot | None] = {}
        self.quote = Decimal("100")
        self.place_mode: str = "filled"
        self.raise_on_reconcile: Exception | None = None

    def connect(self) -> bool:
        return True

    def disconnect(self) -> None:
        return None

    def get_status(self) -> BrokerStatus:
        return BrokerStatus(
            connected=True,
            broker_name="fake",
            account_id="a",
            buying_power=Decimal("100000"),
            checked_at=datetime.now(timezone.utc),
        )

    def get_quote(self, symbol: Symbol) -> Decimal:
        return self.quote

    def place_order(self, request: BrokerOrderRequest) -> ExecutionResult:
        self.place_calls.append(request)
        if self.place_mode == "timeout":
            return ExecutionResult(
                order_id=OrderId("x"),
                symbol=request.symbol,
                side=request.side,
                requested_quantity=request.quantity,
                filled_quantity=Decimal("0"),
                fill_price=Decimal("0"),
                fee=Decimal("0"),
                status=ExecutionStatus.REJECTED,
                message="Alpaca order transport timeout/network failure: TimeoutError",
            )
        if self.place_mode == "reject":
            return ExecutionResult(
                order_id=OrderId("x"),
                symbol=request.symbol,
                side=request.side,
                requested_quantity=request.quantity,
                filled_quantity=Decimal("0"),
                fill_price=Decimal("0"),
                fee=Decimal("0"),
                status=ExecutionStatus.REJECTED,
                message="Alpaca order rejected: no",
            )
        return ExecutionResult(
            order_id=OrderId("brk-1"),
            symbol=request.symbol,
            side=request.side,
            requested_quantity=request.quantity,
            filled_quantity=request.quantity,
            fill_price=self.quote,
            fee=Decimal("0"),
            status=ExecutionStatus.FILLED,
            message="filled",
        )

    def get_order_by_client_id(self, client_order_id: str) -> BrokerOrderSnapshot | None:
        if self.raise_on_reconcile is not None:
            raise self.raise_on_reconcile
        if client_order_id in self.by_client:
            return self.by_client[client_order_id]
        return None

    def get_order_snapshot(self, broker_order_id: str) -> BrokerOrderSnapshot | None:
        return None

    def list_open_orders(self) -> list[BrokerOrderSnapshot]:
        if self.raise_on_reconcile is not None:
            raise self.raise_on_reconcile
        return list(self.open_orders)

    def list_positions(self) -> list[BrokerPositionSnapshot]:
        if self.raise_on_reconcile is not None:
            raise self.raise_on_reconcile
        return list(self.positions)


def _ledger(tmp_path: Path) -> JsonLiveOrderLedger:
    ledger = JsonLiveOrderLedger(tmp_path / "ledger.json")
    ledger.ensure_ready()
    return ledger


def test_same_logical_order_not_submitted_twice_while_open(tmp_path: Path) -> None:
    broker = _FakeReconcileBroker()
    ledger = _ledger(tmp_path)
    guarded = LiveCapGuardBroker(
        broker,
        max_order_notional=Decimal("10000"),
        max_orders_per_day=5,
    )
    executor = IdempotentLiveExecutor(guarded, ledger=ledger)
    broker.place_mode = "reject"
    # Force accepted-open style message path
    broker.place_mode = "reject"

    # First submit becomes REJECTED in ledger; second new intent allowed.
    # For I2 open lock: plant an open row.
    row = ledger.create_order(
        symbol=Symbol("AAPL"),
        side=Side.BUY,
        order_type=OrderType.MARKET,
        quantity=Decimal("1"),
    )
    ledger.update_state(row.client_order_id, LiveOrderState.ACCEPTED_OPEN)

    result = executor.execute(_intent())
    assert result.status is ExecutionStatus.REJECTED
    assert "I2" in result.message
    assert broker.place_calls == []


def test_cap_guard_same_id_no_double_count_without_ledger() -> None:
    """M13.2/C1 in-memory baseline (no ledger attached)."""
    broker = _FakeReconcileBroker()
    counter = LiveOrderCounter()
    guarded = LiveCapGuardBroker(
        broker,
        max_order_notional=Decimal("10000"),
        max_orders_per_day=1,
        counter=counter,
    )
    req = BrokerOrderRequest(
        symbol=Symbol("AAPL"),
        side=Side.BUY,
        order_type=OrderType.MARKET,
        quantity=Decimal("1"),
        client_order_id="same-id-1",
    )
    first = guarded.place_order(req)
    second = guarded.place_order(req)
    assert first.status is ExecutionStatus.FILLED
    assert second.status is ExecutionStatus.FILLED
    assert counter.current_count() == 1
    assert len(broker.place_calls) == 2


def test_ambiguous_timeout_marks_unknown_fail_closed(tmp_path: Path) -> None:
    broker = _FakeReconcileBroker()
    broker.place_mode = "timeout"
    ledger = _ledger(tmp_path)
    executor = IdempotentLiveExecutor(
        LiveCapGuardBroker(
            broker,
            max_order_notional=Decimal("10000"),
            max_orders_per_day=5,
        ),
        ledger=ledger,
    )
    result = executor.execute(_intent())
    assert result.status is ExecutionStatus.REJECTED
    assert "UNKNOWN" in result.message
    assert ledger.has_blocking_unknown()
    assert len(broker.place_calls) == 1
    cid = broker.place_calls[0].client_order_id
    # Second attempt blocked by UNKNOWN
    result2 = executor.execute(_intent())
    assert "UNKNOWN" in result2.message
    assert len(broker.place_calls) == 1
    assert cid == broker.place_calls[0].client_order_id


def test_reconcile_exact_match_proceeds(tmp_path: Path) -> None:
    broker = _FakeReconcileBroker()
    ledger = _ledger(tmp_path)
    portfolio = Portfolio(cash=Decimal("100000"))
    decision = reconcile_live(ledger=ledger, broker=broker, portfolio=portfolio)
    assert decision.policy is ReconcilePolicy.PROCEED
    assert decision.code is ReconcileMismatchCode.MATCH


def test_order_mismatch_and_unexpected_open_abort(tmp_path: Path) -> None:
    broker = _FakeReconcileBroker()
    broker.open_orders = [
        BrokerOrderSnapshot(
            broker_order_id="x",
            client_order_id="foreign",
            symbol=Symbol("AAPL"),
            side=Side.BUY,
            quantity=Decimal("1"),
            filled_quantity=Decimal("0"),
            status=BrokerOrderStatus.OPEN,
        )
    ]
    decision = reconcile_live(
        ledger=_ledger(tmp_path),
        broker=broker,
        portfolio=Portfolio(cash=Decimal("100000")),
    )
    assert decision.policy is ReconcilePolicy.ABORT
    assert decision.code is ReconcileMismatchCode.BROKER_OPEN_UNEXPECTED


def test_local_open_missing_at_broker_aborts(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    row = ledger.create_order(
        symbol=Symbol("AAPL"),
        side=Side.BUY,
        order_type=OrderType.MARKET,
        quantity=Decimal("1"),
    )
    ledger.update_state(row.client_order_id, LiveOrderState.SUBMITTED)
    broker = _FakeReconcileBroker()
    decision = reconcile_live(
        ledger=ledger,
        broker=broker,
        portfolio=Portfolio(cash=Decimal("100000")),
    )
    assert decision.policy is ReconcilePolicy.ABORT
    assert decision.code is ReconcileMismatchCode.LOCAL_OPEN_MISSING_AT_BROKER


def test_position_mismatch_aborts(tmp_path: Path) -> None:
    broker = _FakeReconcileBroker()
    broker.positions = [
        BrokerPositionSnapshot(
            symbol=Symbol("AAPL"),
            quantity=Decimal("5"),
            avg_entry_price=Decimal("10"),
        )
    ]
    decision = reconcile_live(
        ledger=_ledger(tmp_path),
        broker=broker,
        portfolio=Portfolio(cash=Decimal("100000")),
    )
    assert decision.policy is ReconcilePolicy.ABORT
    assert decision.code is ReconcileMismatchCode.POSITION_QTY_MISMATCH


def test_broker_filled_local_not_booked_aborts(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    row = ledger.create_order(
        symbol=Symbol("AAPL"),
        side=Side.BUY,
        order_type=OrderType.MARKET,
        quantity=Decimal("1"),
    )
    ledger.update_state(row.client_order_id, LiveOrderState.ACCEPTED_OPEN)
    broker = _FakeReconcileBroker()
    broker.by_client[row.client_order_id] = BrokerOrderSnapshot(
        broker_order_id="b1",
        client_order_id=row.client_order_id,
        symbol=Symbol("AAPL"),
        side=Side.BUY,
        quantity=Decimal("1"),
        filled_quantity=Decimal("1"),
        status=BrokerOrderStatus.FILLED,
        avg_fill_price=Decimal("100"),
    )
    decision = reconcile_live(
        ledger=ledger,
        broker=broker,
        portfolio=Portfolio(cash=Decimal("100000")),
    )
    assert decision.policy is ReconcilePolicy.ABORT
    assert decision.code is ReconcileMismatchCode.BROKER_FILLED_LOCAL_NOT_BOOKED


def test_process_restart_preserves_identity(tmp_path: Path) -> None:
    path = tmp_path / "ledger.json"
    ledger = JsonLiveOrderLedger(path)
    ledger.ensure_ready()
    row = ledger.create_order(
        symbol=Symbol("MSFT"),
        side=Side.BUY,
        order_type=OrderType.MARKET,
        quantity=Decimal("2"),
        client_order_id="stable-uuid-1",
    )
    ledger.update_state(row.client_order_id, LiveOrderState.UNKNOWN)

    reloaded = JsonLiveOrderLedger(path)
    reloaded.load()
    assert reloaded.get("stable-uuid-1") is not None
    assert reloaded.get("stable-uuid-1").state == LiveOrderState.UNKNOWN.value
    assert reloaded.has_blocking_unknown()


def test_malformed_and_unavailable_reconcile_abort(tmp_path: Path) -> None:
    broker = _FakeReconcileBroker()
    broker.raise_on_reconcile = ConfigurationError("bad payload")
    d1 = reconcile_live(
        ledger=_ledger(tmp_path),
        broker=broker,
        portfolio=Portfolio(cash=Decimal("1")),
    )
    assert d1.policy is ReconcilePolicy.ABORT
    assert d1.code is ReconcileMismatchCode.MALFORMED_BROKER_PAYLOAD

    broker2 = _FakeReconcileBroker()
    broker2.raise_on_reconcile = RuntimeError("down")
    ledger_b = JsonLiveOrderLedger(tmp_path / "ledger_b.json")
    ledger_b.ensure_ready()
    d2 = reconcile_live(
        ledger=ledger_b,
        broker=broker2,
        portfolio=Portfolio(cash=Decimal("1")),
    )
    assert d2.policy is ReconcilePolicy.ABORT
    assert d2.code is ReconcileMismatchCode.BROKER_UNAVAILABLE


def test_paper_path_unchanged_without_ledger() -> None:
    settings = Settings(trading_mode="paper", market_data_provider="mock")
    runtime = create_trading_runtime(
        settings,
        execution="paper",
        market_data=MagicMock(),
        strategy_engine=MagicMock(),
        portfolio=Portfolio(cash=Decimal("100000")),
    )
    assert isinstance(runtime.executor, BrokerOrderExecutor)
    assert isinstance(runtime.executor.broker, PaperBroker)


def test_live_requires_ledger_path(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match="LIVE_ORDER_LEDGER_PATH"):
        create_trading_runtime(
            _live_settings(tmp_path, live_order_ledger_path=None),
            execution="live",
            live_command="run-once",
            market_data=MagicMock(),
            strategy_engine=MagicMock(),
            portfolio=Portfolio(cash=Decimal("100000")),
        )


def test_live_production_still_unreachable(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match="G9|LIVE_PRODUCTION|M14"):
        create_trading_runtime(
            _live_settings(tmp_path, broker_endpoint_class="live_production"),
            execution="live",
            live_command="run-once",
            market_data=MagicMock(),
            strategy_engine=MagicMock(),
            portfolio=Portfolio(cash=Decimal("100000")),
        )


def test_runtime_reconcile_aborts_before_market_data(tmp_path: Path) -> None:
    broker = _FakeReconcileBroker()
    broker.positions = [
        BrokerPositionSnapshot(
            symbol=Symbol("AAPL"),
            quantity=Decimal("1"),
            avg_entry_price=Decimal("1"),
        )
    ]
    ledger = _ledger(tmp_path)
    executor = IdempotentLiveExecutor(
        LiveCapGuardBroker(
            broker,
            max_order_notional=Decimal("10000"),
            max_orders_per_day=5,
        ),
        ledger=ledger,
    )
    market_data = MagicMock()
    runtime = BasicTradingRuntime(
        settings=_live_settings(tmp_path),
        market_data=market_data,
        strategy_engine=MagicMock(),
        risk_manager=BasicRiskManager(Settings(trading_mode="paper")),
        portfolio=Portfolio(cash=Decimal("100000")),
        executor=executor,
        execution="live",
        command="run-once",
        live_ledger=ledger,
    )
    result = runtime.run_once(
        RuntimeContext(symbol="AAPL", mode=TradingMode.LIVE, daily_pnl_pct=Decimal("0"))
    )
    assert result.success is False
    assert result.stage_reached == "reconcile"
    market_data.get_bars.assert_not_called()


def test_idempotent_submit_sets_uuid_client_order_id(tmp_path: Path) -> None:
    broker = _FakeReconcileBroker()
    ledger = _ledger(tmp_path)
    executor = IdempotentLiveExecutor(
        LiveCapGuardBroker(
            broker,
            max_order_notional=Decimal("10000"),
            max_orders_per_day=5,
            ledger=ledger,
        ),
        ledger=ledger,
    )
    result = executor.execute(_intent())
    assert result.status is ExecutionStatus.FILLED
    assert len(broker.place_calls) == 1
    cid = broker.place_calls[0].client_order_id
    assert cid is not None and len(cid) >= 32
    assert ledger.get(cid) is not None
    assert ledger.get(cid).state == LiveOrderState.FILLED.value


def _executor(
    tmp_path: Path,
    broker: _FakeReconcileBroker,
    ledger: JsonLiveOrderLedger,
    *,
    max_orders_per_day: int = 5,
    counter: LiveOrderCounter | None = None,
) -> IdempotentLiveExecutor:
    return IdempotentLiveExecutor(
        LiveCapGuardBroker(
            broker,
            max_order_notional=Decimal("10000"),
            max_orders_per_day=max_orders_per_day,
            counter=counter if counter is not None else LiveOrderCounter(),
            ledger=ledger,
        ),
        ledger=ledger,
    )


def test_executor_same_id_retry_reuses_client_order_id(tmp_path: Path) -> None:
    """1 + 3: executor-level same-id retry after FAILED_ABSENT."""
    broker = _FakeReconcileBroker()
    broker.place_mode = "timeout"
    ledger = _ledger(tmp_path)
    executor = _executor(tmp_path, broker, ledger)

    first = executor.execute(_intent())
    assert first.status is ExecutionStatus.REJECTED
    assert "UNKNOWN" in first.message
    cid = broker.place_calls[0].client_order_id
    assert ledger.get(cid).state == LiveOrderState.UNKNOWN.value

    # 2: UNKNOWN + confirmed absent → FAILED_ABSENT
    decision = reconcile_live(
        ledger=ledger,
        broker=broker,
        portfolio=Portfolio(cash=Decimal("100000")),
    )
    assert decision.policy is ReconcilePolicy.PROCEED
    assert ledger.get(cid).state == LiveOrderState.FAILED_ABSENT.value

    broker.place_mode = "filled"
    second = executor.execute(_intent())
    assert second.status is ExecutionStatus.FILLED
    assert len(broker.place_calls) == 2
    assert broker.place_calls[1].client_order_id == cid
    assert ledger.get(cid).state == LiveOrderState.FILLED.value


def test_unknown_ambiguous_broker_result_aborts(tmp_path: Path) -> None:
    """4: UNKNOWN + OPEN/ambiguous broker observation ⇒ ABORT, no FAILED_ABSENT."""
    ledger = _ledger(tmp_path)
    row = ledger.create_order(
        symbol=Symbol("AAPL"),
        side=Side.BUY,
        order_type=OrderType.MARKET,
        quantity=Decimal("1"),
        client_order_id="unk-open-1",
    )
    ledger.update_state(row.client_order_id, LiveOrderState.UNKNOWN)
    broker = _FakeReconcileBroker()
    broker.by_client[row.client_order_id] = BrokerOrderSnapshot(
        broker_order_id="b-open",
        client_order_id=row.client_order_id,
        symbol=Symbol("AAPL"),
        side=Side.BUY,
        quantity=Decimal("1"),
        filled_quantity=Decimal("0"),
        status=BrokerOrderStatus.OPEN,
    )
    decision = reconcile_live(
        ledger=ledger,
        broker=broker,
        portfolio=Portfolio(cash=Decimal("100000")),
    )
    assert decision.policy is ReconcilePolicy.ABORT
    assert decision.code is ReconcileMismatchCode.UNKNOWN_LOCAL_ORDER
    assert ledger.get(row.client_order_id).state == LiveOrderState.UNKNOWN.value


def test_c1_same_id_retry_across_restart_no_double_count(tmp_path: Path) -> None:
    """5: durable first_submit_counted prevents double-count after restart."""
    path = tmp_path / "ledger.json"
    ledger = JsonLiveOrderLedger(path)
    ledger.ensure_ready()
    broker = _FakeReconcileBroker()
    counter = LiveOrderCounter()
    executor = _executor(
        tmp_path, broker, ledger, max_orders_per_day=1, counter=counter
    )

    result = executor.execute(_intent())
    assert result.status is ExecutionStatus.FILLED
    cid = broker.place_calls[0].client_order_id
    assert counter.current_count() == 1
    assert ledger.get(cid).first_submit_counted is True
    assert ledger.get(cid).first_submit_day

    # Supervised recovery path for same logical order.
    ledger.update_state(cid, LiveOrderState.FAILED_ABSENT)

    # Process restart: reload ledger, fresh in-memory counter.
    reloaded = JsonLiveOrderLedger(path)
    reloaded.load()
    broker2 = _FakeReconcileBroker()
    counter2 = LiveOrderCounter()
    executor2 = IdempotentLiveExecutor(
        LiveCapGuardBroker(
            broker2,
            max_order_notional=Decimal("10000"),
            max_orders_per_day=1,
            counter=counter2,
            ledger=reloaded,
        ),
        ledger=reloaded,
    )
    assert counter2.current_count() == 1

    retry = executor2.execute(_intent())
    assert retry.status is ExecutionStatus.FILLED
    assert broker2.place_calls[0].client_order_id == cid
    assert counter2.current_count() == 1


def test_c1_new_logical_order_after_restart_consumes_slot(tmp_path: Path) -> None:
    """6: a NEW logical order after restart consumes another daily slot."""
    path = tmp_path / "ledger.json"
    ledger = JsonLiveOrderLedger(path)
    ledger.ensure_ready()
    broker = _FakeReconcileBroker()
    executor = _executor(
        tmp_path, broker, ledger, max_orders_per_day=2, counter=LiveOrderCounter()
    )
    first = executor.execute(_intent(symbol=Symbol("AAPL")))
    assert first.status is ExecutionStatus.FILLED

    reloaded = JsonLiveOrderLedger(path)
    reloaded.load()
    broker2 = _FakeReconcileBroker()
    counter2 = LiveOrderCounter()
    guard2 = LiveCapGuardBroker(
        broker2,
        max_order_notional=Decimal("10000"),
        max_orders_per_day=2,
        counter=counter2,
        ledger=reloaded,
    )
    executor2 = IdempotentLiveExecutor(guard2, ledger=reloaded)
    assert counter2.current_count() == 1

    second = executor2.execute(_intent(symbol=Symbol("MSFT")))
    assert second.status is ExecutionStatus.FILLED
    assert counter2.current_count() == 2
    assert broker2.place_calls[0].client_order_id != broker.place_calls[0].client_order_id


def test_created_crash_broker_absent_recoverable_same_id_retry(tmp_path: Path) -> None:
    """7: CREATED + broker absent → FAILED_ABSENT → same-id retry."""
    ledger = _ledger(tmp_path)
    row = ledger.create_order(
        symbol=Symbol("AAPL"),
        side=Side.BUY,
        order_type=OrderType.MARKET,
        quantity=Decimal("1"),
        client_order_id="created-crash-1",
    )
    assert row.state == LiveOrderState.CREATED.value
    broker = _FakeReconcileBroker()
    decision = reconcile_live(
        ledger=ledger,
        broker=broker,
        portfolio=Portfolio(cash=Decimal("100000")),
    )
    assert decision.policy is ReconcilePolicy.PROCEED
    assert ledger.get(row.client_order_id).state == LiveOrderState.FAILED_ABSENT.value

    executor = _executor(tmp_path, broker, ledger)
    result = executor.execute(_intent())
    assert result.status is ExecutionStatus.FILLED
    assert broker.place_calls[0].client_order_id == "created-crash-1"


def test_submitting_crash_broker_absent_recoverable_same_id_retry(
    tmp_path: Path,
) -> None:
    """8: SUBMITTING + broker absent → FAILED_ABSENT → same-id retry."""
    ledger = _ledger(tmp_path)
    row = ledger.create_order(
        symbol=Symbol("AAPL"),
        side=Side.BUY,
        order_type=OrderType.MARKET,
        quantity=Decimal("1"),
        client_order_id="submitting-crash-1",
    )
    ledger.update_state(row.client_order_id, LiveOrderState.SUBMITTING)
    broker = _FakeReconcileBroker()
    decision = reconcile_live(
        ledger=ledger,
        broker=broker,
        portfolio=Portfolio(cash=Decimal("100000")),
    )
    assert decision.policy is ReconcilePolicy.PROCEED
    assert ledger.get(row.client_order_id).state == LiveOrderState.FAILED_ABSENT.value

    executor = _executor(tmp_path, broker, ledger)
    result = executor.execute(_intent())
    assert result.status is ExecutionStatus.FILLED
    assert broker.place_calls[0].client_order_id == "submitting-crash-1"


def test_created_submitting_broker_unavailable_aborts(tmp_path: Path) -> None:
    """9: CREATED/SUBMITTING + broker unavailable ⇒ ABORT."""
    ledger = _ledger(tmp_path)
    row = ledger.create_order(
        symbol=Symbol("AAPL"),
        side=Side.BUY,
        order_type=OrderType.MARKET,
        quantity=Decimal("1"),
        client_order_id="preack-down-1",
    )
    ledger.update_state(row.client_order_id, LiveOrderState.SUBMITTING)
    broker = _FakeReconcileBroker()
    broker.raise_on_reconcile = RuntimeError("down")
    decision = reconcile_live(
        ledger=ledger,
        broker=broker,
        portfolio=Portfolio(cash=Decimal("100000")),
    )
    assert decision.policy is ReconcilePolicy.ABORT
    assert decision.code is ReconcileMismatchCode.BROKER_UNAVAILABLE
    assert ledger.get(row.client_order_id).state == LiveOrderState.SUBMITTING.value


def test_created_submitting_existing_broker_order_no_duplicate_submit(
    tmp_path: Path,
) -> None:
    """10: CREATED/SUBMITTING + existing broker order ⇒ adopt, no duplicate submit."""
    ledger = _ledger(tmp_path)
    row = ledger.create_order(
        symbol=Symbol("AAPL"),
        side=Side.BUY,
        order_type=OrderType.MARKET,
        quantity=Decimal("1"),
        client_order_id="preack-open-1",
    )
    ledger.update_state(row.client_order_id, LiveOrderState.SUBMITTING)
    broker = _FakeReconcileBroker()
    broker.by_client[row.client_order_id] = BrokerOrderSnapshot(
        broker_order_id="brk-existing",
        client_order_id=row.client_order_id,
        symbol=Symbol("AAPL"),
        side=Side.BUY,
        quantity=Decimal("1"),
        filled_quantity=Decimal("0"),
        status=BrokerOrderStatus.OPEN,
    )
    broker.open_orders = [broker.by_client[row.client_order_id]]
    decision = reconcile_live(
        ledger=ledger,
        broker=broker,
        portfolio=Portfolio(cash=Decimal("100000")),
    )
    assert decision.policy is ReconcilePolicy.PROCEED
    assert ledger.get(row.client_order_id).state == LiveOrderState.ACCEPTED_OPEN.value
    assert ledger.get(row.client_order_id).broker_order_id == "brk-existing"

    executor = _executor(tmp_path, broker, ledger)
    blocked = executor.execute(_intent())
    assert blocked.status is ExecutionStatus.REJECTED
    assert "I2" in blocked.message
    assert broker.place_calls == []
