"""General-purpose helper functions."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def ensure_directory(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def format_currency(value: Decimal | float | int, currency: str = "USD") -> str:
    amount = Decimal(str(value))
    return f"{currency} {amount:,.2f}"
