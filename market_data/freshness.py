"""Market-data freshness validation (Milestone 11.2).

Pure helpers: no network I/O. Callers inject ``now_utc`` for deterministic tests.
Does not implement market-hours / session-calendar logic (M11.3).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from core.types import TimeFrame

# Default cadence map for settings.default_timeframe strings / TimeFrame values.
_TIMEFRAME_SECONDS: dict[str, int] = {
    TimeFrame.M1.value: 60,
    TimeFrame.M5.value: 5 * 60,
    TimeFrame.M15.value: 15 * 60,
    TimeFrame.H1.value: 60 * 60,
    TimeFrame.H4.value: 4 * 60 * 60,
    TimeFrame.D1.value: 24 * 60 * 60,
    TimeFrame.W1.value: 7 * 24 * 60 * 60,
}


@dataclass(frozen=True)
class FreshnessResult:
    """Outcome of evaluating one bar's timestamp freshness."""

    ok: bool
    classification: str
    reason: str | None = None
    age_seconds: float | None = None


def utc_now() -> datetime:
    """Default injectable clock (UTC-aware)."""
    return datetime.now(timezone.utc)


def timeframe_seconds(timeframe: str | TimeFrame) -> int:
    """Return bar duration in seconds for a timeframe string or enum."""
    key = timeframe.value if isinstance(timeframe, TimeFrame) else str(timeframe).strip()
    if key not in _TIMEFRAME_SECONDS:
        raise ValueError(f"unsupported timeframe for freshness: {key!r}")
    return _TIMEFRAME_SECONDS[key]


def resolve_max_age_seconds(
    *,
    max_age_seconds: int | None,
    timeframe: str | TimeFrame,
    bar_periods: int = 2,
    slack_seconds: int = 120,
) -> int:
    """Absolute override, or periods × timeframe + slack."""
    if max_age_seconds is not None:
        if not isinstance(max_age_seconds, int) or isinstance(max_age_seconds, bool):
            raise ValueError(f"max_age_seconds must be an int; got {max_age_seconds!r}")
        if max_age_seconds < 0:
            raise ValueError(f"max_age_seconds must be >= 0; got {max_age_seconds}")
        return max_age_seconds
    if not isinstance(bar_periods, int) or isinstance(bar_periods, bool) or bar_periods < 1:
        raise ValueError(f"bar_periods must be a positive int; got {bar_periods!r}")
    if not isinstance(slack_seconds, int) or isinstance(slack_seconds, bool) or slack_seconds < 0:
        raise ValueError(f"slack_seconds must be >= 0; got {slack_seconds!r}")
    return bar_periods * timeframe_seconds(timeframe) + slack_seconds


def normalize_bar_timestamp(value: Any) -> datetime:
    """Normalize to UTC-aware datetime.

    Naive datetimes are treated as UTC (same convention as Yahoo ``_ensure_utc``).
    """
    if value is None:
        raise ValueError("timestamp is missing")
    if not isinstance(value, datetime):
        raise ValueError(f"timestamp must be datetime; got {type(value).__name__}")
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def evaluate_bar_freshness(
    bar: Any,
    *,
    now_utc: datetime | None = None,
    max_age_seconds: int,
    future_skew_seconds: int = 60,
) -> FreshnessResult:
    """Classify bar timestamp freshness against ``now_utc`` (fail-closed)."""
    if max_age_seconds < 0:
        return FreshnessResult(
            ok=False,
            classification="invalid_timestamp",
            reason=f"max_age_seconds must be >= 0; got {max_age_seconds}",
        )
    if (
        not isinstance(future_skew_seconds, int)
        or isinstance(future_skew_seconds, bool)
        or future_skew_seconds < 0
    ):
        return FreshnessResult(
            ok=False,
            classification="invalid_timestamp",
            reason=f"future_skew_seconds must be >= 0; got {future_skew_seconds!r}",
        )

    now = now_utc if now_utc is not None else utc_now()
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    else:
        now = now.astimezone(timezone.utc)

    raw = _extract_timestamp(bar)
    if raw is None and not _has_timestamp_key(bar):
        return FreshnessResult(
            ok=False,
            classification="missing_timestamp",
            reason="bar timestamp is missing",
        )
    if raw is None:
        return FreshnessResult(
            ok=False,
            classification="missing_timestamp",
            reason="bar timestamp is None",
        )

    try:
        bar_ts = normalize_bar_timestamp(raw)
    except ValueError as exc:
        return FreshnessResult(
            ok=False,
            classification="invalid_timestamp",
            reason=str(exc),
        )

    skew = timedelta(seconds=future_skew_seconds)
    if bar_ts > now + skew:
        return FreshnessResult(
            ok=False,
            classification="future_anomaly",
            reason=(
                f"bar timestamp {bar_ts.isoformat()} is beyond future skew "
                f"({future_skew_seconds}s) relative to {now.isoformat()}"
            ),
            age_seconds=(now - bar_ts).total_seconds(),
        )

    age = now - bar_ts
    age_seconds = age.total_seconds()
    if age_seconds > max_age_seconds:
        return FreshnessResult(
            ok=False,
            classification="stale",
            reason=(
                f"bar age {age_seconds:.3f}s exceeds max_age_seconds={max_age_seconds}"
            ),
            age_seconds=age_seconds,
        )

    return FreshnessResult(
        ok=True,
        classification="fresh",
        reason=None,
        age_seconds=age_seconds,
    )


def evaluate_last_bar_freshness(
    bars: list[Any],
    *,
    now_utc: datetime | None = None,
    max_age_seconds: int,
    future_skew_seconds: int = 60,
) -> FreshnessResult:
    """Evaluate freshness of the last bar in a non-empty series."""
    if not bars:
        return FreshnessResult(
            ok=False,
            classification="missing_timestamp",
            reason="no bars available for freshness evaluation",
        )
    return evaluate_bar_freshness(
        bars[-1],
        now_utc=now_utc,
        max_age_seconds=max_age_seconds,
        future_skew_seconds=future_skew_seconds,
    )


def _has_timestamp_key(bar: Any) -> bool:
    if isinstance(bar, dict):
        return "timestamp" in bar
    return hasattr(bar, "timestamp")


def _extract_timestamp(bar: Any) -> Any:
    if isinstance(bar, dict):
        return bar.get("timestamp")
    return getattr(bar, "timestamp", None)


Clock = Callable[[], datetime]
