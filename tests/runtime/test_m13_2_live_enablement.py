"""M13.2 hard LIVE enablement gates — Authority, factory, caps, CLI surfaces."""

from __future__ import annotations

import argparse
from decimal import Decimal
from typing import Any
from unittest.mock import MagicMock

import pytest

from broker_interface.adapter_registry import (
    ENDPOINT_BROKER_SANDBOX,
    ENDPOINT_LIVE_PRODUCTION,
    is_approved_adapter,
)
from broker_interface.alpaca.adapter import AlpacaBroker, AlpacaBrokerConfig
from broker_interface.broker import Broker, PaperBroker
from broker_interface.execution import ExecutionResult, ExecutionStatus
from broker_interface.http_transport import HttpResponse
from broker_interface.orders import BrokerOrderRequest
from config.settings import Settings
from core.exceptions import ConfigurationError
from core.types import OrderId, OrderType, Side, SignalAction, Symbol, TradingMode
from portfolio_manager.portfolio import Portfolio
from risk_manager.basic import BasicRiskManager
from runtime.broker_executor import BrokerOrderExecutor
from runtime.context import RuntimeContext
from runtime.dry_run import DryRunExecutor
from runtime.factory import create_trading_runtime
from runtime.live_caps import LiveCapGuardBroker, LiveOrderCounter
from runtime.live_enablement import (
    EXPECTED_LIVE_CONFIRM_TOKEN,
    LiveExecutionContext,
    evaluate_live_enablement,
)
from runtime.mode_policy import mode_policy_violation
from runtime.models import TradeIntent
from runtime.kill_switch import FileEnvKillSwitch
from runtime.paper_operator import PaperOperator, PaperOperatorConfig
from runtime.trading_runtime import BasicTradingRuntime
from strategy_engine.signal import StrategySignal
import main as main_module


SECRET = "SUPER_SECRET_VALUE_DO_NOT_LEAK"
KEY_ID = "PKTEST_KEY_ID_ONLY"


def _live_settings(**overrides: Any) -> Settings:
    base: dict[str, Any] = {
        "trading_mode": "live",
        "live_trading_enabled": True,
        "live_confirm_token": EXPECTED_LIVE_CONFIRM_TOKEN,
        "broker_name": "alpaca_paper",
        "broker_api_key": KEY_ID,
        "broker_api_secret": SECRET,
        "broker_base_url": "https://paper-api.alpaca.markets",
        "broker_endpoint_class": "broker_sandbox",
        "live_max_order_notional": Decimal("1000"),
        "live_max_orders_per_day": 5,
        "market_data_provider": "mock",
        "market_data_freshness_enabled": False,
        "market_hours_enabled": False,
    }
    base.update(overrides)
    return Settings(**base)


def _live_ctx(
    *,
    command: str = "run-once",
    execution: str = "live",
    context_mode: object = TradingMode.LIVE,
) -> LiveExecutionContext:
    return LiveExecutionContext(
        command=command,
        execution=execution,
        context_mode=context_mode,
    )


class _RecordingTransport:
    def __init__(self, response: HttpResponse | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self._response = response or HttpResponse(
            status_code=200,
            headers={},
            body=(
                b'{"id":"ord-1","status":"filled","filled_qty":"1",'
                b'"filled_avg_price":"100","symbol":"AAPL","side":"buy"}'
            ),
        )

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        json_body: dict[str, Any] | None = None,
        timeout: float = 30.0,
    ) -> HttpResponse:
        self.calls.append(
            {
                "method": method,
                "url": url,
                "headers": dict(headers or {}),
                "json_body": json_body,
            }
        )
        return self._response


class _FakeQuoteBroker(Broker):
    def __init__(self, quote: Decimal = Decimal("100")) -> None:
        self._quote = quote
        self.place_calls = 0

    def connect(self) -> bool:
        return True

    def disconnect(self) -> None:
        return None

    def get_status(self):  # type: ignore[no-untyped-def]
        raise NotImplementedError

    def get_quote(self, symbol: Symbol) -> Decimal:
        return self._quote

    def place_order(self, request: BrokerOrderRequest) -> ExecutionResult:
        self.place_calls += 1
        return ExecutionResult(
            order_id=OrderId("ok"),
            symbol=request.symbol,
            side=request.side,
            requested_quantity=request.quantity,
            filled_quantity=request.quantity,
            fill_price=self._quote,
            fee=Decimal("0"),
            status=ExecutionStatus.FILLED,
            message="filled",
        )


def _explicit_deps(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "market_data": MagicMock(),
        "strategy_engine": MagicMock(),
        "portfolio": Portfolio(cash=Decimal("100000")),
    }
    base["market_data"].get_bars.return_value = [object()]
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Authority unit gates
# ---------------------------------------------------------------------------


def test_all_gates_pass_broker_sandbox_authorized() -> None:
    auth = evaluate_live_enablement(_live_settings(), _live_ctx())
    assert auth.authorized is True
    assert auth.reason is None
    assert auth.endpoint_class == ENDPOINT_BROKER_SANDBOX
    assert auth.adapter_id == "alpaca_paper"


@pytest.mark.parametrize(
    ("overrides", "needle"),
    [
        ({"trading_mode": "paper"}, "G1"),
        ({"live_trading_enabled": False}, "G2"),
        ({"live_confirm_token": ""}, "G3"),
        ({"live_confirm_token": "I_UNDERSTAND"}, "G3"),
        ({"live_confirm_token": EXPECTED_LIVE_CONFIRM_TOKEN + "_X"}, "G3"),
        ({"broker_name": "unknown_broker"}, "G4"),
        ({"broker_name": "paper"}, "G4"),
        ({"broker_api_key": ""}, "G5"),
        ({"broker_api_secret": ""}, "G5"),
        ({"broker_base_url": ""}, "G5"),
        ({"broker_endpoint_class": "local_paper"}, "G9"),
        ({"live_max_order_notional": None}, "G10"),
        ({"live_max_order_notional": Decimal("0")}, "G10"),
        ({"live_max_order_notional": Decimal("-1")}, "G10"),
        ({"live_max_orders_per_day": None}, "G10"),
        ({"live_max_orders_per_day": 0}, "G10"),
        ({"live_max_orders_per_day": -3}, "G10"),
    ],
)
def test_each_settings_gate_failure_denies(overrides: dict[str, Any], needle: str) -> None:
    auth = evaluate_live_enablement(_live_settings(**overrides), _live_ctx())
    assert auth.authorized is False
    assert auth.reason is not None
    assert needle in auth.reason
    assert SECRET not in (auth.reason or "")
    assert KEY_ID not in (auth.reason or "")


def test_g3_substring_and_partial_token_mismatch() -> None:
    partial = EXPECTED_LIVE_CONFIRM_TOKEN[:10]
    auth = evaluate_live_enablement(
        _live_settings(live_confirm_token=partial),
        _live_ctx(),
    )
    assert auth.authorized is False
    assert "G3" in (auth.reason or "")
    assert partial not in (auth.reason or "")
    assert EXPECTED_LIVE_CONFIRM_TOKEN not in (auth.reason or "")


def test_g3_case_mismatch_denies_without_changing_semantics() -> None:
    """Exact-match only: case-folded token must not authorize (audit follow-up)."""
    auth = evaluate_live_enablement(
        _live_settings(live_confirm_token=EXPECTED_LIVE_CONFIRM_TOKEN.lower()),
        _live_ctx(),
    )
    assert auth.authorized is False
    assert "G3" in (auth.reason or "")
    assert EXPECTED_LIVE_CONFIRM_TOKEN.lower() not in (auth.reason or "")
    assert EXPECTED_LIVE_CONFIRM_TOKEN not in (auth.reason or "")


def test_g9_live_production_denied_even_when_other_gates_pass() -> None:
    auth = evaluate_live_enablement(
        _live_settings(broker_endpoint_class="live_production"),
        _live_ctx(),
    )
    assert auth.authorized is False
    assert "G9" in (auth.reason or "")
    assert "M14" in (auth.reason or "")
    assert not is_approved_adapter("alpaca_paper", ENDPOINT_LIVE_PRODUCTION)


def test_g6_g7_g8_context_failures() -> None:
    cases = [
        (_live_ctx(execution="paper"), "G6"),
        (_live_ctx(command="run-backtest"), "G7"),
        (_live_ctx(command="run-paper-operator"), "G8"),
        (_live_ctx(context_mode=TradingMode.PAPER), "G6"),
        (_live_ctx(context_mode=TradingMode.BACKTEST), "G7"),
        (_live_ctx(command="unknown"), "G6"),
    ]
    for ctx, needle in cases:
        auth = evaluate_live_enablement(_live_settings(), ctx)
        assert auth.authorized is False, ctx
        assert needle in (auth.reason or ""), (ctx, auth.reason)


# ---------------------------------------------------------------------------
# Factory wiring
# ---------------------------------------------------------------------------


def test_factory_live_sandbox_wires_cap_guarded_alpaca() -> None:
    transport = _RecordingTransport()
    runtime = create_trading_runtime(
        _live_settings(),
        execution="live",
        live_command="run-once",
        http_transport=transport,
        **_explicit_deps(),
    )
    assert isinstance(runtime.executor, BrokerOrderExecutor)
    assert isinstance(runtime.executor.broker, LiveCapGuardBroker)
    assert isinstance(runtime.executor.broker.inner, AlpacaBroker)


def test_factory_live_production_unreachable() -> None:
    with pytest.raises(ConfigurationError, match="G9|LIVE_PRODUCTION|M14"):
        create_trading_runtime(
            _live_settings(broker_endpoint_class="live_production"),
            execution="live",
            live_command="run-once",
            **_explicit_deps(),
        )


def test_factory_live_plus_paper_mode_denies() -> None:
    with pytest.raises(ConfigurationError, match="G1"):
        create_trading_runtime(
            _live_settings(trading_mode="paper"),
            execution="live",
            live_command="run-once",
            **_explicit_deps(),
        )


def test_factory_unknown_broker_no_paper_fallback() -> None:
    with pytest.raises(ConfigurationError, match="G4|approved"):
        create_trading_runtime(
            _live_settings(broker_name="not_a_real_adapter"),
            execution="live",
            live_command="run-once",
            **_explicit_deps(),
        )


def test_factory_rejects_paperbroker_injection_on_live() -> None:
    with pytest.raises(ConfigurationError, match="injected broker|registry"):
        create_trading_runtime(
            _live_settings(),
            execution="live",
            live_command="run-once",
            broker=PaperBroker(),
            **_explicit_deps(),
        )


def test_factory_rejects_arbitrary_injected_broker_on_live() -> None:
    """MAJOR remediation: injected non-PaperBroker cannot bypass registry."""
    with pytest.raises(ConfigurationError, match="injected broker|registry"):
        create_trading_runtime(
            _live_settings(),
            execution="live",
            live_command="run-once",
            broker=_FakeQuoteBroker(),
            **_explicit_deps(),
        )


def test_execution_paper_unchanged_when_live_env_present() -> None:
    settings = _live_settings(trading_mode="paper")
    runtime = create_trading_runtime(
        settings,
        execution="paper",
        **_explicit_deps(),
    )
    assert isinstance(runtime.executor, BrokerOrderExecutor)
    assert isinstance(runtime.executor.broker, PaperBroker)


def test_execution_paper_still_accepts_explicit_paperbroker() -> None:
    paper = PaperBroker(name="explicit-paper", buying_power=Decimal("50000"))
    paper.connect()
    runtime = create_trading_runtime(
        Settings(trading_mode="paper", market_data_provider="mock"),
        execution="paper",
        broker=paper,
        **_explicit_deps(),
    )
    assert isinstance(runtime.executor, BrokerOrderExecutor)
    assert runtime.executor.broker is paper


def test_factory_live_requires_command() -> None:
    with pytest.raises(ConfigurationError, match="live_command"):
        create_trading_runtime(
            _live_settings(),
            execution="live",
            **_explicit_deps(),
        )


def test_factory_live_rejects_operator_command() -> None:
    with pytest.raises(ConfigurationError, match="run-once|run-session"):
        create_trading_runtime(
            _live_settings(),
            execution="live",
            live_command="run-paper-operator",
            **_explicit_deps(),
        )


# ---------------------------------------------------------------------------
# Caps
# ---------------------------------------------------------------------------


def test_order_above_max_notional_no_place_order() -> None:
    inner = _FakeQuoteBroker(quote=Decimal("100"))
    guard = LiveCapGuardBroker(
        inner,
        max_order_notional=Decimal("50"),
        max_orders_per_day=10,
    )
    result = guard.place_order(
        BrokerOrderRequest(
            symbol=Symbol("AAPL"),
            side=Side.BUY,
            order_type=OrderType.MARKET,
            quantity=Decimal("1"),
        )
    )
    assert result.status is ExecutionStatus.REJECTED
    assert "LIVE_MAX_ORDER_NOTIONAL" in result.message
    assert inner.place_calls == 0


def test_order_count_limit_no_place_order() -> None:
    inner = _FakeQuoteBroker(quote=Decimal("10"))
    counter = LiveOrderCounter()
    guard = LiveCapGuardBroker(
        inner,
        max_order_notional=Decimal("10000"),
        max_orders_per_day=1,
        counter=counter,
    )
    req = BrokerOrderRequest(
        symbol=Symbol("AAPL"),
        side=Side.BUY,
        order_type=OrderType.MARKET,
        quantity=Decimal("1"),
    )
    first = guard.place_order(req)
    second = guard.place_order(req)
    assert first.status is ExecutionStatus.FILLED
    assert second.status is ExecutionStatus.REJECTED
    assert "LIVE_MAX_ORDERS_PER_DAY" in second.message
    assert inner.place_calls == 1


# ---------------------------------------------------------------------------
# mode_policy defense-in-depth
# ---------------------------------------------------------------------------


def test_mode_policy_re_evaluates_authority_for_live() -> None:
    settings = _live_settings(live_trading_enabled=False)
    broker = AlpacaBroker(
        AlpacaBrokerConfig(
            api_key_id=KEY_ID,
            api_secret_key=SECRET,
            base_url="https://paper-api.alpaca.markets",
        ),
        transport=_RecordingTransport(),
    )
    reason = mode_policy_violation(
        settings=settings,
        context_mode=TradingMode.LIVE,
        executor=BrokerOrderExecutor(LiveCapGuardBroker(
            broker,
            max_order_notional=Decimal("1000"),
            max_orders_per_day=5,
        )),
        execution="live",
        command="run-once",
    )
    assert reason is not None
    assert "G2" in reason


def test_mode_policy_rejects_direct_invalid_live_wiring() -> None:
    reason = mode_policy_violation(
        settings=_live_settings(),
        context_mode=TradingMode.LIVE,
        executor=DryRunExecutor(),
        execution="live",
        command="run-once",
    )
    assert reason is not None
    assert "BrokerOrderExecutor" in reason


def test_mode_policy_rejects_non_paper_on_paper_execution() -> None:
    broker = _FakeQuoteBroker()
    reason = mode_policy_violation(
        settings=Settings(trading_mode="paper"),
        context_mode=TradingMode.PAPER,
        executor=BrokerOrderExecutor(broker),
        execution="paper",
    )
    assert reason is not None
    assert "PaperBroker" in reason


# ---------------------------------------------------------------------------
# Operator / backtest / CLI
# ---------------------------------------------------------------------------


def test_paper_operator_refuses_non_paper_settings(tmp_path: Any) -> None:
    runtime = BasicTradingRuntime(
        settings=_live_settings(),
        market_data=MagicMock(),
        strategy_engine=MagicMock(),
        risk_manager=BasicRiskManager(Settings(trading_mode="paper")),
        portfolio=Portfolio(cash=Decimal("100000")),
        executor=DryRunExecutor(),
    )
    op = PaperOperator(
        runtime,
        settings=_live_settings(),
        kill_switch=FileEnvKillSwitch(tmp_path / "kill"),
    )
    with pytest.raises(ConfigurationError, match="paper"):
        op.run(
            PaperOperatorConfig(
                symbol="AAPL",
                max_cycles=1,
                max_wall_time_seconds=10,
                interval_seconds=1,
            )
        )


def test_run_paper_operator_parser_has_no_live_flag() -> None:
    args = main_module.parse_args(
        [
            "run-paper-operator",
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
    assert not hasattr(args, "live") or getattr(args, "live", False) is False
    with pytest.raises(SystemExit):
        main_module.parse_args(
            [
                "run-paper-operator",
                "--live",
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


def test_run_once_and_session_accept_live_flag() -> None:
    once = main_module.parse_args(["run-once", "--live", "--symbol", "AAPL"])
    assert once.live is True
    assert main_module._execution_from_args(once) == "live"
    session = main_module.parse_args(
        ["run-session", "--cycles", "1", "--live", "--symbol", "AAPL"]
    )
    assert session.live is True


def test_backtest_cannot_construct_live_executor() -> None:
    """run-backtest rejects non-paper settings before any live factory wiring."""
    settings = _live_settings()
    with pytest.raises(ConfigurationError, match="paper"):
        main_module._run_backtest_command(
            settings,
            argparse.Namespace(
                bars_csv=None,
                synthetic_bars=10,
                symbol="AAPL",
                strategy=None,
                bar_limit=10,
                commission_pct=None,
                timeframe="1h",
                warmup_bars=None,
                max_cycles=None,
            ),
        )


def test_secret_not_in_authorization_denials() -> None:
    auth = evaluate_live_enablement(
        _live_settings(broker_api_secret=SECRET, live_confirm_token="wrong"),
        _live_ctx(),
    )
    text = auth.reason or ""
    assert SECRET not in text
    assert "wrong" not in text
    assert EXPECTED_LIVE_CONFIRM_TOKEN not in text


def test_runtime_live_path_mode_policy_allows_when_authorized() -> None:
    transport = _RecordingTransport()
    settings = _live_settings()
    runtime = create_trading_runtime(
        settings,
        execution="live",
        live_command="run-once",
        http_transport=transport,
        **_explicit_deps(),
    )
    runtime._strategy_engine.evaluate.return_value = StrategySignal(
        symbol="AAPL",
        action=SignalAction.HOLD,
        confidence=0.1,
        strategy_name="ema_crossover",
        price=Decimal("100"),
    )
    result = runtime.run_once(
        RuntimeContext(symbol="AAPL", mode=TradingMode.LIVE, daily_pnl_pct=Decimal("0"))
    )
    assert result.stage_reached != "mode"
    assert result.aborted_reason is None


def test_m13_1_alpaca_production_host_still_rejected() -> None:
    with pytest.raises(ConfigurationError, match="live API host"):
        AlpacaBroker(
            AlpacaBrokerConfig(
                api_key_id=KEY_ID,
                api_secret_key=SECRET,
                base_url="https://api.alpaca.markets",
            ),
            transport=_RecordingTransport(),
        )


def test_trade_intent_notional_via_executor_guard() -> None:
    inner = _FakeQuoteBroker(quote=Decimal("200"))
    guard = LiveCapGuardBroker(
        inner,
        max_order_notional=Decimal("100"),
        max_orders_per_day=5,
    )
    executor = BrokerOrderExecutor(guard)
    result = executor.execute(
        TradeIntent(
            symbol=Symbol("AAPL"),
            side=Side.BUY,
            quantity=Decimal("1"),
            order_type=OrderType.MARKET,
            limit_price=None,
            strategy_name="t",
            signal_confidence=1.0,
            max_position_value=Decimal("10000"),
            reason="test",
        )
    )
    assert result.status is ExecutionStatus.REJECTED
    assert inner.place_calls == 0
