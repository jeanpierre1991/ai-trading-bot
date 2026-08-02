"""M14.4 controlled trial protocol closure — sandbox dry-run + stack integration."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from alerts.notifier import Alert, AlertLevel, AlertNotifier, FanoutNotifier
from alerts.webhook import WebhookNotifier
from broker_interface.adapter_registry import ENDPOINT_LIVE_PRODUCTION, is_approved_adapter
from broker_interface.broker import Broker, BrokerStatus
from broker_interface.cancel_port import CancelAllResult
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
from runtime.emergency_stop import FileEnvEmergencyTrigger, TriggerSource
from runtime.evidence_checklist import REQUIRED_EVIDENCE_ITEM_IDS, validate_evidence_checklist
from runtime.factory import create_trading_runtime
from runtime.live_enablement import (
    EXPECTED_LIVE_CONFIRM_TOKEN,
    LiveExecutionContext,
    evaluate_live_enablement,
)
from runtime.protocol_dry_run import run_sandbox_emergency_protocol
from runtime.trial_enablement import (
    EXPECTED_LIVE_TRIAL_CONFIRM_TOKEN,
    evaluate_trial_enablement,
)
from runtime.trial_limits import TrialLimitConfig, TrialLimitGuardBroker


class RecordingNotifier(AlertNotifier):
    def __init__(self) -> None:
        self.sent: list[Alert] = []

    def send(self, alert: Alert) -> bool:
        self.sent.append(alert)
        return True


class RecordingHttpTransport:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        body: bytes | None = None,
        timeout: float = 10.0,
    ) -> Any:
        self.calls.append({"method": method, "url": url, "body": body})

        class _Resp:
            status_code = 200

        return _Resp()


class ProtocolBroker(Broker):
    def __init__(self) -> None:
        self.place_calls: list[BrokerOrderRequest] = []
        self.cancel_calls = 0
        self.cancel_result = CancelAllResult(
            attempted=True,
            canceled_order_ids=("ord-1",),
            partial=False,
        )

    def connect(self) -> bool:
        return True

    def disconnect(self) -> None:
        return None

    def get_status(self) -> BrokerStatus:
        return BrokerStatus(
            connected=True,
            broker_name="protocol-fake",
            account_id="acct-proto",
            buying_power=Decimal("25000"),
            checked_at=datetime.now(timezone.utc),
        )

    def get_quote(self, symbol: Symbol) -> Decimal:
        return Decimal("100")

    def place_order(self, request: BrokerOrderRequest) -> ExecutionResult:
        self.place_calls.append(request)
        return ExecutionResult(
            order_id=OrderId("should-not-submit"),
            symbol=request.symbol,
            side=request.side,
            requested_quantity=request.quantity,
            filled_quantity=Decimal("0"),
            fill_price=Decimal("0"),
            fee=Decimal("0"),
            status=ExecutionStatus.REJECTED,
            message="should be blocked by halt",
        )

    def cancel_all_open_orders(self) -> CancelAllResult:
        self.cancel_calls += 1
        return self.cancel_result

    def list_open_orders(self) -> list[BrokerOrderSnapshot]:
        return [
            BrokerOrderSnapshot(
                broker_order_id="ord-1",
                client_order_id="c1",
                symbol=Symbol("AAPL"),
                side=Side.BUY,
                quantity=Decimal("1"),
                filled_quantity=Decimal("0"),
                status=BrokerOrderStatus.OPEN,
            )
        ]

    def list_positions(self) -> list[BrokerPositionSnapshot]:
        return [
            BrokerPositionSnapshot(
                symbol=Symbol("AAPL"),
                quantity=Decimal("2"),
                avg_entry_price=Decimal("10"),
            )
        ]


def _write_complete_checklist(tmp_path: Path) -> Path:
    for item_id in REQUIRED_EVIDENCE_ITEM_IDS:
        (tmp_path / f"{item_id}.txt").write_text(f"{item_id}\n", encoding="utf-8")
    payload = {
        "schema_version": 1,
        "checklist_id": "m14-4-protocol",
        "signed_off_by": "protocol-tester",
        "signed_off_at": "2026-08-01T20:00:00+00:00",
        "sign_off_confirmed": True,
        "items": [
            {
                "id": item_id,
                "description": item_id,
                "evidence_path": f"{item_id}.txt",
                "complete": True,
            }
            for item_id in REQUIRED_EVIDENCE_ITEM_IDS
        ],
    }
    path = tmp_path / "checklist.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_sandbox_protocol_dry_run_kill_cancel_halt_alert(tmp_path: Path) -> None:
    broker = ProtocolBroker()
    http = RecordingHttpTransport()
    notifier = FanoutNotifier(
        [
            RecordingNotifier(),
            WebhookNotifier(
                url="https://hooks.example/m14-4",
                transport=http,
            ),
        ]
    )
    kill = tmp_path / "KILL"
    kill.write_text("1", encoding="utf-8")

    outcome = run_sandbox_emergency_protocol(
        latch_path=tmp_path / "halt.json",
        incident_dir=tmp_path / "incidents",
        broker=broker,
        notifier=notifier,
        triggers=[FileEnvEmergencyTrigger(kill, env_var="LIVE_EMERGENCY_KILL_UNUSED")],
        reason="m14_4_protocol",
        trigger_source=TriggerSource.FILE,
    )

    assert outcome.success is True
    assert outcome.activated is True
    assert outcome.halt_engaged is True
    assert outcome.cancel_attempted is True
    assert outcome.cancel_partial is False
    assert outcome.alert_sent is True
    assert outcome.place_order_blocked is True
    assert outcome.incident_id
    assert broker.cancel_calls == 1
    assert broker.place_calls == []
    assert len(http.calls) == 1
    assert outcome.snapshot_path is not None
    snap = json.loads(Path(outcome.snapshot_path).read_text(encoding="utf-8"))
    assert snap["incident_id"] == outcome.incident_id
    assert snap["reason"] == "m14_4_protocol"


def test_protocol_partial_cancel_still_succeeds_halt(tmp_path: Path) -> None:
    broker = ProtocolBroker()
    broker.cancel_result = CancelAllResult(
        attempted=True,
        canceled_order_ids=("a",),
        failed_order_ids=("b",),
        partial=True,
        message="partial",
    )
    notifier = RecordingNotifier()
    outcome = run_sandbox_emergency_protocol(
        latch_path=tmp_path / "halt.json",
        incident_dir=tmp_path / "incidents",
        broker=broker,
        notifier=notifier,
    )
    assert outcome.halt_engaged is True
    assert outcome.cancel_partial is True
    assert outcome.place_order_blocked is True
    assert outcome.success is True
    assert any("partial" in n for n in outcome.notes)


def test_m14_stack_trial_limits_and_checklist_and_production_deny(tmp_path: Path) -> None:
    # Trial limits: reject before place_order
    broker = ProtocolBroker()
    guarded = TrialLimitGuardBroker(
        broker,
        config=TrialLimitConfig(max_order_notional=Decimal("10")),
    )
    rejected = guarded.place_order(
        BrokerOrderRequest(
            symbol=Symbol("AAPL"),
            side=Side.BUY,
            order_type=OrderType.MARKET,
            quantity=Decimal("1"),
        )
    )
    assert rejected.status is ExecutionStatus.REJECTED
    assert "TRIAL" in rejected.message
    assert broker.place_calls == []

    # Checklist validates when complete
    checklist = _write_complete_checklist(tmp_path)
    assert validate_evidence_checklist(checklist, base_dir=tmp_path).ok is True

    # Production still denied by default even with complete checklist + trial token
    settings = Settings(
        trading_mode="live",
        live_trading_enabled=True,
        live_confirm_token=EXPECTED_LIVE_CONFIRM_TOKEN,
        live_trial_confirm_token=EXPECTED_LIVE_TRIAL_CONFIRM_TOKEN,
        broker_endpoint_class="live_production",
        broker_name="alpaca_paper",
        broker_api_key="k",
        broker_api_secret="s",
        broker_base_url="https://api.example.com",
        live_max_order_notional=Decimal("100"),
        live_max_orders_per_day=2,
        trial_max_order_notional=Decimal("50"),
        trial_max_orders_per_day=1,
        trial_evidence_checklist_path=checklist,
        live_production_trial_wiring_enabled=False,
    )
    auth = evaluate_trial_enablement(
        settings,
        LiveExecutionContext(
            command="run-once",
            execution="live",
            context_mode=TradingMode.LIVE,
        ),
    )
    assert auth.authorized is False
    assert "T8" in (auth.reason or "") or "T9" in (auth.reason or "")
    assert not is_approved_adapter("alpaca_paper", ENDPOINT_LIVE_PRODUCTION)

    live_auth = evaluate_live_enablement(
        settings,
        LiveExecutionContext(
            command="run-once",
            execution="live",
            context_mode=TradingMode.LIVE,
        ),
    )
    assert live_auth.authorized is False
    assert "G9" in (live_auth.reason or "")


def test_wiring_flag_alone_cannot_authorize_without_adapter(tmp_path: Path) -> None:
    checklist = _write_complete_checklist(tmp_path)
    settings = Settings(
        trading_mode="live",
        live_trading_enabled=True,
        live_confirm_token=EXPECTED_LIVE_CONFIRM_TOKEN,
        live_trial_confirm_token=EXPECTED_LIVE_TRIAL_CONFIRM_TOKEN,
        broker_endpoint_class="live_production",
        broker_name="alpaca_paper",
        broker_api_key="k",
        broker_api_secret="s",
        broker_base_url="https://api.example.com",
        live_max_order_notional=Decimal("100"),
        live_max_orders_per_day=2,
        trial_max_order_notional=Decimal("50"),
        trial_max_orders_per_day=1,
        trial_evidence_checklist_path=checklist,
        live_production_trial_wiring_enabled=True,
    )
    auth = evaluate_trial_enablement(
        settings,
        LiveExecutionContext(
            command="run-once",
            execution="live",
            context_mode=TradingMode.LIVE,
        ),
    )
    assert auth.authorized is False
    assert "T8" in (auth.reason or "")


def test_factory_sandbox_still_authorizes_without_production_wiring(tmp_path: Path) -> None:
    from unittest.mock import MagicMock

    from broker_interface.alpaca.adapter import AlpacaBroker
    from broker_interface.http_transport import HttpResponse
    from runtime.emergency_guard import EmergencyHaltGuardBroker
    from runtime.idempotent_submit import IdempotentLiveExecutor
    from runtime.live_caps import unwrap_broker

    class _Transport:
        def request(self, *args: Any, **kwargs: Any) -> HttpResponse:
            method = str(args[0] if args else kwargs.get("method", "GET")).upper()
            url = str(args[1] if len(args) > 1 else kwargs.get("url", ""))
            if method == "GET" and (
                "/v2/positions" in url
                or ("/v2/orders" in url and "by_client_order_id" not in url)
            ):
                return HttpResponse(status_code=200, headers={}, body=b"[]")
            if method == "GET" and "/v2/account" in url:
                return HttpResponse(
                    status_code=200,
                    headers={},
                    body=(
                        b'{"id":"acct","account_number":"1","status":"ACTIVE",'
                        b'"buying_power":"100000","cash":"100000"}'
                    ),
                )
            return HttpResponse(
                status_code=200,
                headers={},
                body=(
                    b'{"id":"ord-1","status":"accepted","filled_qty":"0",'
                    b'"filled_avg_price":"0","symbol":"AAPL","side":"buy"}'
                ),
            )

    settings = Settings(
        trading_mode="live",
        live_trading_enabled=True,
        live_confirm_token=EXPECTED_LIVE_CONFIRM_TOKEN,
        broker_name="alpaca_paper",
        broker_api_key="PK_TEST",
        broker_api_secret="SECRET",
        broker_base_url="https://paper-api.alpaca.markets",
        broker_endpoint_class="broker_sandbox",
        live_max_order_notional=Decimal("1000"),
        live_max_orders_per_day=5,
        live_order_ledger_path=tmp_path / "ledger.json",
        market_data_freshness_enabled=False,
        market_hours_enabled=False,
        live_production_trial_wiring_enabled=False,
    )
    runtime = create_trading_runtime(
        settings,
        execution="live",
        live_command="run-once",
        http_transport=_Transport(),
        market_data=MagicMock(),
        strategy_engine=MagicMock(),
        portfolio=MagicMock(),
    )
    assert isinstance(runtime.executor, IdempotentLiveExecutor)
    assert isinstance(runtime.executor.broker, EmergencyHaltGuardBroker)
    assert isinstance(unwrap_broker(runtime.executor.broker), AlpacaBroker)


def test_factory_production_still_denied_by_default(tmp_path: Path) -> None:
    checklist = _write_complete_checklist(tmp_path)
    with pytest.raises(ConfigurationError, match="G9|T8|T9|LIVE_PRODUCTION|M14"):
        create_trading_runtime(
            Settings(
                trading_mode="live",
                live_trading_enabled=True,
                live_confirm_token=EXPECTED_LIVE_CONFIRM_TOKEN,
                live_trial_confirm_token=EXPECTED_LIVE_TRIAL_CONFIRM_TOKEN,
                broker_endpoint_class="live_production",
                broker_name="alpaca_paper",
                broker_api_key="k",
                broker_api_secret="s",
                broker_base_url="https://api.example.com",
                live_max_order_notional=Decimal("100"),
                live_max_orders_per_day=2,
                trial_max_order_notional=Decimal("50"),
                trial_max_orders_per_day=1,
                trial_evidence_checklist_path=checklist,
                live_order_ledger_path=tmp_path / "ledger.json",
                live_production_trial_wiring_enabled=False,
            ),
            execution="live",
            live_command="run-once",
            market_data=object(),
            strategy_engine=object(),
            portfolio=object(),
        )
