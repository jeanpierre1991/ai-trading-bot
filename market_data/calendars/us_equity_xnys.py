"""XNYS US-equity session calendar with in-repo static tables (M11.3).

Coverage: 2024-01-01 .. 2027-12-31 inclusive (Decision F).
No third-party calendar dependency — stdlib ``zoneinfo`` only.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from market_data.session_calendar import (
    COVERAGE_END,
    COVERAGE_START,
    EXCHANGE_ID_XNYS,
    TZ_AMERICA_NEW_YORK,
    SessionSnapshot,
    SessionState,
    local_time_in_rth,
    normalize_utc,
)

_RTH_OPEN = time(9, 30)
_RTH_CLOSE = time(16, 0)
_EARLY_CLOSE = time(13, 0)

# Full-day XNYS holidays (NYSE Group published calendars).
_HOLIDAYS: frozenset[date] = frozenset(
    {
        # 2024
        date(2024, 1, 1),
        date(2024, 1, 15),
        date(2024, 2, 19),
        date(2024, 3, 29),
        date(2024, 5, 27),
        date(2024, 6, 19),
        date(2024, 7, 4),
        date(2024, 9, 2),
        date(2024, 11, 28),
        date(2024, 12, 25),
        # 2025
        date(2025, 1, 1),
        date(2025, 1, 20),
        date(2025, 2, 17),
        date(2025, 4, 18),
        date(2025, 5, 26),
        date(2025, 6, 19),
        date(2025, 7, 4),
        date(2025, 9, 1),
        date(2025, 11, 27),
        date(2025, 12, 25),
        # 2026
        date(2026, 1, 1),
        date(2026, 1, 19),
        date(2026, 2, 16),
        date(2026, 4, 3),
        date(2026, 5, 25),
        date(2026, 6, 19),
        date(2026, 7, 3),  # Independence Day observed
        date(2026, 9, 7),
        date(2026, 11, 26),
        date(2026, 12, 25),
        # 2027
        date(2027, 1, 1),
        date(2027, 1, 18),
        date(2027, 2, 15),
        date(2027, 3, 26),
        date(2027, 5, 31),
        date(2027, 6, 18),  # Juneteenth observed
        date(2027, 7, 5),  # Independence Day observed
        date(2027, 9, 6),
        date(2027, 11, 25),
        date(2027, 12, 24),  # Christmas Day observed
    }
)

# Early-close days → 13:00 America/New_York (NYSE Group).
_EARLY_CLOSE_DATES: frozenset[date] = frozenset(
    {
        date(2024, 7, 3),
        date(2024, 11, 29),
        date(2024, 12, 24),
        date(2025, 7, 3),
        date(2025, 11, 28),
        date(2025, 12, 24),
        date(2026, 11, 27),
        date(2026, 12, 24),
        date(2027, 11, 26),
        # 2027-12-23 is a normal full RTH day (NYSE early-close bulletins do not
        # list it; Christmas 2027 is a full holiday on 2027-12-24 observed).
    }
)


class UsEquityXnysCalendar:
    """XNYS regular-session calendar (Decision C)."""

    def __init__(
        self,
        *,
        exchange_id: str = EXCHANGE_ID_XNYS,
        tz_name: str = TZ_AMERICA_NEW_YORK,
        coverage_start: date = COVERAGE_START,
        coverage_end: date = COVERAGE_END,
    ) -> None:
        self._exchange_id = exchange_id
        self._tz_name = tz_name
        try:
            self._tz = ZoneInfo(tz_name)
        except Exception as exc:  # noqa: BLE001 - fail closed on bad tz
            raise ValueError(f"invalid market hours timezone {tz_name!r}: {exc}") from exc
        if coverage_end < coverage_start:
            raise ValueError("coverage_end must be >= coverage_start")
        self._coverage_start = coverage_start
        self._coverage_end = coverage_end

    @property
    def exchange_id(self) -> str:
        return self._exchange_id

    @property
    def tz_name(self) -> str:
        return self._tz_name

    def resolve(self, now_utc: datetime) -> SessionSnapshot:
        try:
            as_of = normalize_utc(now_utc)
        except ValueError as exc:
            return self._unavailable(now_utc if isinstance(now_utc, datetime) else datetime.now(timezone.utc), str(exc))

        local = as_of.astimezone(self._tz)
        local_day = local.date()

        if local_day < self._coverage_start or local_day > self._coverage_end:
            return self._unavailable(
                as_of,
                (
                    f"date {local_day.isoformat()} outside XNYS calendar coverage "
                    f"{self._coverage_start.isoformat()}..{self._coverage_end.isoformat()}"
                ),
            )

        last_close = self._last_completed_regular_close(as_of)

        if local_day in _HOLIDAYS:
            return SessionSnapshot(
                state=SessionState.CLOSED_HOLIDAY,
                exchange_id=self._exchange_id,
                tz_name=self._tz_name,
                as_of_utc=as_of,
                session_date=local_day,
                regular_open_utc=None,
                regular_close_utc=None,
                last_completed_regular_close_utc=last_close,
                is_rth_open=False,
                is_early_close_day=False,
                reason=f"XNYS holiday {local_day.isoformat()}",
            )

        if local.weekday() >= 5:  # Saturday=5, Sunday=6
            return SessionSnapshot(
                state=SessionState.CLOSED_WEEKEND,
                exchange_id=self._exchange_id,
                tz_name=self._tz_name,
                as_of_utc=as_of,
                session_date=local_day,
                regular_open_utc=None,
                regular_close_utc=None,
                last_completed_regular_close_utc=last_close,
                is_rth_open=False,
                is_early_close_day=False,
                reason=f"weekend {local_day.isoformat()}",
            )

        early = local_day in _EARLY_CLOSE_DATES
        close_t = _EARLY_CLOSE if early else _RTH_CLOSE
        open_utc = self._combine_local(local_day, _RTH_OPEN)
        close_utc = self._combine_local(local_day, close_t)
        local_t = local.timetz().replace(tzinfo=None)

        if local_time_in_rth(local_t, open_t=_RTH_OPEN, close_t=close_t):
            return SessionSnapshot(
                state=SessionState.OPEN_RTH,
                exchange_id=self._exchange_id,
                tz_name=self._tz_name,
                as_of_utc=as_of,
                session_date=local_day,
                regular_open_utc=open_utc,
                regular_close_utc=close_utc,
                last_completed_regular_close_utc=last_close,
                is_rth_open=True,
                is_early_close_day=early,
                reason=None,
            )

        if local_t < _RTH_OPEN:
            # Morning before open: treat as pre-market (not overnight).
            return SessionSnapshot(
                state=SessionState.PRE_MARKET,
                exchange_id=self._exchange_id,
                tz_name=self._tz_name,
                as_of_utc=as_of,
                session_date=local_day,
                regular_open_utc=open_utc,
                regular_close_utc=close_utc,
                last_completed_regular_close_utc=last_close,
                is_rth_open=False,
                is_early_close_day=early,
                reason=f"pre-market before {_RTH_OPEN.isoformat()}",
            )

        # After close on a trading day.
        return SessionSnapshot(
            state=SessionState.AFTER_HOURS,
            exchange_id=self._exchange_id,
            tz_name=self._tz_name,
            as_of_utc=as_of,
            session_date=local_day,
            regular_open_utc=open_utc,
            regular_close_utc=close_utc,
            last_completed_regular_close_utc=last_close,
            is_rth_open=False,
            is_early_close_day=early,
            reason=f"after regular close {close_t.isoformat()}",
        )

    def _last_completed_regular_close(self, as_of_utc: datetime) -> datetime | None:
        """Most recent regular-session close strictly before or equal? 

        Close is exclusive for RTH membership, so at exactly close_utc the
        session is already closed and that close is completed.
        Search backward from local date (including today if already closed).
        """
        local = as_of_utc.astimezone(self._tz)
        day = local.date()
        # Limit search within coverage + a few days of lookback padding inside coverage.
        for _ in range(400):
            if day < self._coverage_start:
                return None
            if day > self._coverage_end:
                day = day - timedelta(days=1)
                continue
            if self._is_trading_day(day):
                close_t = _EARLY_CLOSE if day in _EARLY_CLOSE_DATES else _RTH_CLOSE
                close_utc = self._combine_local(day, close_t)
                if as_of_utc >= close_utc:
                    return close_utc
            day = day - timedelta(days=1)
        return None

    def _is_trading_day(self, day: date) -> bool:
        if day.weekday() >= 5:
            return False
        if day in _HOLIDAYS:
            return False
        return True

    def _combine_local(self, day: date, local_t: time) -> datetime:
        return datetime(
            day.year,
            day.month,
            day.day,
            local_t.hour,
            local_t.minute,
            local_t.second,
            tzinfo=self._tz,
        ).astimezone(timezone.utc)

    def _unavailable(self, as_of_utc: datetime, reason: str) -> SessionSnapshot:
        try:
            as_of = normalize_utc(as_of_utc) if as_of_utc.tzinfo else as_of_utc.replace(tzinfo=timezone.utc)
        except Exception:  # noqa: BLE001
            as_of = datetime.now(timezone.utc)
        return SessionSnapshot(
            state=SessionState.CALENDAR_UNAVAILABLE,
            exchange_id=self._exchange_id,
            tz_name=self._tz_name,
            as_of_utc=as_of,
            session_date=None,
            regular_open_utc=None,
            regular_close_utc=None,
            last_completed_regular_close_utc=None,
            is_rth_open=False,
            is_early_close_day=False,
            reason=reason,
        )


def build_session_calendar(calendar_id: str, *, tz_name: str = TZ_AMERICA_NEW_YORK) -> UsEquityXnysCalendar:
    """Factory for supported calendar ids (fail-closed on unknown)."""
    key = str(calendar_id).strip().lower()
    if key in {"xnys", "us_equity", "us_equity_xnys"}:
        return UsEquityXnysCalendar(tz_name=tz_name)
    raise ValueError(f"unsupported market_hours_calendar: {calendar_id!r}")
