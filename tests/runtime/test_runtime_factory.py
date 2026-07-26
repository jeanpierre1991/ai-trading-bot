"""M8.6 composition-root factory tests (no network / Yahoo)."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

from alerts.notifier import AlertNotifier, ConsoleNotifier
from broker_interface.broker import PaperBroker
from config.settings import Settings
from core.exceptions import ConfigurationError
from core.module_registry import ModuleRegistry
from order_manager.manager import OrderManager
from portfolio_manager.portfolio import Portfolio
from risk_manager.basic import BasicRiskManager
from runtime.broker_executor import BrokerOrderExecutor
from runtime.dry_run import DryRunExecutor
from runtime.factory import create_trading_runtime, create_trading_runtime_from_app
from runtime.trading_runtime import BasicTradingRuntime


class _FakeMarketData:
    """Local stub — never touches the network."""

    def get_bars(self, symbol: str | None = None, limit: int = 100) -> list:
        return []


class _FakeStrategyEngine:
    def evaluate(self, bars: list, *, symbol: str | None = None, strategy_name: str | None = None):
        raise AssertionError("factory must not call strategy.evaluate")


class _NonPaperBroker:
    """Stand-in for a disallowed non-paper broker."""

    pass


def _explicit_deps(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "market_data": _FakeMarketData(),
        "strategy_engine": _FakeStrategyEngine(),
        "portfolio": Portfolio(cash=Decimal("100000")),
    }
    base.update(overrides)
    return base


def test_factory_creates_dry_run_runtime() -> None:
    settings = Settings(trading_mode="paper", market_data_provider="mock")
    runtime = create_trading_runtime(settings, execution="dry_run", **_explicit_deps())

    assert isinstance(runtime, BasicTradingRuntime)
    assert isinstance(runtime.executor, DryRunExecutor)
    assert runtime.settings is settings


def test_factory_creates_paper_runtime() -> None:
    settings = Settings(trading_mode="paper", market_data_provider="mock")
    runtime = create_trading_runtime(settings, execution="paper", **_explicit_deps())

    assert isinstance(runtime, BasicTradingRuntime)
    assert isinstance(runtime.executor, BrokerOrderExecutor)
    assert isinstance(runtime.executor.broker, PaperBroker)
    assert runtime.executor.broker.get_status().connected is True


def test_factory_rejects_live_trading_mode() -> None:
    settings = Settings(trading_mode="live", market_data_provider="mock")

    with pytest.raises(ConfigurationError, match="live"):
        create_trading_runtime(settings, execution="dry_run", **_explicit_deps())


def test_factory_rejects_backtest_trading_mode() -> None:
    settings = Settings(trading_mode="backtest", market_data_provider="mock")

    with pytest.raises(ConfigurationError, match="backtest"):
        create_trading_runtime(settings, execution="dry_run", **_explicit_deps())


def test_factory_rejects_unsupported_execution() -> None:
    settings = Settings(trading_mode="paper", market_data_provider="mock")

    with pytest.raises(ConfigurationError, match="execution"):
        create_trading_runtime(
            settings,
            execution="live",  # type: ignore[arg-type]
            **_explicit_deps(),
        )


def test_factory_wires_risk_portfolio_strategy_provider() -> None:
    settings = Settings(trading_mode="paper", market_data_provider="mock")
    market_data = _FakeMarketData()
    strategy = _FakeStrategyEngine()
    portfolio = Portfolio(cash=Decimal("50000"))
    risk = BasicRiskManager(settings)

    runtime = create_trading_runtime(
        settings,
        execution="dry_run",
        market_data=market_data,
        strategy_engine=strategy,
        portfolio=portfolio,
        risk_manager=risk,
    )

    assert runtime.market_data is market_data
    assert runtime.strategy_engine is strategy
    assert runtime.portfolio is portfolio
    assert runtime.risk_manager is risk
    assert isinstance(runtime.risk_manager, BasicRiskManager)


def test_factory_default_risk_is_basic_risk_manager() -> None:
    settings = Settings(trading_mode="paper", market_data_provider="mock")
    runtime = create_trading_runtime(settings, execution="dry_run", **_explicit_deps())

    assert isinstance(runtime.risk_manager, BasicRiskManager)


def test_factory_order_manager_optional() -> None:
    settings = Settings(trading_mode="paper", market_data_provider="mock")

    without_om = create_trading_runtime(
        settings,
        execution="dry_run",
        with_order_manager=False,
        **_explicit_deps(),
    )
    assert without_om.order_manager is None

    custom_om = OrderManager()
    with_om = create_trading_runtime(
        settings,
        execution="dry_run",
        with_order_manager=True,
        order_manager=custom_om,
        **_explicit_deps(),
    )
    assert with_om.order_manager is custom_om

    default_om = create_trading_runtime(
        settings,
        execution="dry_run",
        with_order_manager=True,
        **_explicit_deps(),
    )
    assert isinstance(default_om.order_manager, OrderManager)
    assert default_om.order_manager is not custom_om


def test_factory_alert_notifier_optional() -> None:
    settings = Settings(trading_mode="paper", market_data_provider="mock")

    without_alerts = create_trading_runtime(
        settings,
        execution="dry_run",
        with_alerts=False,
        **_explicit_deps(),
    )
    assert without_alerts.alert_notifier is None

    custom = ConsoleNotifier()
    with_alerts = create_trading_runtime(
        settings,
        execution="dry_run",
        with_alerts=True,
        alert_notifier=custom,
        **_explicit_deps(),
    )
    assert with_alerts.alert_notifier is custom
    assert isinstance(with_alerts.alert_notifier, AlertNotifier)

    default_alerts = create_trading_runtime(
        settings,
        execution="dry_run",
        with_alerts=True,
        **_explicit_deps(),
    )
    assert isinstance(default_alerts.alert_notifier, ConsoleNotifier)


def test_factory_rejects_missing_dependencies() -> None:
    settings = Settings(trading_mode="paper", market_data_provider="mock")

    with pytest.raises(ConfigurationError, match="market_data"):
        create_trading_runtime(
            settings,
            execution="dry_run",
            strategy_engine=_FakeStrategyEngine(),
            portfolio=Portfolio(cash=Decimal("100000")),
        )


def test_factory_rejects_non_paper_broker_override() -> None:
    settings = Settings(trading_mode="paper", market_data_provider="mock")

    with pytest.raises(ConfigurationError, match="PaperBroker"):
        create_trading_runtime(
            settings,
            execution="paper",
            broker=_NonPaperBroker(),  # type: ignore[arg-type]
            **_explicit_deps(),
        )


def test_factory_uses_injected_paper_broker() -> None:
    settings = Settings(trading_mode="paper", market_data_provider="mock")
    paper = PaperBroker(name="test-paper", buying_power=Decimal("25000"))

    runtime = create_trading_runtime(
        settings,
        execution="paper",
        broker=paper,
        **_explicit_deps(),
    )

    assert isinstance(runtime.executor, BrokerOrderExecutor)
    assert runtime.executor.broker is paper


def test_factory_resolves_from_registry_without_network() -> None:
    """Registry path uses mock market-data provider only — no Yahoo."""
    settings = Settings(
        trading_mode="paper",
        market_data_provider="mock",
        broker_name="paper",
    )
    registry = ModuleRegistry(
        settings,
        packages=(
            "market_data",
            "strategy_engine",
            "portfolio_manager",
            "broker_interface",
        ),
    )
    registry.discover()
    registry.load_all()

    runtime = create_trading_runtime(
        settings,
        execution="dry_run",
        registry=registry,
        with_order_manager=False,
        with_alerts=False,
    )

    assert runtime.market_data is registry.get("market_data")
    assert runtime.strategy_engine is registry.get("strategy_engine")
    assert runtime.portfolio is registry.get("portfolio_manager").portfolio
    assert isinstance(runtime.risk_manager, BasicRiskManager)
    assert isinstance(runtime.executor, DryRunExecutor)
    assert runtime.order_manager is None
    assert runtime.alert_notifier is None


def test_factory_from_app_helper() -> None:
    settings = Settings(trading_mode="paper", market_data_provider="mock")

    class _FakeApp:
        def __init__(self) -> None:
            self.settings = settings
            self.registry = None

    runtime = create_trading_runtime_from_app(
        _FakeApp(),
        execution="dry_run",
        **_explicit_deps(),
        with_order_manager=False,
        with_alerts=False,
    )
    assert isinstance(runtime, BasicTradingRuntime)
    assert isinstance(runtime.executor, DryRunExecutor)


def test_factory_does_not_invoke_market_or_strategy() -> None:
    """Assembly-only: stubs raise if get_bars/evaluate were called."""

    class _TouchMarketData:
        def get_bars(self, *args: Any, **kwargs: Any) -> list:
            raise AssertionError("factory must not call get_bars")

    class _TouchStrategy:
        def evaluate(self, *args: Any, **kwargs: Any) -> None:
            raise AssertionError("factory must not call evaluate")

    settings = Settings(trading_mode="paper", market_data_provider="mock")
    runtime = create_trading_runtime(
        settings,
        execution="dry_run",
        market_data=_TouchMarketData(),
        strategy_engine=_TouchStrategy(),
        portfolio=Portfolio(cash=Decimal("100000")),
        with_order_manager=False,
        with_alerts=False,
    )
    assert isinstance(runtime, BasicTradingRuntime)
