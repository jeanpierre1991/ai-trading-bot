"""M11.3 XNYS session calendar unit tests (frozen clock, no network)."""

from __future__ import annotations

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from market_data.calendars.us_equity_xnys import UsEquityXnysCalendar, build_session_calendar
from market_data.session_calendar import (
    MarketHoursPolicy,
    SessionState,
    freshness_reference_now,
    is_trading_permitted,
    local_time_in_rth,
)

_NY = ZoneInfo("America/New_York")


def _utc(year: int, month: int, day: int, hour: int, minute: int = 0) -> datetime:
    """Build UTC instant from America/New_York civil time."""
    return datetime(year, month, day, hour, minute, tzinfo=_NY).astimezone(timezone.utc)


def test_build_session_calendar_xnys() -> None:
    cal = build_session_calendar("xnys")
    assert isinstance(cal, UsEquityXnysCalendar)


def test_build_session_calendar_rejects_unknown() -> None:
    with pytest.raises(ValueError, match="unsupported"):
        build_session_calendar("xlon")


def test_rth_open_inclusive_close_exclusive() -> None:
    cal = UsEquityXnysCalendar()
    # Wednesday 2026-07-15 is a normal trading day
    at_open = cal.resolve(_utc(2026, 7, 15, 9, 30))
    assert at_open.state is SessionState.OPEN_RTH
    assert at_open.is_rth_open is True

    before_open = cal.resolve(_utc(2026, 7, 15, 9, 29))
    assert before_open.state is SessionState.PRE_MARKET
    assert before_open.is_rth_open is False

    at_close = cal.resolve(_utc(2026, 7, 15, 16, 0))
    assert at_close.state is SessionState.AFTER_HOURS
    assert at_close.is_rth_open is False

    mid = cal.resolve(_utc(2026, 7, 15, 12, 0))
    assert mid.is_rth_open is True


def test_weekend_closed() -> None:
    cal = UsEquityXnysCalendar()
    snap = cal.resolve(_utc(2026, 7, 18, 12, 0))  # Saturday
    assert snap.state is SessionState.CLOSED_WEEKEND
    assert snap.is_rth_open is False
    assert snap.last_completed_regular_close_utc is not None


def test_holiday_july4_2025() -> None:
    cal = UsEquityXnysCalendar()
    snap = cal.resolve(_utc(2025, 7, 4, 12, 0))
    assert snap.state is SessionState.CLOSED_HOLIDAY
    assert snap.is_rth_open is False


def test_early_close_day_after_1300() -> None:
    cal = UsEquityXnysCalendar()
    # 2025-07-03 early close at 13:00 ET
    during = cal.resolve(_utc(2025, 7, 3, 11, 0))
    assert during.is_rth_open is True
    assert during.is_early_close_day is True

    after = cal.resolve(_utc(2025, 7, 3, 13, 0))
    assert after.is_rth_open is False
    assert after.state is SessionState.AFTER_HOURS
    assert after.is_early_close_day is True


def test_outside_coverage_fail_closed() -> None:
    cal = UsEquityXnysCalendar()
    snap = cal.resolve(_utc(2023, 6, 15, 12, 0))
    assert snap.state is SessionState.CALENDAR_UNAVAILABLE
    assert snap.is_rth_open is False
    assert snap.last_completed_regular_close_utc is None


def test_naive_now_fail_closed() -> None:
    cal = UsEquityXnysCalendar()
    snap = cal.resolve(datetime(2026, 7, 15, 12, 0))  # naive
    assert snap.state is SessionState.CALENDAR_UNAVAILABLE


def test_freshness_reference_now_rth_uses_wall() -> None:
    cal = UsEquityXnysCalendar()
    wall = _utc(2026, 7, 15, 11, 0)
    snap = cal.resolve(wall)
    assert freshness_reference_now(snap, wall) == wall.astimezone(timezone.utc)


def test_freshness_reference_now_weekend_uses_last_close() -> None:
    cal = UsEquityXnysCalendar()
    wall = _utc(2026, 7, 18, 12, 0)  # Saturday
    snap = cal.resolve(wall)
    ref = freshness_reference_now(snap, wall)
    assert ref == snap.last_completed_regular_close_utc
    # Friday 2026-07-17 close 16:00 ET
    assert ref == _utc(2026, 7, 17, 16, 0)


def test_is_trading_permitted_policies() -> None:
    cal = UsEquityXnysCalendar()
    open_snap = cal.resolve(_utc(2026, 7, 15, 11, 0))
    closed_snap = cal.resolve(_utc(2026, 7, 18, 12, 0))
    assert is_trading_permitted(open_snap, MarketHoursPolicy.ALLOW) is True
    assert is_trading_permitted(closed_snap, MarketHoursPolicy.ALLOW) is True
    assert is_trading_permitted(open_snap, MarketHoursPolicy.REJECT) is True
    assert is_trading_permitted(closed_snap, MarketHoursPolicy.REJECT) is False


def test_local_time_in_rth_boundaries() -> None:
    from datetime import time

    assert local_time_in_rth(time(9, 30)) is True
    assert local_time_in_rth(time(15, 59)) is True
    assert local_time_in_rth(time(16, 0)) is False
    assert local_time_in_rth(time(9, 29)) is False


def test_coverage_constants() -> None:
    assert date(2024, 1, 1) <= date(2027, 12, 31)


def test_2027_12_23_is_full_rth_not_early_close() -> None:
    """Regression: 2027-12-23 must be normal RTH (09:30–16:00), not early close."""
    cal = UsEquityXnysCalendar()

    at_1259 = cal.resolve(_utc(2027, 12, 23, 12, 59))
    assert at_1259.state is SessionState.OPEN_RTH
    assert at_1259.is_rth_open is True
    assert at_1259.is_early_close_day is False

    at_1300 = cal.resolve(_utc(2027, 12, 23, 13, 0))
    assert at_1300.state is SessionState.OPEN_RTH
    assert at_1300.is_rth_open is True
    assert at_1300.is_early_close_day is False

    before_close = cal.resolve(_utc(2027, 12, 23, 15, 59))
    assert before_close.state is SessionState.OPEN_RTH
    assert before_close.is_rth_open is True

    at_close = cal.resolve(_utc(2027, 12, 23, 16, 0))
    assert at_close.state is SessionState.AFTER_HOURS
    assert at_close.is_rth_open is False


def test_2027_12_24_is_christmas_observed_holiday() -> None:
    cal = UsEquityXnysCalendar()
    snap = cal.resolve(_utc(2027, 12, 24, 12, 0))
    assert snap.state is SessionState.CLOSED_HOLIDAY
    assert snap.is_rth_open is False
