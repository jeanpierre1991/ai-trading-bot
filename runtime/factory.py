"""Composition root for BasicTradingRuntime (Milestone 8.6 / 13.2).

Assembles existing components into a usable Runtime. Does not fetch market
data, place orders, or run decision cycles.

M13.2: ``execution='live'`` may wire an approved BROKER_SANDBOX adapter only
when LiveEnablementAuthority authorizes all gates. LIVE_PRODUCTION is denied.
There is no silent PaperBroker fallback for ``execution='live'``.
"""

from __future__ import annotations

from typing import Any, Literal

from alerts.notifier import AlertNotifier, ConsoleNotifier
from broker_interface.adapter_registry import construct_sandbox_broker
from broker_interface.broker import Broker, PaperBroker
from broker_interface.quotes import ClosedBarQuoteSource
from config.settings import Settings
from core.exceptions import ConfigurationError
from core.module_registry import ModuleRegistry
from core.types import TradingMode
from market_data.calendars.us_equity_xnys import build_session_calendar
from order_manager.manager import OrderManager
from risk_manager.base import RiskManager
from risk_manager.basic import BasicRiskManager
from runtime.broker_executor import BrokerOrderExecutor
from runtime.dry_run import DryRunExecutor
from runtime.idempotent_submit import IdempotentLiveExecutor, require_live_ledger_path
from runtime.live_caps import LiveCapGuardBroker, LiveOrderCounter
from runtime.live_enablement import (
    LiveExecutionContext,
    evaluate_live_enablement,
    evaluate_shadow_enablement,
)
from runtime.live_order_ledger import JsonLiveOrderLedger
from runtime.shadow_executor import ShadowExecutor
from runtime.shadow_record import ShadowAuditLog, require_shadow_audit_path
from runtime.trading_runtime import BasicTradingRuntime

ExecutionBackend = Literal["dry_run", "paper", "live", "shadow"]
_ALLOWED_PAPER_SETTINGS_MODES = frozenset({"paper"})
_ALLOWED_EXECUTIONS = frozenset({"dry_run", "paper", "live", "shadow"})
_LIVE_COMMANDS = frozenset({"run-once", "run-session"})
_SHADOW_COMMANDS = frozenset({"run-once", "run-session"})


def create_trading_runtime(
    settings: Settings,
    *,
    execution: ExecutionBackend = "dry_run",
    registry: ModuleRegistry | None = None,
    market_data: Any | None = None,
    strategy_engine: Any | None = None,
    portfolio: Any | None = None,
    risk_manager: RiskManager | None = None,
    broker: Broker | None = None,
    with_order_manager: bool = True,
    order_manager: OrderManager | None = None,
    with_alerts: bool = True,
    alert_notifier: AlertNotifier | None = None,
    live_command: str | None = None,
    http_transport: Any | None = None,
) -> BasicTradingRuntime:
    """Build a ``BasicTradingRuntime`` from settings and wired dependencies.

    Parameters
    ----------
    settings:
        Application settings. ``trading_mode`` must be ``\"paper\"`` for
        dry-run/paper execution. For ``execution=\"live\"`` / ``\"shadow\"``,
        all live/shadow gates must pass and endpoint class must be
        ``broker_sandbox``.
    execution:
        ``\"dry_run\"`` → ``DryRunExecutor``;
        ``\"paper\"`` → ``BrokerOrderExecutor(PaperBroker)``;
        ``\"live\"`` → gated BROKER_SANDBOX adapter (never LIVE_PRODUCTION);
        ``\"shadow\"`` → no-submit ``ShadowExecutor`` (never place_order).
    live_command:
        Required when ``execution=\"live\"`` or ``\"shadow\"``:
        ``run-once`` or ``run-session``.
    http_transport:
        Optional injectable transport for sandbox adapter construction (tests).
        Live brokers themselves cannot be injected via ``broker=``; they must
        come from the approved BROKER_SANDBOX registry path.
        Shadow never constructs a broker client.
    """
    _validate_execution(execution)

    if execution == "live":
        command = _require_live_command(live_command)
        _authorize_live_sandbox(settings, command=command)
    elif execution == "shadow":
        command = _require_shadow_command(live_command)
        _authorize_shadow(settings, command=command)
    else:
        command = live_command or "run-once"
        _validate_paper_settings_mode(settings)

    resolved_market_data = _resolve_market_data(market_data, registry)
    resolved_strategy = _resolve_strategy_engine(strategy_engine, registry)
    resolved_portfolio = _resolve_portfolio(portfolio, registry)
    resolved_risk = (
        risk_manager if risk_manager is not None else BasicRiskManager(settings)
    )
    resolved_order_manager = _resolve_order_manager(
        with_order_manager=with_order_manager,
        order_manager=order_manager,
    )
    resolved_alert_notifier = _resolve_alert_notifier(
        with_alerts=with_alerts,
        alert_notifier=alert_notifier,
    )
    try:
        session_calendar = build_session_calendar(
            settings.market_hours_calendar,
            tz_name=settings.market_hours_timezone,
        )
    except ValueError as exc:
        raise ConfigurationError(str(exc)) from exc

    live_ledger: JsonLiveOrderLedger | None = None
    shadow_audit: ShadowAuditLog | None = None
    if execution == "live":
        ledger_path = require_live_ledger_path(settings.live_order_ledger_path)
        live_ledger = JsonLiveOrderLedger(ledger_path)
        live_ledger.ensure_ready()
    if execution == "shadow":
        shadow_audit = ShadowAuditLog(require_shadow_audit_path(settings.shadow_audit_path))

    resolved_executor = _build_executor(
        execution=execution,
        settings=settings,
        registry=registry,
        broker=broker,
        market_data=resolved_market_data,
        session_calendar=session_calendar,
        http_transport=http_transport,
        live_ledger=live_ledger,
        shadow_audit=shadow_audit,
        command=command,
    )

    return BasicTradingRuntime(
        settings=settings,
        market_data=resolved_market_data,
        strategy_engine=resolved_strategy,
        risk_manager=resolved_risk,
        portfolio=resolved_portfolio,
        executor=resolved_executor,
        order_manager=resolved_order_manager,
        alert_notifier=resolved_alert_notifier,
        session_calendar=session_calendar,
        execution=execution,
        command=(
            command
            if execution in {"live", "shadow"}
            else (live_command or "run-once")
        ),
        live_ledger=live_ledger,
        shadow_audit=shadow_audit,
    )


def create_trading_runtime_from_app(
    app: Any,
    *,
    execution: ExecutionBackend = "dry_run",
    live_command: str | None = None,
    **kwargs: Any,
) -> BasicTradingRuntime:
    """Build a Runtime from a ``TradingBotApplication`` (settings + registry)."""
    settings = getattr(app, "settings", None)
    registry = getattr(app, "registry", None)
    if settings is None:
        raise ConfigurationError(
            "create_trading_runtime_from_app requires an application with settings"
        )
    return create_trading_runtime(
        settings,
        registry=registry,
        execution=execution,
        live_command=live_command,
        **kwargs,
    )


def _validate_paper_settings_mode(settings: Settings) -> None:
    mode = settings.trading_mode
    if mode == "live":
        raise ConfigurationError(
            "trading_mode='live' requires execution='live' or execution='shadow' "
            "with all live/shadow gates; paper/dry-run factory paths permit "
            "trading_mode='paper' only"
        )
    if mode not in _ALLOWED_PAPER_SETTINGS_MODES:
        raise ConfigurationError(
            f"trading_mode={mode!r} is not allowed; "
            "paper/dry-run factory paths permit trading_mode='paper' only"
        )


def _validate_execution(execution: str) -> None:
    if execution not in _ALLOWED_EXECUTIONS:
        raise ConfigurationError(
            f"execution={execution!r} is not supported; "
            "expected 'dry_run', 'paper', 'live', or 'shadow'"
        )


def _require_live_command(live_command: str | None) -> str:
    if live_command is None or not str(live_command).strip():
        raise ConfigurationError(
            "execution='live' requires live_command='run-once' or 'run-session'"
        )
    command = str(live_command).strip()
    if command not in _LIVE_COMMANDS:
        raise ConfigurationError(
            f"execution='live' rejects command={command!r}; "
            "only run-once and run-session are supervised LIVE surfaces"
        )
    return command


def _require_shadow_command(live_command: str | None) -> str:
    if live_command is None or not str(live_command).strip():
        raise ConfigurationError(
            "execution='shadow' requires live_command='run-once' or 'run-session'"
        )
    command = str(live_command).strip()
    if command not in _SHADOW_COMMANDS:
        raise ConfigurationError(
            f"execution='shadow' rejects command={command!r}; "
            "only run-once and run-session are supervised SHADOW surfaces"
        )
    return command


def _authorize_live_sandbox(settings: Settings, *, command: str) -> None:
    """Factory-time Authority check (mode_policy re-checks independently)."""
    auth = evaluate_live_enablement(
        settings,
        LiveExecutionContext(
            command=command,
            execution="live",
            context_mode=TradingMode.LIVE,
        ),
    )
    if not auth.authorized:
        raise ConfigurationError(auth.reason or "live enablement denied")


def _authorize_shadow(settings: Settings, *, command: str) -> None:
    """Factory-time shadow Authority check (G6b; mode_policy re-checks)."""
    auth = evaluate_shadow_enablement(
        settings,
        LiveExecutionContext(
            command=command,
            execution="shadow",
            context_mode=TradingMode.LIVE,
        ),
    )
    if not auth.authorized:
        raise ConfigurationError(auth.reason or "shadow enablement denied")


def _resolve_market_data(market_data: Any | None, registry: ModuleRegistry | None) -> Any:
    if market_data is not None:
        return market_data
    module = _get_module(registry, "market_data")
    return module


def _resolve_strategy_engine(
    strategy_engine: Any | None,
    registry: ModuleRegistry | None,
) -> Any:
    if strategy_engine is not None:
        return strategy_engine
    return _get_module(registry, "strategy_engine")


def _resolve_portfolio(portfolio: Any | None, registry: ModuleRegistry | None) -> Any:
    if portfolio is not None:
        return portfolio
    module = _get_module(registry, "portfolio_manager")
    try:
        resolved = module.portfolio
    except Exception as exc:
        raise ConfigurationError(
            "portfolio_manager module has no initialized portfolio"
        ) from exc
    if resolved is None:
        raise ConfigurationError(
            "portfolio_manager module has no initialized portfolio"
        )
    return resolved


def _resolve_order_manager(
    *,
    with_order_manager: bool,
    order_manager: OrderManager | None,
) -> OrderManager | None:
    if not with_order_manager:
        return None
    if order_manager is not None:
        return order_manager
    return OrderManager()


def _resolve_alert_notifier(
    *,
    with_alerts: bool,
    alert_notifier: AlertNotifier | None,
) -> AlertNotifier | None:
    if not with_alerts:
        return None
    if alert_notifier is not None:
        return alert_notifier
    return ConsoleNotifier()


def _build_executor(
    *,
    execution: ExecutionBackend,
    settings: Settings,
    registry: ModuleRegistry | None,
    broker: Broker | None,
    market_data: Any,
    session_calendar: Any,
    http_transport: Any | None,
    live_ledger: JsonLiveOrderLedger | None = None,
    shadow_audit: ShadowAuditLog | None = None,
    command: str = "run-once",
) -> DryRunExecutor | BrokerOrderExecutor | ShadowExecutor:
    if execution == "dry_run":
        return DryRunExecutor()

    if execution == "live":
        if live_ledger is None:
            raise ConfigurationError(
                "execution='live' requires an initialized live order ledger"
            )
        return _build_live_sandbox_executor(
            settings=settings,
            broker=broker,
            http_transport=http_transport,
            live_ledger=live_ledger,
        )

    if execution == "shadow":
        if shadow_audit is None:
            raise ConfigurationError(
                "execution='shadow' requires an initialized shadow audit log"
            )
        # No broker construction for shadow (D-S6): quotes from market data only.
        if broker is not None:
            raise ConfigurationError(
                "execution='shadow' rejects broker= injection; "
                "no-submit shadow must not construct a broker client"
            )
        assert settings.live_max_order_notional is not None
        assert settings.live_max_orders_per_day is not None
        quote_source = ClosedBarQuoteSource(
            market_data,
            freshness_enabled=False,
            market_hours_enabled=False,
            timeframe=settings.default_timeframe,
            max_age_seconds=settings.market_data_max_age_seconds,
            bar_periods=settings.market_data_freshness_bar_periods,
            slack_seconds=settings.market_data_freshness_slack_seconds,
            future_skew_seconds=settings.market_data_future_skew_seconds,
            session_calendar=session_calendar,
        )
        return ShadowExecutor(
            audit=shadow_audit,
            quote_source=quote_source,
            max_order_notional=settings.live_max_order_notional,
            max_orders_per_day=settings.live_max_orders_per_day,
            broker_adapter_id=settings.broker_name,
            broker_endpoint_class=settings.broker_endpoint_class,
            command=command,
            counter=LiveOrderCounter(),
        )

    paper_broker = _resolve_paper_broker(
        settings=settings,
        registry=registry,
        broker=broker,
        market_data=market_data,
        session_calendar=session_calendar,
    )
    return BrokerOrderExecutor(paper_broker)


def _build_live_sandbox_executor(
    *,
    settings: Settings,
    broker: Broker | None,
    http_transport: Any | None,
    live_ledger: JsonLiveOrderLedger,
) -> BrokerOrderExecutor:
    # M13.2 remediation: never accept injected broker instances under live.
    # Sandbox adapters must come from the approved registry path only.
    if broker is not None:
        raise ConfigurationError(
            "execution='live' rejects externally injected broker= instances; "
            "BROKER_SANDBOX adapters must be constructed via the approved registry"
        )

    try:
        live_broker = construct_sandbox_broker(
            settings,
            transport=http_transport,
        )
    except ConfigurationError:
        raise
    except Exception as exc:
        raise ConfigurationError(
            f"failed to construct approved sandbox broker: {exc.__class__.__name__}"
        ) from exc

    if isinstance(live_broker, PaperBroker):
        raise ConfigurationError(
            "execution='live' constructed PaperBroker; refusing silent downgrade"
        )

    assert settings.live_max_order_notional is not None
    assert settings.live_max_orders_per_day is not None
    guarded = LiveCapGuardBroker(
        live_broker,
        max_order_notional=settings.live_max_order_notional,
        max_orders_per_day=settings.live_max_orders_per_day,
        counter=LiveOrderCounter(),
        ledger=live_ledger,
    )
    try:
        guarded.connect()
    except Exception as exc:
        raise ConfigurationError(
            f"sandbox broker connect failed: {exc.__class__.__name__}"
        ) from exc
    return IdempotentLiveExecutor(guarded, ledger=live_ledger)


def _resolve_paper_broker(
    *,
    settings: Settings,
    registry: ModuleRegistry | None,
    broker: Broker | None,
    market_data: Any,
    session_calendar: Any,
) -> PaperBroker:
    quote_source = ClosedBarQuoteSource(
        market_data,
        freshness_enabled=settings.market_data_freshness_enabled,
        timeframe=settings.default_timeframe,
        max_age_seconds=settings.market_data_max_age_seconds,
        bar_periods=settings.market_data_freshness_bar_periods,
        slack_seconds=settings.market_data_freshness_slack_seconds,
        future_skew_seconds=settings.market_data_future_skew_seconds,
        session_calendar=session_calendar,
        market_hours_enabled=settings.market_hours_enabled,
        market_hours_policy=settings.market_hours_policy,
    )

    if broker is not None:
        if not isinstance(broker, PaperBroker):
            raise ConfigurationError(
                f"broker must be a PaperBroker for execution='paper'; "
                f"got {type(broker).__name__}"
            )
        # M11.1: wire closed-bar quote source when not already configured.
        if broker.quote_source is None:
            broker.set_quote_source(quote_source)
        return broker

    if registry is not None:
        try:
            module = registry.get("broker_interface")
        except Exception:
            module = None
        if module is not None:
            candidate = getattr(module, "broker", None)
            if candidate is not None:
                if not isinstance(candidate, PaperBroker):
                    raise ConfigurationError(
                        f"broker_interface broker {type(candidate).__name__} "
                        "is not a PaperBroker; live brokers are not allowed "
                        "on execution='paper'"
                    )
                if candidate.quote_source is None:
                    candidate.set_quote_source(quote_source)
                return candidate

    paper = PaperBroker(
        name=settings.broker_name or "paper",
        buying_power=settings.backtest_initial_capital,
        quote_source=quote_source,
    )
    paper.connect()
    return paper


def _get_module(registry: ModuleRegistry | None, name: str) -> Any:
    if registry is None:
        raise ConfigurationError(
            f"missing required dependency '{name}'; "
            "pass it explicitly or provide a loaded ModuleRegistry"
        )
    try:
        return registry.get(name)
    except Exception as exc:
        raise ConfigurationError(
            f"module '{name}' is not loaded; "
            "initialize the registry or pass the dependency explicitly"
        ) from exc
