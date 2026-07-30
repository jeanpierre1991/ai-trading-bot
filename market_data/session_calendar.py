"""Market session calendar abstractions (Milestone 11.3).

Pure helpers: no network I/O. Callers inject UTC ``now`` via ``resolve``.
Does not implement LIVE trading or multi-exchange routing.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from enum import Enum
from typing import Protocol, runtime_checkable

EXCHANGE_ID_XNYS = "XNYS"
TZ_AMERICA_NEW_YORK = "America/New_York"

# Inclusive coverage window for in-repo XNYS static tables (Decision F).
COVERAGE_START = date(2024, 1, 1)
COVERAGE_END = date(2027, 12, 31)


class SessionState(str, Enum):
    OPEN_RTH = "open_rth"
    PRE_MARKET = "pre_market"
    AFTER_HOURS = "after_hours"
    CLOSED_OVERNIGHT = "closed_overnight"
    CLOSED_WEEKEND = "closed_weekend"
    CLOSED_HOLIDAY = "closed_holiday"
    CALENDAR_UNAVAILABLE = "calendar_unavailable"


class MarketHoursPolicy(str, Enum):
    ALLOW = "allow"
    REJECT = "reject"


@dataclass(frozen=True)
class SessionSnapshot:
    """Resolved exchange session state at one UTC instant."""

    state: SessionState
    exchange_id: str
    tz_name: str
    as_of_utc: datetime
    session_date: date | None
    regular_open_utc: datetime | None
    regular_close_utc: datetime | None
    last_completed_regular_close_utc: datetime | None
    is_rth_open: bool
    is_early_close_day: bool
    reason: str | None = None


@runtime_checkable
class SessionCalendar(Protocol):
    """Resolves exchange session state for a UTC instant."""

    def resolve(self, now_utc: datetime) -> SessionSnapshot:
        """Return session snapshot for ``now_utc`` (must be timezone-aware)."""
        ...


def freshness_reference_now(
    snapshot: SessionSnapshot,
    wall_now_utc: datetime,
) -> datetime:
    """Return the UTC instant used for M11.2 age checks (Decision B).

    RTH open → wall clock. Otherwise → last completed regular-session close.
    Raises ``ValueError`` when reference cannot be determined (fail-closed).
    """
    if wall_now_utc.tzinfo is None:
        raise ValueError("wall_now_utc must be timezone-aware")
    wall = wall_now_utc.astimezone(timezone.utc)

    if snapshot.state is SessionState.CALENDAR_UNAVAILABLE:
        raise ValueError(
            snapshot.reason or "session calendar unavailable for freshness reference"
        )
    if snapshot.is_rth_open:
        return wall
    ref = snapshot.last_completed_regular_close_utc
    if ref is None:
        raise ValueError(
            "last_completed_regular_close_utc is required when market is not RTH-open"
        )
    if ref.tzinfo is None:
        raise ValueError("last_completed_regular_close_utc must be timezone-aware")
    return ref.astimezone(timezone.utc)


def is_trading_permitted(snapshot: SessionSnapshot, policy: MarketHoursPolicy | str) -> bool:
    """Return whether RTH trading is permitted under ``policy`` (Decision A/D)."""
    resolved = (
        policy
        if isinstance(policy, MarketHoursPolicy)
        else MarketHoursPolicy(str(policy).strip().lower())
    )
    if snapshot.state is SessionState.CALENDAR_UNAVAILABLE:
        return False
    if resolved is MarketHoursPolicy.ALLOW:
        return True
    if resolved is MarketHoursPolicy.REJECT:
        return snapshot.is_rth_open
    return False


def normalize_utc(value: datetime) -> datetime:
    """Require aware datetime and normalize to UTC."""
    if not isinstance(value, datetime):
        raise ValueError(f"timestamp must be datetime; got {type(value).__name__}")
    if value.tzinfo is None:
        raise ValueError("timestamp must be timezone-aware UTC (or convertible)")
    return value.astimezone(timezone.utc)


def local_time_in_rth(
    local_t: time,
    *,
    open_t: time = time(9, 30),
    close_t: time = time(16, 0),
) -> bool:
    """Decision G: open-inclusive, close-exclusive."""
    return open_t <= local_t < close_t
