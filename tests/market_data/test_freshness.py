"""M11.2 market-data freshness helpers (pure, no network)."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from core.types import MarketBar, TimeFrame
from market_data.freshness import (
    evaluate_bar_freshness,
    evaluate_last_bar_freshness,
    normalize_bar_timestamp,
    resolve_max_age_seconds,
    timeframe_seconds,
)


def _bar(*, timestamp: object) -> MarketBar:
    return MarketBar(
        timestamp=timestamp,
        open=Decimal("100"),
        high=Decimal("101"),
        low=Decimal("99"),
        close=Decimal("100"),
        volume=Decimal("10"),
        symbol="AAPL",
        timeframe="1h",
    )


def test_timeframe_seconds_known_values() -> None:
    assert timeframe_seconds("1h") == 3600
    assert timeframe_seconds(TimeFrame.H1) == 3600
    assert timeframe_seconds("1d") == 86400


def test_timeframe_seconds_rejects_unknown() -> None:
    with pytest.raises(ValueError, match="unsupported timeframe"):
        timeframe_seconds("2h")


def test_resolve_max_age_absolute_override() -> None:
    assert (
        resolve_max_age_seconds(
            max_age_seconds=90,
            timeframe="1h",
            bar_periods=2,
            slack_seconds=120,
        )
        == 90
    )


def test_resolve_max_age_derived_periods_times_tf_plus_slack() -> None:
    # 2 * 3600 + 120 = 7320
    assert (
        resolve_max_age_seconds(
            max_age_seconds=None,
            timeframe="1h",
            bar_periods=2,
            slack_seconds=120,
        )
        == 7320
    )


def test_normalize_naive_treated_as_utc() -> None:
    naive = datetime(2026, 7, 13, 12, 0, 0)
    assert normalize_bar_timestamp(naive) == datetime(
        2026, 7, 13, 12, 0, 0, tzinfo=timezone.utc
    )


def test_normalize_aware_converted_to_utc() -> None:
    from datetime import timedelta

    eastern = timezone(timedelta(hours=-4))
    aware = datetime(2026, 7, 13, 8, 0, tzinfo=eastern)
    assert normalize_bar_timestamp(aware) == datetime(
        2026, 7, 13, 12, 0, tzinfo=timezone.utc
    )


def test_fresh_at_exact_max_age_boundary() -> None:
    now = datetime(2026, 7, 13, 12, 0, tzinfo=timezone.utc)
    bar_ts = datetime(2026, 7, 13, 10, 0, tzinfo=timezone.utc)  # age 7200
    result = evaluate_bar_freshness(
        _bar(timestamp=bar_ts),
        now_utc=now,
        max_age_seconds=7200,
        future_skew_seconds=60,
    )
    assert result.ok is True
    assert result.classification == "fresh"
    assert result.age_seconds == 7200.0


def test_stale_one_second_past_max_age() -> None:
    now = datetime(2026, 7, 13, 12, 0, tzinfo=timezone.utc)
    bar_ts = datetime(2026, 7, 13, 9, 59, 59, tzinfo=timezone.utc)  # age 7201
    result = evaluate_bar_freshness(
        _bar(timestamp=bar_ts),
        now_utc=now,
        max_age_seconds=7200,
        future_skew_seconds=60,
    )
    assert result.ok is False
    assert result.classification == "stale"


def test_missing_timestamp() -> None:
    result = evaluate_bar_freshness(
        {"close": Decimal("1")},
        now_utc=datetime(2026, 7, 13, tzinfo=timezone.utc),
        max_age_seconds=100,
    )
    assert result.ok is False
    assert result.classification == "missing_timestamp"


def test_none_timestamp() -> None:
    result = evaluate_bar_freshness(
        _bar(timestamp=None),
        now_utc=datetime(2026, 7, 13, tzinfo=timezone.utc),
        max_age_seconds=100,
    )
    assert result.ok is False
    assert result.classification == "missing_timestamp"


def test_invalid_timestamp_type() -> None:
    result = evaluate_bar_freshness(
        _bar(timestamp="2026-07-13"),
        now_utc=datetime(2026, 7, 13, tzinfo=timezone.utc),
        max_age_seconds=100,
    )
    assert result.ok is False
    assert result.classification == "invalid_timestamp"


def test_future_within_skew_ok() -> None:
    now = datetime(2026, 7, 13, 12, 0, tzinfo=timezone.utc)
    bar_ts = datetime(2026, 7, 13, 12, 0, 30, tzinfo=timezone.utc)  # +30s
    result = evaluate_bar_freshness(
        _bar(timestamp=bar_ts),
        now_utc=now,
        max_age_seconds=7200,
        future_skew_seconds=60,
    )
    assert result.ok is True
    assert result.classification == "fresh"


def test_future_beyond_skew_anomaly() -> None:
    now = datetime(2026, 7, 13, 12, 0, tzinfo=timezone.utc)
    bar_ts = datetime(2026, 7, 13, 12, 2, 0, tzinfo=timezone.utc)  # +120s
    result = evaluate_bar_freshness(
        _bar(timestamp=bar_ts),
        now_utc=now,
        max_age_seconds=7200,
        future_skew_seconds=60,
    )
    assert result.ok is False
    assert result.classification == "future_anomaly"


def test_evaluate_last_bar_uses_trailing_bar() -> None:
    now = datetime(2026, 7, 13, 12, 0, tzinfo=timezone.utc)
    old = _bar(timestamp=datetime(2026, 7, 1, tzinfo=timezone.utc))
    fresh = _bar(timestamp=datetime(2026, 7, 13, 11, 0, tzinfo=timezone.utc))
    result = evaluate_last_bar_freshness(
        [old, fresh],
        now_utc=now,
        max_age_seconds=7320,
    )
    assert result.ok is True
