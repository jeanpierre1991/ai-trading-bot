"""Backtesting engine for strategy evaluation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal


@dataclass(frozen=True)
class BacktestResult:
    strategy_name: str
    start_date: datetime
    end_date: datetime
    initial_capital: Decimal
    final_capital: Decimal
    total_return_pct: Decimal
    total_trades: int
    win_rate: Decimal

    def to_dict(self) -> dict[str, str | int | float]:
        return {
            "strategy_name": self.strategy_name,
            "start_date": self.start_date.isoformat(),
            "end_date": self.end_date.isoformat(),
            "initial_capital": float(self.initial_capital),
            "final_capital": float(self.final_capital),
            "total_return_pct": float(self.total_return_pct),
            "total_trades": self.total_trades,
            "win_rate": float(self.win_rate),
        }


class BacktestEngine:
    """Runs historical simulations (full logic in milestone 2)."""

    def __init__(
        self,
        initial_capital: Decimal,
        commission_pct: Decimal = Decimal("0.001"),
    ) -> None:
        self._initial_capital = initial_capital
        self._commission_pct = commission_pct
        self._runs = 0

    @property
    def run_count(self) -> int:
        return self._runs

    def run_dry_check(self, strategy_name: str = "architecture_check") -> BacktestResult:
        """Returns a synthetic result for startup verification."""
        self._runs += 1
        now = datetime.now(timezone.utc)
        return BacktestResult(
            strategy_name=strategy_name,
            start_date=now,
            end_date=now,
            initial_capital=self._initial_capital,
            final_capital=self._initial_capital,
            total_return_pct=Decimal("0"),
            total_trades=0,
            win_rate=Decimal("0"),
        )
