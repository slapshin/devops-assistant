"""Per-project report schedules: an analysis at a local time of day on chosen weekdays.

Occurrences are computed in the schedule's IANA time zone, so "08:00 Europe/Berlin" stays at
08:00 local time across DST changes. A local time skipped by a DST jump resolves to the
instant just after the gap; a repeated one resolves to its first occurrence.
"""

from datetime import UTC, date, datetime, time, timedelta
from enum import StrEnum
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, field_validator

from app.domain.common import Contract

LOCAL_TIME_PATTERN = r"^([01]\d|2[0-3]):[0-5]\d$"
DAYS_PER_WEEK = 7


class Weekday(StrEnum):
    MON = "mon"
    TUE = "tue"
    WED = "wed"
    THU = "thu"
    FRI = "fri"
    SAT = "sat"
    SUN = "sun"


WEEKDAYS = list(Weekday)
"""Indexed like ``date.weekday()`` (Monday is 0)."""


class ReportSchedule(Contract):
    """Run an analysis automatically at ``time`` (local to ``timezone``) on ``weekdays``.

    Each run analyses the 24 h ending at the scheduled instant.
    """

    time: str = Field(
        pattern=LOCAL_TIME_PATTERN,
        description="Local time of day, HH:MM (24 h).",
        examples=["08:00"],
    )
    timezone: str = Field(
        default="UTC", max_length=64, description="IANA time zone, e.g. Europe/Berlin."
    )
    weekdays: list[Weekday] = Field(
        default_factory=lambda: list(WEEKDAYS),
        min_length=1,
        max_length=DAYS_PER_WEEK,
        description="Days to run on; all days by default.",
    )

    @field_validator("timezone")
    @classmethod
    def _known_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError, ValueError:
            raise ValueError(
                "unknown time zone; expected an IANA name such as Europe/Berlin"
            ) from None
        return value

    @field_validator("weekdays")
    @classmethod
    def _ordered_weekdays(cls, value: list[Weekday]) -> list[Weekday]:
        return sorted(set(value), key=WEEKDAYS.index)

    def _at(self, day: date) -> datetime:
        hour, minute = map(int, self.time.split(":"))
        local = datetime.combine(day, time(hour, minute), tzinfo=ZoneInfo(self.timezone))
        # Round-trip through UTC so a local time inside a DST gap lands after the gap.
        return local.astimezone(UTC)

    def _days(self, moment: datetime, step: int) -> list[date]:
        """Scheduled local days from the day of ``moment``, one week and a day in ``step``."""
        today = moment.astimezone(ZoneInfo(self.timezone)).date()
        days = (today + timedelta(days=step * n) for n in range(DAYS_PER_WEEK + 2))
        return [d for d in days if WEEKDAYS[d.weekday()] in self.weekdays]

    def latest_at_or_before(self, moment: datetime) -> datetime:
        """The most recent scheduled instant not after ``moment`` (UTC)."""
        return next(at for d in self._days(moment, -1) if (at := self._at(d)) <= moment)

    def next_after(self, moment: datetime) -> datetime:
        """The first scheduled instant strictly after ``moment`` (UTC)."""
        return next(at for d in self._days(moment, 1) if (at := self._at(d)) > moment)
