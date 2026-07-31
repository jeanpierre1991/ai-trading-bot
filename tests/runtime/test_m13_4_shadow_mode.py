"""M13.4 no-submit shadow mode proofs."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from broker_interface.broker import Broker, BrokerStatus, PaperBroker
from broker_interface.execution import ExecutionResult, ExecutionStatus
from broker_interface.orders import BrokerOrderRequest
from config.settings import Settings
from core.exceptions import ConfigurationError
from core.types import OrderId, OrderType, Side, SignalAction, Symbol, TradingMode
from portfolio_manager.portfolio import Portfolio
from risk_manager.basic import BasicRiskManager
from runtime.broker_executor import BrokerOrderExecutor
from runtime.context import RuntimeContext
from runtime.factory import create_trading_runtime
from runtime.idempotent_submit import IdempotentLiveExecutor
from runtime.live_caps import LiveOrderCounter
from runtime.live_enablement import (
    EXPECTED_LIVE_CONFIRM_TOKEN,
    LiveExecutionContext,
    evaluate_live_enablement,
    evaluate_shadow_enablement,
)
from runtime.live_order_ledger import JsonLiveOrderLedger
from runtime.mode_policy import mode_policy_violation
from runtime.models import TradeIntent
from runtime.shadow_executor import ShadowExecutor
from runtime.shadow_record import (
    SHADOW_RECORD_SCHEMA_VERSION,
    ShadowAuditLog,
    shadow_record_from_dict,
)
from runtime.trading_runtime import BasicTradingRuntime
from strategy_engine.signal import StrategySignal
import main as main_module

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
        "shadow_audit_path": tmp_path / "shadow.jsonl",
        "market_data_provider": "mock",
        "market_data_freshness_enabled": False,
        "market_hours_enabled": False,
    }
    base.update(overrides)
    return Settings(**base)


class _Bar:
    def __init__(self, close: Decimal = Decimal("100")) -> None:
        self.close = close
        self.symbol = "AAPL"
        self.timestamp = None


class _HostileBroker(Broker):
    def __init__(self) -> None:
        self.place_calls: list[BrokerOrderRequest] = []

    def connect(self) -> bool:
        return True

    def disconnect(self) -> None:
        return None

    def get_status(self) -> BrokerStatus:
        from datetime import datetime, timezone

        return BrokerStatus(
            connected=True,
            broker_name="hostile",
            account_id="x",
            buying_power=Decimal("1"),
            checked_at=datetime.now(timezone.utc),
        )

    def get_quote(self, symbol: Symbol) -> Decimal:
        return Decimal("100")

    def place_order(self, request: BrokerOrderRequest) -> ExecutionResult:
        self.place_calls.append(request)
        raise AssertionError("shadow must never call place_order")


class _Quote:
    def get_closed_bar_price(self, symbol: Symbol | str) -> Decimal:
        return Decimal("100")


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


def test_shadow_zero_place_order_calls(tmp_path: Path) -> None:
    """1+2: shadow makes zero production and zero sandbox place_order calls."""
    hostile = _HostileBroker()
    audit = ShadowAuditLog(tmp_path / "s.jsonl")
    counter = LiveOrderCounter()
    executor = ShadowExecutor(
        audit=audit,
        quote_source=_Quote(),
        max_order_notional=Decimal("10000"),
        max_orders_per_day=5,
        broker_adapter_id="alpaca_paper",
        broker_endpoint_class="broker_sandbox",
        command="run-once",
        counter=counter,
    )
    result = executor.execute(_intent())
    assert result.status is ExecutionStatus.REJECTED
    assert "shadow no-submit" in result.message
    assert "would_submit" in result.message
    assert hostile.place_calls == []
    assert counter.current_count() == 0
    # Ensure no broker attribute that could be used for submit.
    assert not hasattr(executor, "broker")


def test_shadow_cannot_authorize_live_production(tmp_path: Path) -> None:
    """3: LIVE_PRODUCTION denied for shadow."""
    settings = _live_settings(tmp_path, broker_endpoint_class="live_production")
    auth = evaluate_shadow_enablement(
        settings,
        LiveExecutionContext(
            command="run-once",
            execution="shadow",
            context_mode=TradingMode.LIVE,
        ),
    )
    assert auth.authorized is False
    assert "G9" in (auth.reason or "") or "PRODUCTION" in (auth.reason or "")
    with pytest.raises(ConfigurationError, match="G9|PRODUCTION|M14"):
        create_trading_runtime(
            settings,
            execution="shadow",
            live_command="run-once",
            market_data=MagicMock(get_bars=MagicMock(return_value=[_Bar()])),
            strategy_engine=MagicMock(),
            portfolio=Portfolio(cash=Decimal("100000")),
        )


def test_paper_path_unchanged_with_shadow_present() -> None:
    """4: paper factory path unchanged."""
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


def test_live_g6_still_rejects_shadow_execution(tmp_path: Path) -> None:
    """5: live enablement G6 unchanged — execution=shadow does not authorize live."""
    settings = _live_settings(tmp_path)
    auth = evaluate_live_enablement(
        settings,
        LiveExecutionContext(
            command="run-once",
            execution="shadow",
            context_mode=TradingMode.LIVE,
        ),
    )
    assert auth.authorized is False
    assert "G6" in (auth.reason or "")


def test_run_paper_operator_cannot_use_shadow() -> None:
    """6: operator has no --shadow flag."""
    with pytest.raises(SystemExit):
        main_module.parse_args(
            [
                "run-paper-operator",
                "--shadow",
                "--max-cycles",
                "1",
                "--max-wall-time-seconds",
                "5",
                "--interval-seconds",
                "1",
                "--state-path",
                "/tmp/x.json",
                "--kill-file",
                "/tmp/kill",
            ]
        )


def test_run_backtest_cannot_use_shadow(tmp_path: Path) -> None:
    """7: backtest remains isolated from shadow."""
    settings = _live_settings(tmp_path)
    with pytest.raises(ConfigurationError, match="paper"):
        main_module._run_backtest_command(
            settings,
            type(
                "Args",
                (),
                {
                    "bars_csv": None,
                    "synthetic_bars": 10,
                    "symbol": "AAPL",
                    "strategy": None,
                    "bar_limit": 10,
                    "commission_pct": None,
                    "timeframe": "1h",
                    "warmup_bars": None,
                    "max_cycles": None,
                },
            )(),
        )


def test_mode_policy_rejects_invalid_shadow_combinations(tmp_path: Path) -> None:
    """8: mode_policy rejects invalid shadow wiring."""
    settings = _live_settings(tmp_path)
    audit = ShadowAuditLog(tmp_path / "a.jsonl")
    shadow = ShadowExecutor(
        audit=audit,
        quote_source=_Quote(),
        max_order_notional=Decimal("10000"),
        max_orders_per_day=2,
        broker_adapter_id="alpaca_paper",
        broker_endpoint_class="broker_sandbox",
        command="run-once",
    )
    assert (
        mode_policy_violation(
            settings=settings,
            context_mode=TradingMode.PAPER,
            executor=shadow,
            execution="shadow",
            command="run-once",
        )
        is not None
    )
    assert (
        mode_policy_violation(
            settings=settings,
            context_mode=TradingMode.LIVE,
            executor=BrokerOrderExecutor(PaperBroker()),
            execution="shadow",
            command="run-once",
        )
        is not None
    )
    ledger = JsonLiveOrderLedger(tmp_path / "ledger.json")
    ledger.ensure_ready()
    # Fake idempotent-looking wiring must be rejected.
    assert (
        mode_policy_violation(
            settings=settings,
            context_mode=TradingMode.LIVE,
            executor=IdempotentLiveExecutor(
                _HostileBroker(),
                ledger=ledger,
            ),
            execution="shadow",
            command="run-once",
        )
        is not None
    )
    ok = mode_policy_violation(
        settings=settings,
        context_mode=TradingMode.LIVE,
        executor=shadow,
        execution="shadow",
        command="run-once",
    )
    assert ok is None


def test_risk_rejection_recorded_zero_submit(tmp_path: Path) -> None:
    """9: risk rejection writes artifact; zero submit."""
    settings = _live_settings(
        tmp_path,
        max_position_size_pct=Decimal("0.0000001"),
        max_open_positions=0,
    )
    market_data = MagicMock()
    market_data.get_bars.return_value = [_Bar()]
    strategy = MagicMock()
    strategy.evaluate.return_value = StrategySignal(
        symbol="AAPL",
        action=SignalAction.BUY,
        confidence=0.9,
        strategy_name="ema_crossover",
        price=Decimal("100"),
    )
    runtime = create_trading_runtime(
        settings,
        execution="shadow",
        live_command="run-once",
        market_data=market_data,
        strategy_engine=strategy,
        portfolio=Portfolio(cash=Decimal("100000")),
        with_order_manager=False,
        with_alerts=False,
    )
    assert isinstance(runtime.executor, ShadowExecutor)
    count_before = runtime.executor.counter.current_count()
    result = runtime.run_once(
        RuntimeContext(symbol="AAPL", mode=TradingMode.LIVE, daily_pnl_pct=Decimal("0"))
    )
    assert result.success is False
    assert result.stage_reached == "risk"
    assert runtime.executor.counter.current_count() == count_before
    lines = (tmp_path / "shadow.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    row = shadow_record_from_dict(json.loads(lines[0]))
    assert row.hypothetical_execution_decision == "blocked_risk"
    assert row.risk_decision == "reject"
    assert row.submitted_price is None
    assert row.fill_price is None
    assert SECRET not in lines[0]


def test_cap_breach_recorded_without_consuming(tmp_path: Path) -> None:
    """10: hypothetical cap breach does not consume daily cap."""
    audit = ShadowAuditLog(tmp_path / "cap.jsonl")
    counter = LiveOrderCounter()
    executor = ShadowExecutor(
        audit=audit,
        quote_source=_Quote(),
        max_order_notional=Decimal("10"),  # qty 1 * 100 = 100 > 10
        max_orders_per_day=1,
        broker_adapter_id="future_broker",
        broker_endpoint_class="broker_sandbox",
        command="run-once",
        counter=counter,
    )
    result = executor.execute(_intent(quantity=Decimal("1")))
    assert result.status is ExecutionStatus.REJECTED
    assert "blocked_cap" in result.message or "cap breach" in result.message
    assert counter.current_count() == 0
    row = shadow_record_from_dict(
        json.loads((tmp_path / "cap.jsonl").read_text(encoding="utf-8").strip())
    )
    assert row.hypothetical_execution_decision == "blocked_cap"
    assert row.cap_evaluation.would_consume_slot is False
    assert row.cap_evaluation.order_notional_ok is False


def test_shadow_jsonl_schema_stable(tmp_path: Path) -> None:
    """11: schema_version and required fields are auditable."""
    audit = ShadowAuditLog(tmp_path / "schema.jsonl")
    executor = ShadowExecutor(
        audit=audit,
        quote_source=_Quote(),
        max_order_notional=Decimal("10000"),
        max_orders_per_day=5,
        broker_adapter_id="alpaca_paper",
        broker_endpoint_class="broker_sandbox",
        command="run-session",
    )
    executor.execute(_intent())
    raw = json.loads((tmp_path / "schema.jsonl").read_text(encoding="utf-8").strip())
    assert raw["schema_version"] == SHADOW_RECORD_SCHEMA_VERSION
    for key in (
        "execution_mode",
        "broker_adapter_id",
        "symbol",
        "risk_decision",
        "hypothetical_execution_decision",
        "cap_evaluation",
        "submitted_price",
        "fill_price",
        "arrival_price",
        "signal_price",
        "latency_ms",
    ):
        assert key in raw
    assert raw["execution_mode"] == "shadow"
    assert raw["submitted_price"] is None
    assert raw["fill_price"] is None
    shadow_record_from_dict(raw)


def test_shadow_core_broker_agnostic() -> None:
    """12: shadow core modules have no Alpaca imports."""
    import runtime.shadow_executor as se
    import runtime.shadow_record as sr
    import runtime.shadow_compare as sc
    import inspect

    for mod in (se, sr, sc):
        src = inspect.getsource(mod)
        assert "alpaca" not in src.lower()
        assert "Alpaca" not in src


def test_shadow_requires_audit_path(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match="SHADOW_AUDIT_PATH"):
        create_trading_runtime(
            _live_settings(tmp_path, shadow_audit_path=None),
            execution="shadow",
            live_command="run-once",
            market_data=MagicMock(),
            strategy_engine=MagicMock(),
            portfolio=Portfolio(cash=Decimal("100000")),
        )


def test_cli_shadow_mutex_and_mapping() -> None:
    once = main_module.parse_args(["run-once", "--shadow", "--symbol", "AAPL"])
    assert once.shadow is True
    assert main_module._execution_from_args(once) == "shadow"
    assert main_module._context_mode_for_execution("shadow") is TradingMode.LIVE
    with pytest.raises(SystemExit):
        main_module.parse_args(["run-once", "--shadow", "--live"])


def test_shadow_factory_rejects_broker_injection(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match="broker"):
        create_trading_runtime(
            _live_settings(tmp_path),
            execution="shadow",
            live_command="run-once",
            broker=_HostileBroker(),
            market_data=MagicMock(get_bars=MagicMock(return_value=[_Bar()])),
            strategy_engine=MagicMock(),
            portfolio=Portfolio(cash=Decimal("100000")),
        )


def test_shadow_would_submit_path_writes_audit(tmp_path: Path) -> None:
    settings = _live_settings(
        tmp_path,
        live_max_order_notional=Decimal("1000000"),
        live_max_orders_per_day=50,
    )
    market_data = MagicMock()
    market_data.get_bars.return_value = [_Bar()]
    strategy = MagicMock()
    strategy.evaluate.return_value = StrategySignal(
        symbol="AAPL",
        action=SignalAction.BUY,
        confidence=0.9,
        strategy_name="ema_crossover",
        price=Decimal("100"),
    )
    runtime = create_trading_runtime(
        settings,
        execution="shadow",
        live_command="run-once",
        market_data=market_data,
        strategy_engine=strategy,
        portfolio=Portfolio(cash=Decimal("100000")),
        risk_manager=BasicRiskManager(
            Settings(trading_mode="paper", max_position_size_pct=Decimal("0.5"))
        ),
        with_order_manager=False,
        with_alerts=False,
    )
    result = runtime.run_once(
        RuntimeContext(symbol="AAPL", mode=TradingMode.LIVE, daily_pnl_pct=Decimal("0"))
    )
    # ShadowExecutor returns REJECTED (no-submit) → existing abort semantics.
    assert result.success is False
    assert result.stage_reached == "execution"
    assert isinstance(runtime.executor, ShadowExecutor)
    assert runtime.executor.counter.current_count() == 0
    row = shadow_record_from_dict(
        json.loads((tmp_path / "shadow.jsonl").read_text(encoding="utf-8").strip())
    )
    assert row.hypothetical_execution_decision == "would_submit"
