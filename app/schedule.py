"""Which schedule and which calendar applied, for a group, on a date.

This is the question step 6 and step 7 will ask constantly, and the reason the
answer is stored effective-dated: re-rendering last March has to use the
schedule and the calendar that were in force last March, not today's. Changing
a schedule today must not move a past period. If it does, the sheet HR already
signed stops matching the sheet the system prints.

The attendance day is decided here, not downstream. A shift that ends after
midnight says so on its row, so a punch at 04:35 on Tuesday belongs to Monday's
attendance day because Monday's shift window contains it — not because some
later code compared two times and guessed.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from sqlalchemy import select

from app.models import GroupSchedule, Holiday, HolidayAdjustment


@dataclass
class EffectiveHoliday:
    """What the calendar says about one date, after adjustments."""

    date: dt.date
    name: str
    scope_code: str | None
    closes: bool
    provisional: bool
    adjusted: bool
    source: str
    reason: str | None = None
    made_by: str | None = None


@dataclass
class DayContext:
    group_code: str
    date: dt.date
    schedule: GroupSchedule | None
    is_rest_day: bool
    holiday: EffectiveHoliday | None
    shift_start: dt.datetime | None
    shift_end: dt.datetime | None
    window_start: dt.datetime | None
    window_end: dt.datetime | None

    @property
    def closed(self) -> bool:
        """Rest day, or a holiday the company actually closes for."""
        return self.is_rest_day or bool(self.holiday and self.holiday.closes)

    @property
    def working_day(self) -> bool:
        return self.schedule is not None and not self.closed


@dataclass
class AttendanceDay:
    """Which day a punch belongs to. `within_window` false means the punch fell
    outside every scheduled window — a fact to carry forward, never a reason to
    drop it."""

    date: dt.date
    group_code: str
    schedule_id: int | None
    within_window: bool
    note: str


def schedule_for(session, group_code: str, on_date: dt.date) -> GroupSchedule | None:
    """The row in force on that date. Never the newest row."""
    return session.scalars(
        select(GroupSchedule)
        .where(
            GroupSchedule.group_code == group_code,
            GroupSchedule.effective_from <= on_date,
            (GroupSchedule.effective_to.is_(None))
            | (GroupSchedule.effective_to >= on_date),
        )
        .order_by(GroupSchedule.effective_from.desc())
        .limit(1)
    ).first()


def shift_window(schedule: GroupSchedule, day: dt.date) -> tuple[dt.datetime, dt.datetime]:
    """The scheduled shift for that attendance day, as two instants. The end
    lands on the next calendar day when the row says so."""
    start = dt.datetime.combine(day, schedule.start_time)
    end_day = day + dt.timedelta(days=1) if schedule.end_next_day else day
    return start, dt.datetime.combine(end_day, schedule.end_time)


def break_window(schedule: GroupSchedule, day: dt.date):
    if schedule.break_start is None or schedule.break_end is None:
        return None
    start_day = day + dt.timedelta(days=1) if schedule.break_start_next_day else day
    end_day = day + dt.timedelta(days=1) if schedule.break_end_next_day else day
    return (
        dt.datetime.combine(start_day, schedule.break_start),
        dt.datetime.combine(end_day, schedule.break_end),
    )


def catch_window(schedule: GroupSchedule, day: dt.date) -> tuple[dt.datetime, dt.datetime]:
    """The shift window widened by the row's margins — how early or late a
    punch can be and still belong to this attendance day (A30)."""
    start, end = shift_window(schedule, day)
    return (
        start - dt.timedelta(minutes=schedule.window_before_minutes),
        end + dt.timedelta(minutes=schedule.window_after_minutes),
    )


def is_rest_day(schedule: GroupSchedule, day: dt.date) -> bool:
    return day.isoweekday() in (schedule.rest_weekdays or [])


def effective_holiday(session, day: dt.date) -> EffectiveHoliday | None:
    """The uploaded holiday for that date with the latest adjustment applied.

    Adjustments live in their own table, so a re-upload of the year replaces the
    uploaded rows and leaves them standing.
    """
    uploaded = session.scalars(
        select(Holiday).where(Holiday.holiday_date == day)
    ).first()
    adjustment = session.scalars(
        select(HolidayAdjustment)
        .where(HolidayAdjustment.holiday_date == day)
        .order_by(HolidayAdjustment.made_at.desc(), HolidayAdjustment.id.desc())
        .limit(1)
    ).first()

    if adjustment is None:
        if uploaded is None:
            return None
        return EffectiveHoliday(
            date=day,
            name=uploaded.name,
            scope_code=uploaded.scope_code,
            closes=uploaded.closes,
            provisional=uploaded.provisional,
            adjusted=False,
            source="upload",
        )

    if adjustment.action == "remove":
        return None

    return EffectiveHoliday(
        date=day,
        name=adjustment.name or (uploaded.name if uploaded else ""),
        scope_code=adjustment.scope_code
        or (uploaded.scope_code if uploaded else None),
        closes=(
            adjustment.closes
            if adjustment.closes is not None
            else (uploaded.closes if uploaded else False)
        ),
        provisional=uploaded.provisional if uploaded else False,
        adjusted=True,
        source="upload + adjustment" if uploaded else "adjustment",
        reason=adjustment.reason,
        made_by=adjustment.made_by,
    )


def day_context(session, group_code: str, day: dt.date) -> DayContext:
    """For this group, on this date: what schedule and what calendar applied."""
    schedule = schedule_for(session, group_code, day)
    holiday = effective_holiday(session, day)
    if schedule is None:
        return DayContext(
            group_code=group_code,
            date=day,
            schedule=None,
            is_rest_day=False,
            holiday=holiday,
            shift_start=None,
            shift_end=None,
            window_start=None,
            window_end=None,
        )
    start, end = shift_window(schedule, day)
    window_start, window_end = catch_window(schedule, day)
    return DayContext(
        group_code=group_code,
        date=day,
        schedule=schedule,
        is_rest_day=is_rest_day(schedule, day),
        holiday=holiday,
        shift_start=start,
        shift_end=end,
        window_start=window_start,
        window_end=window_end,
    )


def attendance_day_for(session, group_code: str, punched_at: dt.datetime) -> AttendanceDay:
    """Which attendance day a punch belongs to.

    Decided by the shift, not by the clock: each candidate day's window is built
    from that day's schedule row, including whether the shift ends tomorrow. A
    night-shift punch at 04:35 on Tuesday falls inside Monday's window and is
    Monday's.
    """
    punch_date = punched_at.date()
    matches = []
    for offset in (-1, 0, 1):
        day = punch_date + dt.timedelta(days=offset)
        schedule = schedule_for(session, group_code, day)
        if schedule is None:
            continue
        window_start, window_end = catch_window(schedule, day)
        if window_start <= punched_at <= window_end:
            matches.append((day, schedule, window_start))

    if not matches:
        return AttendanceDay(
            date=punch_date,
            group_code=group_code,
            schedule_id=None,
            within_window=False,
            note="outside every scheduled window — kept on the punch's own date",
        )

    # More than one window can cover a punch if the margins are wide. The shift
    # that has already started is the one the employee is on.
    day, schedule, _ = max(matches, key=lambda m: m[2])
    note = "inside the shift window"
    if len(matches) > 1:
        note = (
            f"{len(matches)} windows covered it; took the shift that had already "
            "started"
        )
    return AttendanceDay(
        date=day,
        group_code=group_code,
        schedule_id=schedule.id,
        within_window=True,
        note=note,
    )


def set_schedule(session, group_code: str, effective_from: dt.date, **fields) -> GroupSchedule:
    """Add a schedule from a date, closing the one it supersedes the day before.

    A change is a new row. The old row keeps its dates, so a past period still
    renders with it.
    """
    later = session.scalars(
        select(GroupSchedule).where(
            GroupSchedule.group_code == group_code,
            GroupSchedule.effective_from >= effective_from,
        )
    ).all()
    if later:
        dates = ", ".join(str(row.effective_from) for row in later)
        raise ValueError(
            f"{group_code} already has a schedule starting on or after "
            f"{effective_from}: {dates}. Pick a later date, or remove that row."
        )

    current = session.scalars(
        select(GroupSchedule)
        .where(
            GroupSchedule.group_code == group_code,
            GroupSchedule.effective_from < effective_from,
            GroupSchedule.effective_to.is_(None),
        )
        .order_by(GroupSchedule.effective_from.desc())
        .limit(1)
    ).first()
    if current is not None:
        current.effective_to = effective_from - dt.timedelta(days=1)
        session.flush()

    schedule = GroupSchedule(
        group_code=group_code, effective_from=effective_from, **fields
    )
    session.add(schedule)
    session.flush()
    return schedule
