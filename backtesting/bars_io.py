"""Network-free historical bar helpers for the M10.4 CLI path.

Does not call Yahoo or any external market-data API.
"""

from __future__ import annotations

import csv
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

from core.exceptions import ConfigurationError
from core.types import MarketBar, TimeFrame
from market_data.historical_provider import MAX_HISTORICAL_BARS

_REQUIRED_CSV_COLUMNS = ("timestamp", "open", "high", "low", "close", "volume")


def make_synthetic_bars(
    count: int,
    *,
    symbol: str,
    timeframe: TimeFrame | str,
    start_price: Decimal = Decimal("100"),
) -> list[MarketBar]:
    """Build a deterministic, strictly increasing synthetic OHLCV series."""
    if not isinstance(count, int) or isinstance(count, bool) or count < 1:
        raise ConfigurationError(f"synthetic bar count must be a positive int; got {count!r}")
    if count > MAX_HISTORICAL_BARS:
        raise ConfigurationError(
            f"synthetic bar count exceeds MAX_HISTORICAL_BARS={MAX_HISTORICAL_BARS}; "
            f"got {count}"
        )
    resolved_symbol = str(symbol).strip()
    if not resolved_symbol:
        raise ConfigurationError("symbol must be a non-empty string")
    tf = timeframe if isinstance(timeframe, TimeFrame) else TimeFrame(timeframe)
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    bars: list[MarketBar] = []
    for index in range(count):
        close = (start_price + Decimal(index)).quantize(Decimal("0.0001"))
        bars.append(
            MarketBar(
                timestamp=start + timedelta(hours=index),
                open=close,
                high=close + Decimal("1"),
                low=close - Decimal("1"),
                close=close,
                volume=Decimal("1000"),
                symbol=resolved_symbol,
                timeframe=tf.value,
            )
        )
    return bars


def load_bars_from_csv(
    path: str | Path,
    *,
    symbol: str,
    timeframe: TimeFrame | str,
) -> list[MarketBar]:
    """Load OHLCV bars from a local CSV file (no network)."""
    file_path = Path(path)
    if not file_path.is_file():
        raise ConfigurationError(f"bars file not found: {file_path}")

    resolved_symbol = str(symbol).strip()
    if not resolved_symbol:
        raise ConfigurationError("symbol must be a non-empty string")
    tf = timeframe if isinstance(timeframe, TimeFrame) else TimeFrame(timeframe)

    bars: list[MarketBar] = []
    with file_path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise ConfigurationError("bars CSV has no header row")
        headers = {name.strip().lower() for name in reader.fieldnames}
        missing = [col for col in _REQUIRED_CSV_COLUMNS if col not in headers]
        if missing:
            raise ConfigurationError(
                f"bars CSV missing required columns: {missing}; "
                f"expected {_REQUIRED_CSV_COLUMNS}"
            )
        for row_number, row in enumerate(reader, start=2):
            if len(bars) >= MAX_HISTORICAL_BARS:
                raise ConfigurationError(
                    f"bars CSV exceeds MAX_HISTORICAL_BARS={MAX_HISTORICAL_BARS}"
                )
            try:
                bars.append(
                    MarketBar(
                        timestamp=_parse_timestamp(row["timestamp"]),
                        open=_parse_decimal(row["open"], "open"),
                        high=_parse_decimal(row["high"], "high"),
                        low=_parse_decimal(row["low"], "low"),
                        close=_parse_decimal(row["close"], "close"),
                        volume=_parse_decimal(row["volume"], "volume"),
                        symbol=resolved_symbol,
                        timeframe=tf.value,
                    )
                )
            except ConfigurationError as exc:
                raise ConfigurationError(
                    f"bars CSV row {row_number}: {exc}"
                ) from exc

    if not bars:
        raise ConfigurationError("bars CSV contains no data rows")
    return bars


def _parse_timestamp(value: str) -> datetime:
    text = str(value).strip()
    if not text:
        raise ConfigurationError("timestamp is empty")
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ConfigurationError(f"invalid timestamp {value!r}") from exc
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def _parse_decimal(value: str, field: str) -> Decimal:
    try:
        return Decimal(str(value).strip())
    except (InvalidOperation, ValueError) as exc:
        raise ConfigurationError(f"invalid {field}={value!r}") from exc
