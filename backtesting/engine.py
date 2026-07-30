"""Backtesting engine for strategy evaluation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal


@dataclass(frozen=True)
class BacktestResult:
    """Outcome of a synthetic dry-check or a real historical backtest run."""

    strategy_name: str
    start_date: datetime
    end_date: datetime
    initial_capital: Decimal
    final_capital: Decimal
    total_return_pct: Decimal
    total_trades: int
    win_rate: Decimal
    wins: int = 0
    losses: int = 0
    realized_pnl: Decimal = Decimal("0")
    commissions_paid: Decimal = Decimal("0")
    cycles_executed: int = 0

    @property
    def ending_equity(self) -> Decimal:
        """Alias for ``final_capital`` (M10.2 metrics naming)."""
        return self.final_capital

    @property
    def return_pct(self) -> Decimal:
        """Alias for ``total_return_pct`` (M10.2 metrics naming)."""
        return self.total_return_pct

    def to_dict(self) -> dict[str, str | int | float]:
        return {
            "strategy_name": self.strategy_name,
            "start_date": self.start_date.isoformat(),
            "end_date": self.end_date.isoformat(),
            "initial_capital": float(self.initial_capital),
            "final_capital": float(self.final_capital),
            "ending_equity": float(self.final_capital),
            "total_return_pct": float(self.total_return_pct),
            "return_pct": float(self.total_return_pct),
            "total_trades": self.total_trades,
            "wins": self.wins,
            "losses": self.losses,
            "win_rate": float(self.win_rate),
            "realized_pnl": float(self.realized_pnl),
            "commissions_paid": float(self.commissions_paid),
            "cycles_executed": self.cycles_executed,
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
