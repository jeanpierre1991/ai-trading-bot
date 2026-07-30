"""Composition root for BasicTradingRuntime (Milestone 8.6).

Assembles existing components into a usable Runtime. Does not fetch market
data, place orders, or run decision cycles.
"""

from __future__ import annotations

from typing import Any, Literal

from alerts.notifier import AlertNotifier, ConsoleNotifier
from broker_interface.broker import PaperBroker
from broker_interface.quotes import ClosedBarQuoteSource
from config.settings import Settings
from core.exceptions import ConfigurationError
from core.module_registry import ModuleRegistry
from order_manager.manager import OrderManager
from risk_manager.base import RiskManager
from risk_manager.basic import BasicRiskManager
from runtime.broker_executor import BrokerOrderExecutor
from runtime.dry_run import DryRunExecutor
from runtime.trading_runtime import BasicTradingRuntime

ExecutionBackend = Literal["dry_run", "paper"]
_ALLOWED_SETTINGS_MODES = frozenset({"paper"})
_ALLOWED_EXECUTIONS = frozenset({"dry_run", "paper"})


def create_trading_runtime(
    settings: Settings,
    *,
    execution: ExecutionBackend = "dry_run",
    registry: ModuleRegistry | None = None,
    market_data: Any | None = None,
    strategy_engine: Any | None = None,
    portfolio: Any | None = None,
    risk_manager: RiskManager | None = None,
    broker: PaperBroker | None = None,
    with_order_manager: bool = True,
    order_manager: OrderManager | None = None,
    with_alerts: bool = True,
    alert_notifier: AlertNotifier | None = None,
) -> BasicTradingRuntime:
    """Build a ``BasicTradingRuntime`` from settings and wired dependencies.

    Parameters
    ----------
    settings:
        Application settings. ``trading_mode`` must be ``\"paper\"``.
    execution:
        ``\"dry_run\"`` → ``DryRunExecutor``;
        ``\"paper\"`` → ``BrokerOrderExecutor(PaperBroker)``.
    registry:
        Optional loaded module registry used when explicit deps are omitted.
    market_data / strategy_engine / portfolio:
        Explicit overrides (preferred in tests). Resolved from ``registry``
        when omitted.
    risk_manager:
        Override; default is a fresh ``BasicRiskManager(settings)``.
    broker:
        Optional ``PaperBroker`` for ``execution=\"paper\"``.
    with_order_manager / order_manager:
        When ``with_order_manager`` is True, uses ``order_manager`` or a new
        ``OrderManager()``. When False, injects ``None``.
    with_alerts / alert_notifier:
        When ``with_alerts`` is True, uses ``alert_notifier`` or a new
        ``ConsoleNotifier()``. When False, injects ``None``.
        Runtime still respects ``settings.alerts_enabled`` at send time.
    """
    _validate_settings_mode(settings)
    _validate_execution(execution)

    resolved_market_data = _resolve_market_data(market_data, registry)
    resolved_strategy = _resolve_strategy_engine(strategy_engine, registry)
    resolved_portfolio = _resolve_portfolio(portfolio, registry)
    resolved_risk = (
        risk_manager if risk_manager is not None else BasicRiskManager(settings)
    )
    resolved_executor = _build_executor(
        execution=execution,
        settings=settings,
        registry=registry,
        broker=broker,
        market_data=resolved_market_data,
    )
    resolved_order_manager = _resolve_order_manager(
        with_order_manager=with_order_manager,
        order_manager=order_manager,
    )
    resolved_alert_notifier = _resolve_alert_notifier(
        with_alerts=with_alerts,
        alert_notifier=alert_notifier,
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
    )


def create_trading_runtime_from_app(
    app: Any,
    *,
    execution: ExecutionBackend = "dry_run",
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
        **kwargs,
    )


def _validate_settings_mode(settings: Settings) -> None:
    mode = settings.trading_mode
    if mode == "live":
        raise ConfigurationError(
            "trading_mode='live' is not allowed; "
            "Milestone 8 factory permits paper and dry-run only"
        )
    if mode not in _ALLOWED_SETTINGS_MODES:
        raise ConfigurationError(
            f"trading_mode={mode!r} is not allowed; "
            "Milestone 8 factory permits paper and dry-run only"
        )


def _validate_execution(execution: str) -> None:
    if execution not in _ALLOWED_EXECUTIONS:
        raise ConfigurationError(
            f"execution={execution!r} is not supported; "
            "expected 'dry_run' or 'paper'"
        )


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
    broker: PaperBroker | None,
    market_data: Any,
) -> DryRunExecutor | BrokerOrderExecutor:
    if execution == "dry_run":
        return DryRunExecutor()

    paper_broker = _resolve_paper_broker(
        settings=settings,
        registry=registry,
        broker=broker,
        market_data=market_data,
    )
    return BrokerOrderExecutor(paper_broker)


def _resolve_paper_broker(
    *,
    settings: Settings,
    registry: ModuleRegistry | None,
    broker: PaperBroker | None,
    market_data: Any,
) -> PaperBroker:
    quote_source = ClosedBarQuoteSource(
        market_data,
        freshness_enabled=settings.market_data_freshness_enabled,
        timeframe=settings.default_timeframe,
        max_age_seconds=settings.market_data_max_age_seconds,
        bar_periods=settings.market_data_freshness_bar_periods,
        slack_seconds=settings.market_data_freshness_slack_seconds,
        future_skew_seconds=settings.market_data_future_skew_seconds,
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
                        "is not a PaperBroker; live brokers are not allowed"
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
